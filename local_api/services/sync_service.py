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

        # 1. Route to adapter
        adapter = self._route_adapter(sync_target)
        if adapter is None:
            return _error_result(
                sync_id=None,
                status="failed_permanent",
                code="adapter_not_found",
                message=f"No adapter configured for sync_target: {sync_target}",
            )

        # 2. Validate adapter config
        valid, err_msg = adapter.validate_config()
        if not valid:
            return _error_result(
                sync_id=None,
                status="failed_permanent",
                code="adapter_config_invalid",
                message=err_msg or "Adapter config validation failed",
            )

        # 3. Look up existing sync_state before fetching task data. This keeps
        # task-not-found errors tied to the existing record when a task was
        # deleted after state creation, while still avoiding new state creation
        # for never-seen tasks.
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
        if (
            sync_state is not None
            and sync_state.get("sync_status") == "synced"
            and sync_state.get("external_id")
        ):
            return {
                "success": True,
                "sync_id": sync_id,
                "sync_status": "skipped",
                "external_id": sync_state.get("external_id"),
                "error_code": "already_synced",
                "error_message": "Task is already synced to Apple Calendar",
            }

        # 6. Phase 22 eligibility guardrails run before new sync_state creation
        # and before processing existing pending/failed records.
        eligibility = eligible_for_calendar_sync(task_data, sync_target)
        if not eligibility.allowed:
            if sync_id is not None:
                _safe_transition(sync_id, "skipped", trigger="manual")
            return _error_result(
                sync_id=sync_id,
                status="skipped",
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
        try:
            push_result: AdapterResult = adapter.push(task_data, sync_state)
        except Exception as exc:
            logger.exception("Adapter push failed for %s: %s", sync_id, exc)
            _safe_transition(sync_id, "failed", trigger="engine")
            _log_failure(
                sync_id, task_id, sync_target,
                attempt=1, code="adapter_exception", message=str(exc),
            )
            return _error_result(
                sync_id=sync_id,
                status="failed",
                code="adapter_exception",
                message=str(exc),
            )

        # 7. Record result
        if push_result.success:
            payload_hash_before = sync_state.get("payload_hash")
            payload_hash_after = compute_task_payload_hash(task_data)
            external_id_before = sync_state.get("external_id")
            external_id_after = push_result.external_id

            transitioned, transition_error = _safe_transition(
                sync_id, "synced", trigger="engine"
            )
            if transitioned is None:
                _log_failure(
                    sync_id, task_id, sync_target,
                    attempt=push_result.sync_attempt,
                    code="invalid_transition",
                    message=str(transition_error),
                )
                current = get_sync_state_by_key(task_id, sync_target)
                return _error_result(
                    sync_id=sync_id,
                    status=current.get("sync_status") if current else "failed",
                    code="invalid_transition",
                    message=str(transition_error),
                    external_id=sync_state.get("external_id"),
                )
            # Update external_id and payload_hash on sync_state
            try:
                from .sync_state_service import update_sync_state

                update_sync_state(
                    sync_id,
                    external_id=external_id_after,
                    last_synced_at=_iso_now(),
                    payload_hash=payload_hash_after,
                )
            except Exception as exc:
                logger.warning(
                    "Could not update external_id for %s: %s", sync_id, exc,
                )
            _log_sync(
                sync_id, task_id, sync_target, push_result.sync_attempt,
                payload_hash_before=payload_hash_before,
                payload_hash_after=payload_hash_after,
                external_id_before=external_id_before,
                external_id_after=external_id_after,
            )
            return {
                "success": True,
                "sync_id": sync_id,
                "sync_status": "synced",
                "external_id": push_result.external_id,
                "error_code": None,
                "error_message": None,
            }
        else:
            permanent_errors = {"auth_failed", "adapter_config_invalid", "invalid_data"}
            target = "failed_permanent" if push_result.error_code in permanent_errors else "failed"
            _safe_transition(sync_id, target, trigger="engine")
            _log_failure(
                sync_id, task_id, sync_target,
                attempt=push_result.sync_attempt,
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


def _log_sync(
    sync_id: str,
    task_id: str,
    target: str,
    attempt: int,
    payload_hash_before: Optional[str] = None,
    payload_hash_after: Optional[str] = None,
    external_id_before: Optional[str] = None,
    external_id_after: Optional[str] = None,
) -> None:
    try:
        create_sync_log(
            sync_id=sync_id,
            local_task_id=task_id,
            sync_target=target,
            sync_attempt=attempt,
            sync_result="success",
            payload_hash_before=payload_hash_before,
            payload_hash_after=payload_hash_after,
            external_id_before=external_id_before,
            external_id_after=external_id_after,
            triggered_by="sync_service",
        )
    except Exception as exc:
        logger.warning("Could not write sync_log for %s: %s", sync_id, exc)


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
