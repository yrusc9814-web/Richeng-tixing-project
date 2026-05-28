"""Phase 6 sync engine scanner."""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from . import config
from .adapters import SyncAdapter
from .database import get_db
from .services.sync_log_service import create_sync_log
from .services.sync_state_service import get_sync_state, transition_sync_state

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    pending_picked: int = 0
    retry_triggered: int = 0
    stale_triggered: int = 0
    timeout_detected: int = 0
    completed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


class SyncEngine:
    def __init__(self, adapters: Optional[list] = None):
        self._running = False
        self._stop_event = None
        self._loop_thread = None
        self._batch_size = config.SYNC_BATCH_SIZE
        self._lock = threading.Lock()
        self._scan_count = 0
        self._last_scan_at = None
        self._adapters: list = adapters or []

    def scan_once(self) -> ScanResult:
        """Run one priority-ordered scan cycle."""
        self._batch_size = config.SYNC_BATCH_SIZE
        result = ScanResult()

        if self._handle_timeouts(result):
            return result
        if self._handle_failed(result):
            return result
        if self._handle_stale(result):
            return result
        pending_syncs = self._handle_pending(result)
        if config.ADAPTER_ENABLED and pending_syncs and self._adapters:
            self._adapter_push_cycle(pending_syncs)
        return result

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event = threading.Event()
            self._running = True
            self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
            self._loop_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            if self._stop_event is not None:
                self._stop_event.set()
        # Join outside lock to avoid deadlock
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=config.SYNC_SCAN_INTERVAL + 1)
        with self._lock:
            self._running = False
            self._loop_thread = None
            self._stop_event = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def daemon(self) -> bool:
        """Whether the engine runs a background loop (always True when active)."""
        return self._running

    @property
    def scan_count(self) -> int:
        return self._scan_count

    @property
    def last_scan_at(self):
        return self._last_scan_at

    def _run_loop(self) -> None:
        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                result = self.scan_once()
                self._scan_count += 1
                self._last_scan_at = _now().isoformat()
            except Exception as exc:
                logger.exception("SyncEngine scan_once failed: %s", exc)
            self._stop_event.wait(config.SYNC_SCAN_INTERVAL)
        self._running = False

    def _handle_timeouts(self, result: ScanResult) -> bool:
        conn = get_db()
        rows = conn.execute(
            """SELECT * FROM sync_state
               WHERE sync_status = 'in_progress'
               ORDER BY updated_at ASC
               LIMIT ?""",
            (self._batch_size,),
        ).fetchall()

        now = _now()
        processed = False
        for row in rows:
            try:
                # Use started_at for timeout calculation, fallback to updated_at
                started = row["started_at"]
                if started is None:
                    started = row["updated_at"]
                elapsed = (now - _parse_iso(started)).total_seconds()

                if elapsed <= config.SYNC_TIMEOUT_SECONDS:
                    continue

                current_attempt = self._max_attempt(row["sync_id"]) + 1
                target_status = "failed_permanent" if current_attempt >= 4 else "failed"
                transition_sync_state(row["sync_id"], target_status, trigger="engine")
                create_sync_log(
                    sync_id=row["sync_id"],
                    local_task_id=row["task_id"],
                    sync_target=row["sync_target"],
                    sync_attempt=current_attempt,
                    sync_result="failed",
                    error_code="timeout",
                    error_message="Sync attempt timed out",
                    triggered_by="sync_engine",
                )
                result.timeout_detected += 1
                result.failed += 1
                processed = True
            except Exception as exc:
                result.errors.append(f"{row['sync_id']}: {exc}")
                processed = True

        return processed

    def _handle_failed(self, result: ScanResult) -> bool:
        conn = get_db()
        rows = conn.execute(
            """SELECT * FROM sync_state
               WHERE sync_status = 'failed'
               ORDER BY updated_at ASC
               LIMIT ?""",
            (self._batch_size,),
        ).fetchall()

        processed = False
        for row in rows:
            try:
                max_attempt = self._max_attempt(row["sync_id"])
                if max_attempt >= 4:
                    transition_sync_state(
                        row["sync_id"],
                        "failed_permanent",
                        trigger="engine",
                    )
                    create_sync_log(
                        sync_id=row["sync_id"],
                        local_task_id=row["task_id"],
                        sync_target=row["sync_target"],
                        sync_attempt=max_attempt,
                        sync_result="failed",
                        error_code="retry_cap",
                        error_message="Retry cap reached",
                        triggered_by="sync_engine",
                    )
                    result.failed += 1
                    processed = True
                    continue

                latest_log = self._latest_log(row["sync_id"])
                if latest_log is None:
                    continue

                elapsed = (_now() - _parse_iso(latest_log["created_at"])).total_seconds()
                if elapsed < self._backoff_interval(max_attempt):
                    continue

                transition_sync_state(row["sync_id"], "in_progress", trigger="engine")
                result.retry_triggered += 1
                processed = True
            except Exception as exc:
                result.errors.append(f"{row['sync_id']}: {exc}")
                processed = True

        return processed

    def _handle_stale(self, result: ScanResult) -> bool:
        conn = get_db()
        rows = conn.execute(
            """SELECT * FROM sync_state
               WHERE sync_status = 'stale'
               ORDER BY updated_at ASC
               LIMIT ?""",
            (self._batch_size,),
        ).fetchall()

        processed = False
        for row in rows:
            try:
                transition_sync_state(row["sync_id"], "pending", trigger="engine")
                result.stale_triggered += 1
                processed = True
            except Exception as exc:
                result.errors.append(f"{row['sync_id']}: {exc}")
                processed = True

        return processed

    def _handle_pending(self, result: ScanResult) -> list[dict]:
        conn = get_db()
        rows = conn.execute(
            """SELECT * FROM sync_state
               WHERE sync_status = 'pending'
               ORDER BY updated_at ASC
               LIMIT ?""",
            (self._batch_size,),
        ).fetchall()

        pending_syncs: list[dict] = []
        for row in rows:
            try:
                transition_sync_state(row["sync_id"], "in_progress", trigger="engine")
                result.pending_picked += 1
                pending_syncs.append(dict(row))
            except Exception as exc:
                result.errors.append(f"{row['sync_id']}: {exc}")

        return pending_syncs

    def _route_adapter(self, sync_target: str) -> Optional[SyncAdapter]:
        """Find the first adapter that matches the sync_target.

        Rules:
        - 'apple_calendar' or 'apple_reminder' → MockAppleAdapter (target_name='apple_calendar')
        - 'weather' → MockWeatherAdapter
        """
        for adapter in self._adapters:
            if sync_target.startswith("apple_") and adapter.target_name == "apple_calendar":
                return adapter
            if adapter.target_name == sync_target:
                return adapter
        return None

    def _adapter_push_cycle(self, pending_syncs: list[dict]) -> None:
        """Execute adapter push for pending sync records.

        State terminal guarantees:
        - push success           → synced
        - retryable error        → failed
        - permanent error        → failed_permanent
        - no adapter             → failed_permanent + log
        - no task data           → failed_permanent + log
        - any exception          → caught, logged, failed

        Every record gets a sync_log entry and a terminal state.
        """
        for sync in pending_syncs:
            sync_id = sync["sync_id"]
            sync_target = sync.get("sync_target", "unknown")
            task_id = sync.get("task_id")

            try:
                # 1. Route to adapter
                adapter = self._route_adapter(sync_target)

                if adapter is None:
                    transition_sync_state(sync_id, "failed_permanent", trigger="engine")
                    create_sync_log(
                        sync_id=sync_id,
                        local_task_id=task_id,
                        sync_target=sync_target,
                        sync_attempt=self._max_attempt(sync_id) + 1,
                        sync_result="failed",
                        error_code="adapter_not_found",
                        error_message=f"No adapter configured for sync_target: {sync_target}",
                        triggered_by="sync_engine",
                    )
                    continue

                # 2. Validate config
                valid, err_msg = adapter.validate_config()
                if not valid:
                    transition_sync_state(sync_id, "failed_permanent", trigger="engine")
                    create_sync_log(
                        sync_id=sync_id,
                        local_task_id=task_id,
                        sync_target=sync_target,
                        sync_attempt=self._max_attempt(sync_id) + 1,
                        sync_result="failed",
                        error_code="adapter_config_invalid",
                        error_message=err_msg or "Adapter config validation failed",
                        triggered_by="sync_engine",
                    )
                    continue

                # 3. Fetch task data
                conn = get_db()
                task_row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if task_row is None:
                    transition_sync_state(sync_id, "failed_permanent", trigger="engine")
                    create_sync_log(
                        sync_id=sync_id,
                        local_task_id=task_id,
                        sync_target=sync_target,
                        sync_attempt=self._max_attempt(sync_id) + 1,
                        sync_result="failed",
                        error_code="task_not_found",
                        error_message=f"Task not found: {task_id}",
                        triggered_by="sync_engine",
                    )
                    continue

                task_data = dict(task_row)

                # 4. Execute push
                attempt = self._max_attempt(sync_id) + 1
                push_result = adapter.push(task_data, sync)

                if push_result.success:
                    transition_sync_state(sync_id, "synced", trigger="engine")
                    create_sync_log(
                        sync_id=sync_id,
                        local_task_id=task_id,
                        sync_target=sync_target,
                        sync_attempt=attempt,
                        sync_result="success",
                        triggered_by="sync_engine",
                    )
                else:
                    # Determine if the error is permanent or retryable
                    permanent_errors = {"auth_failed", "adapter_config_invalid", "invalid_data"}
                    if push_result.error_code in permanent_errors:
                        target_status = "failed_permanent"
                    else:
                        target_status = "failed"

                    transition_sync_state(sync_id, target_status, trigger="engine")
                    create_sync_log(
                        sync_id=sync_id,
                        local_task_id=task_id,
                        sync_target=sync_target,
                        sync_attempt=attempt,
                        sync_result="failed",
                        error_code=push_result.error_code or "push_failed",
                        error_message=push_result.error_message or "Push failed",
                        triggered_by="sync_engine",
                    )

            except Exception as exc:
                logger.exception("Adapter push cycle error for %s: %s", sync_id, exc)
                try:
                    # Check current state — don't roll back a successful transition
                    current = get_sync_state(sync_id)
                    if current and current["sync_status"] in ("synced", "failed_permanent"):
                        # Already at terminal state — don't flip back
                        logger.error(
                            "Sync %s already at '%s' but subsequent op failed: %s",
                            sync_id, current["sync_status"], exc,
                        )
                    else:
                        transition_sync_state(sync_id, "failed", trigger="engine")
                    create_sync_log(
                        sync_id=sync_id,
                        local_task_id=task_id,
                        sync_target=sync_target,
                        sync_attempt=self._max_attempt(sync_id) + 1,
                        sync_result="failed",
                        error_code="adapter_exception",
                        error_message=str(exc),
                        triggered_by="sync_engine",
                    )
                except Exception:
                    logger.exception("Failed to record adapter exception for %s", sync_id)

    def _max_attempt(self, sync_id: str) -> int:
        conn = get_db()
        row = conn.execute(
            "SELECT COALESCE(MAX(sync_attempt), 0) AS max_attempt FROM sync_logs WHERE sync_id = ?",
            (sync_id,),
        ).fetchone()
        return int(row["max_attempt"])

    def _latest_log(self, sync_id: str):
        conn = get_db()
        return conn.execute(
            """SELECT * FROM sync_logs
               WHERE sync_id = ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (sync_id,),
        ).fetchone()

    def _backoff_interval(self, attempt: int) -> float:
        interval = config.SYNC_RETRY_BASE * (config.SYNC_RETRY_FACTOR ** max(attempt - 1, 0))
        interval = min(interval, config.SYNC_RETRY_MAX_GAP)
        if config.SYNC_JITTER_ENABLED:
            interval *= random.uniform(0.8, 1.2)
        return interval
