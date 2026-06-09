"""Phase 8B — Sync service bridging adapters to service layer.

Provides SyncService, the primary entry point for external callers
(API routes, cron, tests) to execute adapter-based sync operations.
Coordinates sync_state lifecycle, adapter routing, push execution,
and result recording (sync_state transition + sync_log).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ..adapters import AdapterResult, SyncAdapter
from ..config import ALLOWED_SYNC_TARGETS
from ..database import get_db
from ..sync_client.payload import compute_task_payload_hash
from .sync_log_service import create_sync_log
from .sync_eligibility import eligible_for_calendar_sync
from .sync_state_service import (
    create_sync_state,
    get_sync_state_by_key,
    transition_sync_state,
)

logger = logging.getLogger(__name__)


class SyncService:
    """Service layer for executing adapter-based sync operations.

    Bridges external callers (API routes, cron, tests) with the Phase 8A
    adapter layer. Coordinates:
      - Creating or finding sync_state records
      - Routing to the correct adapter
      - Executing push via adapter
      - Recording results (sync_state transition + sync_log)
    """

    def __init__(self, adapters: Optional[list[SyncAdapter]] = None):
        self._adapters: list[SyncAdapter] = adapters or []

    def add_adapter(self, adapter: SyncAdapter) -> None:
        """Register an adapter."""
        self._adapters.append(adapter)

    # ── Internal routing ──────────────────────────────────────────────

    def _route_adapter(self, sync_target: str) -> Optional[SyncAdapter]:
        """Find the first adapter that matches sync_target.

        Routing rules (mirror of SyncEngine._route_adapter):
        - exact match → adapter with matching target_name
        - apple_reminder must not fall through to the Calendar adapter
        """
        for adapter in self._adapters:
            if adapter.target_name == sync_target:
                return adapter
        return None

    # ── Primary entry point ───────────────────────────────────────────

    def run_task_sync(self, task_id: str, sync_target: str) -> dict:
        """Execute a full sync cycle for a task + target.

        Returns a result dict with:
          success         - bool
          sync_id         - str | None
          sync_status     - str
          external_id     - str | None
          error_code      - str | None
          error_message   - str | None

        This is the primary entry point for external callers.
        """
        # 0. Validate sync_target
        if sync_target not in ALLOWED_SYNC_TARGETS:
            return _error_result(
                sync_id=None,
                status="failed_permanent",
                code="invalid_target",
                message=f"Invalid sync_target: {sync_target}",
            )

        if sync_target != "apple_calendar":
            return _error_result(
                sync_id=None,
                status="skipped",
                code="unsupported_target",
                message="Only apple_calendar sync is allowed",
            )

        # 1. Look up existing sync_state before adapter routing. This keeps
        # adapter-not-found and adapter-config-invalid errors tied to the
        # existing record when state exists, while still avoiding new state
        # creation for never-seen tasks.
        try:
            existing = get_sync_state_by_key(task_id, sync_target)
            if existing:
                sync_state = existing
                sync_id = sync_state["sync_id"]
            else:
                sync_state = None
                sync_id = None
        except ValueError as e:
            return _error_result(
                sync_id=None,
                status="failed_permanent",
                code="state_lookup_failed",
                message=str(e),
            )

        # 2. Route to adapter
        adapter = self._route_adapter(sync_target)
        if adapter is None:
            if sync_id is not None:
                # Existing sync_state: transition + log
                transitioned = _transition_to_failed_permanent(sync_id)
                if transitioned is not None:
                    result_status = "failed_permanent"
                else:
                    # Illegal transition — preserve real DB state
                    current = get_sync_state_by_key(task_id, sync_target)
                    result_status = current.get("sync_status") if current else "failed_permanent"
                _log_failure(
                    sync_id, task_id, sync_target,
                    attempt=self._next_sync_attempt(sync_id),
                    code="adapter_not_found",
                    message=f"No adapter configured for sync_target: {sync_target}",
                )
                return _error_result(
                    sync_id=sync_id,
                    status=result_status,
                    code="adapter_not_found",
                    message=f"No adapter configured for sync_target: {sync_target}",
                )
            else:
                # No existing sync_state: preserve current failure return semantics
                return _error_result(
                    sync_id=None,
                    status="failed_permanent",
                    code="adapter_not_found",
                    message=f"No adapter configured for sync_target: {sync_target}",
                )

        # 3. Validate adapter config
        valid, err_msg = adapter.validate_config()
        if not valid:
            if sync_id is not None:
                # Existing sync_state: transition + log
                transitioned = _transition_to_failed_permanent(sync_id)
                if transitioned is not None:
                    result_status = "failed_permanent"
                else:
                    # Illegal transition — preserve real DB state
                    current = get_sync_state_by_key(task_id, sync_target)
                    result_status = current.get("sync_status") if current else "failed_permanent"
                _log_failure(
                    sync_id, task_id, sync_target,
                    attempt=self._next_sync_attempt(sync_id),
                    code="adapter_config_invalid",
                    message=err_msg or "Adapter config validation failed",
                )
                return _error_result(
                    sync_id=sync_id,
                    status=result_status,
                    code="adapter_config_invalid",
                    message=err_msg or "Adapter config validation failed",
                )
            else:
                # No existing sync_state: preserve current failure return semantics
                return _error_result(
                    sync_id=None,
                    status="failed_permanent",
                    code="adapter_config_invalid",
                    message=err_msg or "Adapter config validation failed",
                )

        # 4. Fetch task data before creating sync_state; eligibility failures
        # must not enqueue new records.
        conn = get_db()
        task_row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task_row is None:
            return _error_result(
                sync_id=sync_id,
                status="failed_permanent",
                code="task_not_found",
                message=f"Task not found: {task_id}",
            )
        task_data = dict(task_row)

        # 5. Duplicate create guard: already synced records with an external_id
        # are idempotently skipped instead of creating a second Calendar event.
        # Phase 32: detect drift via payload_hash mismatch — only skip when
        # the stored hash matches the current task hash.  Mismatch or missing
        # stored hash → fall through to resync.
        if (
            sync_state is not None
            and sync_state.get("sync_status") == "synced"
            and sync_state.get("external_id")
        ):
            stored_hash = sync_state.get("payload_hash")
            current_hash = compute_task_payload_hash(task_data)
            if stored_hash and current_hash and stored_hash == current_hash:
                return {
                    "success": True,
                    "sync_id": sync_id,
                    "sync_status": "skipped",
                    "external_id": sync_state.get("external_id"),
                    "error_code": "already_synced",
                    "error_message": "Task is already synced to Apple Calendar",
                }
            # Phase 32: hash mismatch or missing stored hash → drift detected.
            # Transition to stale so the stale→pending→in_progress pipeline
            # handles this record as an update instead of a fresh create.
            stale_transitioned, stale_err = _safe_transition(
                sync_id, "stale", trigger="trigger"
            )
            if stale_transitioned is None:
                return _error_result(
                    sync_id=sync_id,
                    status=sync_state.get("sync_status", "synced"),
                    code="invalid_transition",
                    message=f"synced→stale drift transition failed: {stale_err}",
                    external_id=sync_state.get("external_id"),
                )
            # Re-read so downstream code sees the updated status
            sync_state = get_sync_state_by_key(task_id, sync_target)
            if sync_state is None:
                return _error_result(
                    sync_id=sync_id,
                    status="failed_permanent",
                    code="state_lookup_failed",
                    message="sync_state disappeared after drift→stale transition",
                )

        # 6. Phase 22 eligibility guardrails run before new sync_state creation
        # and before processing existing pending/failed records.
        eligibility = eligible_for_calendar_sync(task_data, sync_target)
        if not eligibility.allowed:
            if sync_id is not None:
                transitioned, _ = _safe_transition(
                    sync_id, "skipped", trigger="manual"
                )
                if transitioned is not None:
                    result_status = "skipped"
                else:
                    # Transition to "skipped" is illegal from stale/failed
                    # states. Keep the existing status to avoid returning
                    # "skipped" while the DB still shows stale/failed.
                    result_status = sync_state.get("sync_status", "skipped")
                _log_failure(
                    sync_id, task_id, sync_target,
                    attempt=self._next_sync_attempt(sync_id),
                    code=eligibility.reason,
                    message=f"Calendar sync eligibility failed: {eligibility.reason}",
                )
            else:
                result_status = "skipped"
            return _error_result(
                sync_id=sync_id,
                status=result_status,
                code=eligibility.reason,
                message=f"Calendar sync eligibility failed: {eligibility.reason}",
                external_id=sync_state.get("external_id") if sync_state else None,
            )

        if sync_state is None:
            try:
                sync_state = create_sync_state(
                    task_id=task_id, sync_target=sync_target
                )
                sync_id = sync_state["sync_id"]
            except ValueError as e:
                return _error_result(
                    sync_id=None,
                    status="failed_permanent",
                    code="state_create_failed",
                    message=str(e),
                )

        # 6.5 Phase 22: stale records require pre-transition to pending
        # before the normal pending → in_progress transition.
        # stale → in_progress is illegal; the legal chain is
        # stale → pending → in_progress → synced/failed.
        if sync_state.get("sync_status") == "stale":
            pre_transitioned, pre_error = _safe_transition(
                sync_id, "pending", trigger="engine"
            )
            if pre_transitioned is None:
                return _error_result(
                    sync_id=sync_id,
                    status=sync_state.get("sync_status", "stale"),
                    code="invalid_transition",
                    message=f"stale→pending pre-transition failed: {pre_error}",
                    external_id=sync_state.get("external_id"),
                )
            # Re-read after pre-transition so adapter receives current state
            sync_state = get_sync_state_by_key(task_id, sync_target)
            if sync_state is None:
                return _error_result(
                    sync_id=sync_id,
                    status="failed_permanent",
                    code="state_lookup_failed",
                    message="sync_state disappeared after stale→pending transition",
                )

        # 7. Transition to in_progress
        transitioned, transition_error = _safe_transition(
            sync_id, "in_progress", trigger="engine"
        )
        if transitioned is None:
            return _error_result(
                sync_id=sync_id,
                status=sync_state.get("sync_status", "skipped"),
                code="invalid_transition",
                message=str(transition_error),
                external_id=sync_state.get("external_id"),
            )

        # 8. Execute push
        attempt = self._next_sync_attempt(sync_id)
        try:
            push_result: AdapterResult = adapter.push(task_data, sync_state)
        except Exception as exc:
            logger.exception("Adapter push failed for %s: %s", sync_id, exc)
            return _finalization_failure_result(
                sync_id=sync_id,
                task_id=task_id,
                sync_target=sync_target,
                attempt=attempt,
                target_status="failed",
                original_code="adapter_exception",
                original_message=str(exc),
                finalization_code="failure_finalize_failed",
                finalization_message_prefix="failed transition failed",
                last_confirmed_status="in_progress",
                external_id=sync_state.get("external_id"),
            )

        # 7. Record result
        if push_result.success:
            payload_hash_before = sync_state.get("payload_hash")
            payload_hash_after = compute_task_payload_hash(task_data)
            external_id_before = sync_state.get("external_id")
            external_id_after = push_result.external_id

            # Phase 30: Calendar success must require durable external_id.
            # Preserve existing external_id when adapter returns None/empty
            # (update/stale path), and fail permanently if no final id is
            # available — a synced Calendar record without external_id can
            # bypass update semantics and create duplicate Apple Calendar events.
            if not external_id_after and external_id_before:
                external_id_after = external_id_before

            if sync_target == "apple_calendar" and not external_id_after:
                return _finalization_failure_result(
                    sync_id=sync_id,
                    task_id=task_id,
                    sync_target=sync_target,
                    attempt=attempt,
                    target_status="failed_permanent",
                    original_code="missing_external_id",
                    original_message="Calendar push succeeded but returned no external_id",
                    finalization_code="failure_finalize_failed",
                    finalization_message_prefix="failed_permanent transition failed",
                    last_confirmed_status="in_progress",
                    external_id=sync_state.get("external_id"),
                    transition_func=_transition_to_failed_permanent,
                )

            # Phase 23: Write success metadata while state is still
            # in_progress.  Only transition to synced after the metadata
            # write succeeds, so a DB failure cannot leave a synced record
            # without refreshed payload_hash / external_id / last_synced_at.
            from .sync_state_service import update_sync_state

            try:
                update_sync_state(
                    sync_id,
                    external_id=external_id_after,
                    last_synced_at=_iso_now(),
                    payload_hash=payload_hash_after,
                )
            except Exception as metadata_exc:
                logger.exception(
                    "Metadata write failed for %s: %s", sync_id, metadata_exc
                )
                return _finalization_failure_result(
                    sync_id=sync_id,
                    task_id=task_id,
                    sync_target=sync_target,
                    attempt=attempt,
                    target_status="failed",
                    original_code="metadata_write_failed",
                    original_message=str(metadata_exc),
                    finalization_code="failure_finalize_failed",
                    finalization_message_prefix="failed transition failed",
                    last_confirmed_status="in_progress",
                    external_id=external_id_after,
                )

            # Phase 31: Create success log BEFORE synced transition so a
            # success log write failure cannot leave sync_state=synced
            # without a matching success log.  Treat metadata update +
            # success log recording + synced transition as one required
            # local finalization step.
            try:
                create_sync_log(
                    sync_id=sync_id,
                    local_task_id=task_id,
                    sync_target=sync_target,
                    sync_attempt=attempt,
                    sync_result="success",
                    payload_hash_before=payload_hash_before,
                    payload_hash_after=payload_hash_after,
                    external_id_before=external_id_before,
                    external_id_after=external_id_after,
                    triggered_by="sync_service",
                )
            except Exception as log_exc:
                logger.exception(
                    "Success log finalization failed for %s: %s",
                    sync_id,
                    log_exc,
                )
                return _finalization_failure_result(
                    sync_id=sync_id,
                    task_id=task_id,
                    sync_target=sync_target,
                    attempt=attempt,
                    target_status="failed",
                    original_code="success_finalize_failed",
                    original_message=str(log_exc),
                    finalization_code="success_finalize_failed",
                    finalization_message_prefix="failed transition failed",
                    last_confirmed_status="in_progress",
                    external_id=external_id_after,
                )

            transitioned, transition_error = _safe_transition(
                sync_id, "synced", trigger="engine"
            )
            if transitioned is None:
                # Phase 31: synced transition failed after success log
                # was committed.  Delete the orphaned success log so we
                # don't leave a success log without synced state.
                cleanup_ok = True
                try:
                    _delete_success_log(sync_id, attempt)
                except Exception:
                    logger.exception(
                        "Failed to clean up orphaned success log "
                        "for %s attempt %d",
                        sync_id,
                        attempt,
                    )
                    cleanup_ok = False

                if not cleanup_ok:
                    # Phase 31: orphan success log cleanup itself failed.
                    # State is inconsistent — do NOT claim consistency
                    # was restored.
                    code = "orphan_success_log_cleanup_failed"
                    msg = (
                        "synced transition failed and orphan success log "
                        "cleanup also failed: " + str(transition_error)
                    )
                else:
                    code = "success_finalize_failed"
                    msg = (
                        "synced transition failed after success log: "
                        + str(transition_error)
                    )

                _log_failure(
                    sync_id,
                    task_id,
                    sync_target,
                    attempt=attempt,
                    code=code,
                    message=msg,
                )
                current = get_sync_state_by_key(task_id, sync_target)
                return _error_result(
                    sync_id=sync_id,
                    status=current.get("sync_status") if current else "failed",
                    code=code,
                    message=msg,
                    external_id=sync_state.get("external_id"),
                )
            return {
                "success": True,
                "sync_id": sync_id,
                "sync_status": "synced",
                "external_id": external_id_after,
                "error_code": None,
                "error_message": None,
            }
        else:
            permanent_errors = {"auth_failed", "adapter_config_invalid", "invalid_data"}
            target = "failed_permanent" if push_result.error_code in permanent_errors else "failed"
            transitioned, transition_error = _safe_transition(
                sync_id, target, trigger="engine"
            )
            if transitioned is not None:
                _log_failure(
                    sync_id, task_id, sync_target,
                    attempt=attempt,
                    code=push_result.error_code or "push_failed",
                    message=push_result.error_message or "Push failed",
                )
                return _error_result(
                    sync_id=sync_id,
                    status=target,
                    code=push_result.error_code,
                    message=push_result.error_message,
                    external_id=push_result.external_id,
                )
            # Phase 33: transition to failure state failed — do NOT
            # pretend the target state was reached. Read the real
            # persisted status and align both result and sync_log.
            current = get_sync_state_by_key(task_id, sync_target)
            if current is None:
                return _error_result(
                    sync_id=sync_id,
                    status="in_progress",
                    code="failure_finalize_failed",
                    message=(
                        f"{target} transition failed and sync_state lookup failed: "
                        f"{transition_error}"
                    ),
                    external_id=sync_state.get("external_id"),
                )
            real_status = current.get("sync_status") or "failed_permanent"
            _log_failure(
                sync_id, task_id, sync_target,
                attempt=attempt,
                code="failure_finalize_failed",
                message=f"{target} transition failed: {transition_error}",
            )
            return _error_result(
                sync_id=sync_id,
                status=real_status,
                code="failure_finalize_failed",
                message=f"{target} transition failed: {transition_error}",
                external_id=sync_state.get("external_id"),
            )

    def _next_sync_attempt(self, sync_id: str) -> int:
        """Return the next monotonic sync_attempt for an existing sync_state."""
        conn = get_db()
        row = conn.execute(
            """SELECT COALESCE(MAX(sync_attempt), 0) AS max_attempt
               FROM sync_logs
               WHERE sync_id = ?""",
            (sync_id,),
        ).fetchone()
        return int(row["max_attempt"]) + 1


# ── Internal helpers ──────────────────────────────────────────────────


def _error_result(
    sync_id: Optional[str] = None,
    status: str = "failed_permanent",
    code: Optional[str] = None,
    message: Optional[str] = None,
    external_id: Optional[str] = None,
) -> dict:
    return {
        "success": False,
        "sync_id": sync_id,
        "sync_status": status,
        "external_id": external_id,
        "error_code": code,
        "error_message": message,
    }


def _safe_transition(
    sync_id: str,
    to_status: str,
    trigger: str = "service",
) -> tuple[Optional[dict], Optional[Exception]]:
    """Transition sync_state and return errors to callers that must stop."""
    try:
        return transition_sync_state(sync_id, to_status, trigger=trigger), None
    except Exception as exc:
        logger.warning(
            "Could not transition %s to %s: %s", sync_id, to_status, exc,
        )
        return None, exc


def _finalization_failure_result(
    *,
    sync_id: str,
    task_id: str,
    sync_target: str,
    attempt: int,
    target_status: str,
    original_code: str,
    original_message: str,
    finalization_code: str,
    finalization_message_prefix: str,
    last_confirmed_status: str,
    external_id: Optional[str] = None,
    transition_func=None,
) -> dict:
    """Finalize a failed local path without claiming an unpersisted status."""
    if transition_func is not None:
        transitioned = transition_func(sync_id)
        transition_error = None if transitioned is not None else RuntimeError(
            f"{target_status} transition failed"
        )
    else:
        transitioned, transition_error = _safe_transition(
            sync_id, target_status, trigger="engine"
        )

    if transitioned is not None:
        _log_failure(
            sync_id, task_id, sync_target,
            attempt=attempt,
            code=original_code,
            message=original_message,
        )
        return _error_result(
            sync_id=sync_id,
            status=target_status,
            code=original_code,
            message=original_message,
            external_id=external_id,
        )

    current = get_sync_state_by_key(task_id, sync_target)
    real_status = (
        current.get("sync_status")
        if current is not None
        else last_confirmed_status
    )
    msg = f"{finalization_message_prefix}: {transition_error}"
    if current is None:
        msg = f"{finalization_message_prefix} and sync_state lookup failed: {transition_error}"
    _log_failure(
        sync_id, task_id, sync_target,
        attempt=attempt,
        code=finalization_code,
        message=msg,
    )
    return _error_result(
        sync_id=sync_id,
        status=real_status,
        code=finalization_code,
        message=msg,
        external_id=external_id,
    )


def _transition_to_failed_permanent(sync_id: str) -> Optional[dict]:
    """Transition to failed_permanent via the shortest legal engine path.

    Direct pending→failed_permanent is illegal under the engine trigger,
    so this helper walks the legal multi-step path:
      - in_progress / failed  →  failed_permanent  (direct, one step)
      - pending  →  in_progress  →  failed_permanent
      - stale    →  pending  →  in_progress  →  failed_permanent

    Returns the final state dict on success, or None if unreachable.
    """
    # Path 1: direct (works for in_progress, failed, or idempotent)
    result, _ = _safe_transition(sync_id, "failed_permanent", trigger="engine")
    if result is not None:
        return result

    # Path 2: pending → in_progress → failed_permanent
    result, _ = _safe_transition(sync_id, "in_progress", trigger="engine")
    if result is not None:
        result2, _ = _safe_transition(sync_id, "failed_permanent", trigger="engine")
        return result2

    # Path 3: stale → pending → in_progress → failed_permanent
    result, _ = _safe_transition(sync_id, "pending", trigger="engine")
    if result is not None:
        result2, _ = _safe_transition(sync_id, "in_progress", trigger="engine")
        if result2 is not None:
            result3, _ = _safe_transition(sync_id, "failed_permanent", trigger="engine")
            return result3

    return None


def _delete_success_log(sync_id: str, attempt: int) -> None:
    """Delete an orphaned success log entry after a failed synced transition.

    Extracted as a standalone helper so tests can monkeypatch it without
    touching sqlite3.Connection.execute (which is read-only in C).
    """
    conn = get_db()
    conn.execute(
        "DELETE FROM sync_logs WHERE sync_id = ? "
        "AND sync_attempt = ? AND sync_result = 'success'",
        (sync_id, attempt),
    )
    conn.commit()


def _log_failure(
    sync_id: str,
    task_id: str,
    target: str,
    attempt: int,
    code: str,
    message: str,
) -> None:
    try:
        create_sync_log(
            sync_id=sync_id,
            local_task_id=task_id,
            sync_target=target,
            sync_attempt=attempt,
            sync_result="failed",
            error_code=code,
            error_message=message,
            triggered_by="sync_service",
        )
    except Exception as exc:
        logger.warning("Could not write failure log for %s: %s", sync_id, exc)


def _iso_now() -> str:
    """Return current time as ISO-8601 string with Asia/Shanghai timezone (+08:00)."""
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
