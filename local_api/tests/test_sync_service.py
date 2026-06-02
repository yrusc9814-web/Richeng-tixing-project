"""Phase 8B — Tests for SyncService adapter bridge.

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_service.py -v
"""

from __future__ import annotations

from typing import Optional

import pytest

from local_api import config
from local_api.adapters import AdapterResult, SyncAdapter
from local_api.database import get_db, init_db, reset_db
from local_api.services.sync_service import SyncService
from local_api.services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    get_sync_state_by_key,
    transition_sync_state,
    update_sync_state,
)
from local_api.services.sync_log_service import create_sync_log, list_sync_logs


# ── Mock adapters ─────────────────────────────────────────────────────


class MockSuccessAdapter(SyncAdapter):
    """Adapter that always succeeds and returns a deterministic external_id."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=True,
            external_id="ext_abc123",
            sync_result="success",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


class MockFailAdapter(SyncAdapter):
    """Adapter that fails with a retryable error (network)."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=False,
            error_code="network",
            error_message="Connection refused",
            sync_result="failed",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


class MockPermanentFailAdapter(SyncAdapter):
    """Adapter that fails with a permanent error (auth_failed)."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=False,
            error_code="auth_failed",
            error_message="Invalid credentials",
            sync_result="failed",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


class CountingSuccessAdapter(MockSuccessAdapter):
    """Success adapter that counts push calls."""

    def __init__(self):
        self.push_count = 0

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        self.push_count += 1
        return super().push(task_data, sync_state)


class MockAdapterConfigInvalid(SyncAdapter):
    """Adapter that fails config validation."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (False, "Missing API key")

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        raise AssertionError("push should not be called when config is invalid")

    def pull(self, external_id: str) -> Optional[dict]:
        return None


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


def _insert_task(
    task_id: str,
    *,
    sync_enabled: int = 0,
    sync_targets: str = '["apple_calendar"]',
    start_time: str | None = "2026-06-01T10:00:00+08:00",
    due_time: str | None = "2026-06-01T10:30:00+08:00",
    status: str = "pending",
) -> None:
    """Insert a minimal task row."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    conn = get_db()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, start_time, due_time,
            created_channel, created_at, updated_at, sync_enabled
        ) VALUES (?, ?, ?, 'P2', ?, ?, 'api_test', ?, ?, ?)""",
        (task_id, f"Task {task_id}", status, start_time, due_time, now, now, sync_enabled),
    )
    try:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN sync_targets TEXT NOT NULL DEFAULT '[\"apple_calendar\"]'"
        )
    except Exception:
        pass
    conn.execute(
        "UPDATE tasks SET sync_targets = ? WHERE task_id = ?",
        (sync_targets, task_id),
    )
    conn.commit()


def _logs(sync_id: str) -> list[dict]:
    records, _ = list_sync_logs(sync_id=sync_id)
    return records


# ── Tests: Routing ────────────────────────────────────────────────────


class TestRouteAdapter:
    """Verify the adapter routing logic."""

    def test_routes_apple_calendar(self):
        svc = SyncService(adapters=[MockSuccessAdapter()])
        adapter = svc._route_adapter("apple_calendar")
        assert adapter is not None
        assert adapter.target_name == "apple_calendar"

    def test_apple_reminder_does_not_route_to_calendar_adapter(self):
        """apple_reminder must not route via the apple_calendar adapter."""
        svc = SyncService(adapters=[MockSuccessAdapter()])
        adapter = svc._route_adapter("apple_reminder")
        assert adapter is None

    def test_returns_none_for_unregistered_target(self):
        svc = SyncService(adapters=[MockSuccessAdapter()])
        assert svc._route_adapter("unknown_target") is None

    def test_returns_none_when_no_adapters(self):
        svc = SyncService()
        assert svc._route_adapter("apple_calendar") is None

    def test_apple_prefix_is_not_used_for_routing(self):
        """Only exact sync_target matches may route to a registered adapter."""
        svc = SyncService(adapters=[MockSuccessAdapter()])
        assert svc._route_adapter("apple_calendar") is not None
        assert svc._route_adapter("apple_reminder") is None


# ── Tests: run_task_sync — success path ───────────────────────────────


class TestRunTaskSyncSuccess:
    """SyncService fully succeeds with a working adapter."""

    def test_routes_and_records_success(self):
        _insert_task("task_svc_001", sync_enabled=1)
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_001", "apple_calendar")

        assert result["success"] is True
        assert result["sync_status"] == "synced"
        assert result["external_id"] == "ext_abc123"
        assert result["sync_id"] is not None

    def test_creates_sync_state_and_persists(self):
        _insert_task("task_svc_002", sync_enabled=1)
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_002", "apple_calendar")

        # Verify persisted in DB
        record = get_sync_state(result["sync_id"])
        assert record is not None
        assert record["sync_status"] == "synced"
        assert record["external_id"] == "ext_abc123"

    def test_writes_sync_log_on_success(self):
        _insert_task("task_svc_003", sync_enabled=1)
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_003", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "success"
        assert logs[0]["triggered_by"] == "sync_service"

    def test_reuses_existing_sync_state_without_duplicate_create_when_already_synced(self):
        _insert_task("task_svc_004", sync_enabled=1)
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        r1 = svc.run_task_sync("task_svc_004", "apple_calendar")
        r2 = svc.run_task_sync("task_svc_004", "apple_calendar")

        assert r1["sync_id"] == r2["sync_id"]
        assert r2["success"] is True
        assert r2["sync_status"] == "skipped"
        assert r2["error_code"] == "already_synced"
        assert adapter.push_count == 1
        logs = _logs(r1["sync_id"])
        assert len(logs) == 1


class TestRunTaskSyncGuardrails:
    """Phase 22 Calendar sync eligibility guardrails."""

    def test_blocks_no_time_task_without_creating_sync_state(self):
        _insert_task("task_guard_no_time", sync_enabled=1, start_time=None, due_time=None)
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_no_time", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "missing_time"
        assert adapter.push_count == 0
        assert get_sync_state_by_key("task_guard_no_time", "apple_calendar") is None

    def test_blocks_sync_disabled_without_creating_sync_state(self):
        _insert_task("task_guard_disabled", sync_enabled=0)
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_disabled", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "sync_disabled"
        assert adapter.push_count == 0
        assert get_sync_state_by_key("task_guard_disabled", "apple_calendar") is None

    def test_blocks_due_time_not_after_start_time(self):
        _insert_task(
            "task_guard_bad_window",
            sync_enabled=1,
            start_time="2026-06-01T10:30:00+08:00",
            due_time="2026-06-01T10:00:00+08:00",
        )
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_bad_window", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "invalid_time_window"
        assert adapter.push_count == 0
        assert get_sync_state_by_key("task_guard_bad_window", "apple_calendar") is None

    def test_blocks_non_pending_task_status(self):
        _insert_task("task_guard_completed", sync_enabled=1, status="completed")
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_completed", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "invalid_task_status"
        assert adapter.push_count == 0
        assert get_sync_state_by_key("task_guard_completed", "apple_calendar") is None

    def test_blocks_when_sync_targets_do_not_include_calendar(self):
        _insert_task("task_guard_target_disabled", sync_enabled=1, sync_targets='["apple_reminder"]')
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_target_disabled", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "target_not_enabled"
        assert adapter.push_count == 0
        assert get_sync_state_by_key("task_guard_target_disabled", "apple_calendar") is None

    def test_existing_pending_sync_state_is_not_processed_when_no_time(self):
        _insert_task("task_guard_existing_pending_no_time", sync_enabled=1, start_time=None, due_time=None)
        sync = create_sync_state("task_guard_existing_pending_no_time", "apple_calendar")
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_existing_pending_no_time", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["sync_id"] == sync["sync_id"]
        assert result["error_code"] == "missing_time"
        assert adapter.push_count == 0
        state = get_sync_state(sync["sync_id"])
        assert state["sync_status"] == "skipped"

    def test_skipped_state_is_not_illegally_pushed_to_in_progress(self):
        _insert_task("task_guard_skipped", sync_enabled=1)
        sync = create_sync_state(
            "task_guard_skipped",
            "apple_calendar",
            sync_status="skipped",
        )
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_skipped", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "invalid_transition"
        assert adapter.push_count == 0
        assert get_sync_state(sync["sync_id"])["sync_status"] == "skipped"
        assert _logs(sync["sync_id"]) == []

    def test_skipped_state_can_be_revived_manually_before_sync(self):
        _insert_task("task_guard_skipped_manual", sync_enabled=1)
        sync = create_sync_state(
            "task_guard_skipped_manual",
            "apple_calendar",
            sync_status="skipped",
        )
        transition_sync_state(sync["sync_id"], "pending", trigger="manual")
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_guard_skipped_manual", "apple_calendar")

        assert result["success"] is True
        assert result["sync_status"] == "synced"
        assert adapter.push_count == 1
        state = get_sync_state(sync["sync_id"])
        assert state["sync_status"] == "synced"
        logs = _logs(sync["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "success"


# ── Phase 25 — Eligibility failure with stale/failed sync_state ────────


class TestEligibilityFailureStaleFailed:
    """Eligibility failures on stale/failed records must preserve external_id,
    not refresh payload_hash/last_synced_at, and write a sync_log."""

    def test_stale_record_eligibility_failure_preserves_state_and_external_id(self):
        """Stale record that fails eligibility must:
        - not push to adapter
        - not change external_id
        - not refresh payload_hash or last_synced_at
        - return the actual persisted status (not "skipped")
        - write a sync_log with the eligibility reason
        """
        _insert_task(
            "task_stale_elig", sync_enabled=1, start_time=None, due_time=None,
        )
        state = create_sync_state(
            "task_stale_elig", "apple_calendar", sync_status="synced",
        )
        update_sync_state(
            state["sync_id"],
            external_id="ext_stale_elig",
            payload_hash="old_hash_stale_elig",
            last_synced_at="2026-06-01T00:00:00",
        )
        transition_sync_state(state["sync_id"], "stale", trigger="trigger")

        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_stale_elig", "apple_calendar")

        # Adapter must not be called
        assert adapter.push_count == 0

        # Returned status must match persisted state (stale → skipped is illegal)
        assert result["success"] is False
        assert result["sync_status"] == "stale"
        assert result["sync_id"] == state["sync_id"]
        assert result["error_code"] == "missing_time"

        # DB state must be unchanged
        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "stale"
        assert persisted["external_id"] == "ext_stale_elig"
        assert persisted["payload_hash"] == "old_hash_stale_elig"
        assert persisted["last_synced_at"] == "2026-06-01T00:00:00"

        # Sync_log must be written with eligibility reason
        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "missing_time"

    def test_failed_record_eligibility_failure_preserves_state_and_external_id(self):
        """Failed record that fails eligibility must behave the same as stale:
        preserve state, not push, write sync_log."""
        _insert_task(
            "task_failed_elig", sync_enabled=1, start_time=None, due_time=None,
        )
        state = create_sync_state(
            "task_failed_elig", "apple_calendar", sync_status="failed",
        )
        update_sync_state(
            state["sync_id"],
            external_id="ext_failed_elig",
            payload_hash="old_hash_failed_elig",
            last_synced_at="2026-06-01T00:00:00",
        )

        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_failed_elig", "apple_calendar")

        # Adapter must not be called
        assert adapter.push_count == 0

        # Returned status must match persisted state
        assert result["success"] is False
        assert result["sync_status"] == "failed"
        assert result["sync_id"] == state["sync_id"]
        assert result["error_code"] == "missing_time"

        # DB state must be unchanged
        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "failed"
        assert persisted["external_id"] == "ext_failed_elig"
        assert persisted["payload_hash"] == "old_hash_failed_elig"
        assert persisted["last_synced_at"] == "2026-06-01T00:00:00"

        # Sync_log must be written with eligibility reason
        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "missing_time"

    def test_pending_record_eligibility_failure_still_transitions_to_skipped(self):
        """Pending records must still transition to skipped when eligibility fails,
        and the sync_log must be written."""
        _insert_task(
            "task_pending_elig", sync_enabled=1, start_time=None, due_time=None,
        )
        state = create_sync_state("task_pending_elig", "apple_calendar")

        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_pending_elig", "apple_calendar")

        assert adapter.push_count == 0
        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["sync_id"] == state["sync_id"]
        assert result["error_code"] == "missing_time"

        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "skipped"

        # Sync_log must now be written (previously missing)
        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "missing_time"
        assert logs[0]["sync_attempt"] == 1

    def test_pending_eligibility_failure_log_attempt_uses_max_plus_one(self):
        """pending eligibility failures must not restart sync_attempt at 1."""
        _insert_task(
            "task_pending_elig_attempt",
            sync_enabled=1,
            start_time=None,
            due_time=None,
        )
        state = create_sync_state("task_pending_elig_attempt", "apple_calendar")
        create_sync_log(
            sync_id=state["sync_id"],
            local_task_id=state["task_id"],
            sync_target=state["sync_target"],
            sync_attempt=2,
            sync_result="failed",
            error_code="previous_failure",
            triggered_by="test",
        )

        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_pending_elig_attempt", "apple_calendar")

        assert adapter.push_count == 0
        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        logs = _logs(state["sync_id"])
        assert [log["sync_attempt"] for log in logs] == [3, 2]
        assert logs[0]["error_code"] == "missing_time"


# ── Tests: run_task_sync — error paths ────────────────────────────────


class TestRunTaskSyncError:
    """SyncService error handling."""

    def test_invalid_target_returns_error(self):
        _insert_task("task_svc_inv")
        svc = SyncService()

        result = svc.run_task_sync("task_svc_inv", "invalid_target")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "invalid_target"
        assert "invalid_target" in (result["error_message"] or "")

    def test_no_adapter_returns_adapter_not_found(self):
        _insert_task("task_svc_no_adapter")
        svc = SyncService()

        result = svc.run_task_sync("task_svc_no_adapter", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "adapter_not_found"

    def test_apple_reminder_is_explicitly_unsupported_without_calendar_push(self):
        _insert_task("task_svc_reminder", sync_enabled=1, sync_targets='["apple_reminder"]')
        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_svc_reminder", "apple_reminder")

        assert result["success"] is False
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "unsupported_target"
        assert adapter.push_count == 0
        assert get_sync_state_by_key("task_svc_reminder", "apple_reminder") is None

    def test_retryable_failure_returns_failed_status(self):
        _insert_task("task_svc_retry", sync_enabled=1)
        svc = SyncService(adapters=[MockFailAdapter()])

        result = svc.run_task_sync("task_svc_retry", "apple_calendar")

        assert result["success"] is False
        # network error → retryable → "failed"
        assert result["sync_status"] == "failed"
        assert result["error_code"] == "network"

    def test_retryable_failure_writes_log(self):
        _insert_task("task_svc_retry_log", sync_enabled=1)
        svc = SyncService(adapters=[MockFailAdapter()])

        result = svc.run_task_sync("task_svc_retry_log", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "network"
        assert logs[0]["sync_attempt"] == 1

    def test_retryable_failure_log_attempt_uses_max_plus_one(self):
        """direct retryable failures should share queued max+1 attempt semantics."""
        _insert_task("task_svc_retry_attempt", sync_enabled=1)
        state = create_sync_state("task_svc_retry_attempt", "apple_calendar")
        transition_sync_state(state["sync_id"], "in_progress", trigger="engine")
        transition_sync_state(state["sync_id"], "failed", trigger="engine")
        create_sync_log(
            sync_id=state["sync_id"],
            local_task_id=state["task_id"],
            sync_target=state["sync_target"],
            sync_attempt=2,
            sync_result="failed",
            error_code="previous_failure",
            triggered_by="test",
        )
        svc = SyncService(adapters=[MockFailAdapter()])

        result = svc.run_task_sync("task_svc_retry_attempt", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed"
        logs = _logs(state["sync_id"])
        assert [log["sync_attempt"] for log in logs] == [3, 2]
        assert logs[0]["error_code"] == "network"

    def test_permanent_failure_returns_failed_permanent(self):
        _insert_task("task_svc_perm", sync_enabled=1)
        svc = SyncService(adapters=[MockPermanentFailAdapter()])

        result = svc.run_task_sync("task_svc_perm", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "auth_failed"

    def test_permanent_failure_writes_log(self):
        _insert_task("task_svc_perm_log", sync_enabled=1)
        svc = SyncService(adapters=[MockPermanentFailAdapter()])

        result = svc.run_task_sync("task_svc_perm_log", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "auth_failed"

    def test_config_invalid_returns_error_before_push(self):
        _insert_task("task_svc_config", sync_enabled=1)
        svc = SyncService(adapters=[MockAdapterConfigInvalid()])

        result = svc.run_task_sync("task_svc_config", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "adapter_config_invalid"

    def test_task_not_found_error(self):
        """Sync where task was deleted after sync_state creation."""
        # Create task + sync_state, then delete the task
        _insert_task("task_svc_orphan", sync_enabled=1)
        svc = SyncService(adapters=[MockSuccessAdapter()])
        first_result = svc.run_task_sync("task_svc_orphan", "apple_calendar")
        assert first_result["success"] is True

        # Delete the task behind SyncService's back
        conn = get_db()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM tasks WHERE task_id = 'task_svc_orphan'")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # Second call: task exists in sync_state but not in tasks table
        result = svc.run_task_sync("task_svc_orphan", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "task_not_found"
        assert result["sync_id"] == first_result["sync_id"]

    def test_adapter_exception_is_caught(self):
        """Adapter that raises during push is caught and logged."""
        _insert_task("task_svc_exc", sync_enabled=1)

        class CrashingAdapter(SyncAdapter):
            @property
            def target_name(self) -> str:
                return "apple_calendar"

            def validate_config(self) -> tuple[bool, Optional[str]]:
                return (True, None)

            def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
                raise RuntimeError("Adapter crashed!")

            def pull(self, external_id: str) -> Optional[dict]:
                return None

        svc = SyncService(adapters=[CrashingAdapter()])
        result = svc.run_task_sync("task_svc_exc", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed"
        assert result["error_code"] == "adapter_exception"
        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["error_code"] == "adapter_exception"
        assert logs[0]["sync_attempt"] == 1

    def test_adapter_exception_log_attempt_uses_max_plus_one(self):
        """direct adapter exceptions should share queued max+1 attempt semantics."""
        _insert_task("task_svc_exc_attempt", sync_enabled=1)
        state = create_sync_state("task_svc_exc_attempt", "apple_calendar")
        transition_sync_state(state["sync_id"], "in_progress", trigger="engine")
        transition_sync_state(state["sync_id"], "failed", trigger="engine")
        create_sync_log(
            sync_id=state["sync_id"],
            local_task_id=state["task_id"],
            sync_target=state["sync_target"],
            sync_attempt=3,
            sync_result="failed",
            error_code="previous_failure",
            triggered_by="test",
        )

        class CrashingAdapter(SyncAdapter):
            @property
            def target_name(self) -> str:
                return "apple_calendar"

            def validate_config(self) -> tuple[bool, Optional[str]]:
                return (True, None)

            def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
                raise RuntimeError("Adapter crashed again!")

            def pull(self, external_id: str) -> Optional[dict]:
                return None

        svc = SyncService(adapters=[CrashingAdapter()])
        result = svc.run_task_sync("task_svc_exc_attempt", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed"
        logs = _logs(state["sync_id"])
        assert [log["sync_attempt"] for log in logs] == [4, 3]
        assert logs[0]["error_code"] == "adapter_exception"


# ── Tests: add_adapter ────────────────────────────────────────────────


class TestAddAdapter:
    """Registration of adapters after construction."""

    def test_add_adapter_before_call(self):
        svc = SyncService()
        svc.add_adapter(MockSuccessAdapter())
        assert svc._route_adapter("apple_calendar") is not None

    def test_add_multiple_adapters(self):
        svc = SyncService()
        svc.add_adapter(MockSuccessAdapter())
        svc.add_adapter(MockFailAdapter())
        assert len(svc._adapters) == 2


# ── Tests: skipped consistency ────────────────────────────────────────


@pytest.mark.usefixtures("clean_db")
class TestSkippedConsistency:
    """skipped state cannot be illegally advanced; already-synced records are idempotent."""

    def test_already_synced_returns_skipped_without_log(self):
        """run_task_sync with synced + external_id must NOT write a sync_log."""
        _insert_task("task_skip_001", sync_enabled=1)
        state = create_sync_state("task_skip_001", "apple_calendar", sync_status="synced")
        update_sync_state(
            state["sync_id"],
            external_id="ext_already_done",
            last_synced_at="2026-06-01T00:00:00",
        )

        svc = SyncService(adapters=[MockSuccessAdapter()])
        result = svc.run_task_sync("task_skip_001", "apple_calendar")

        # Returns success=True (idempotent) with status=skipped
        assert result["success"] is True
        assert result["sync_status"] == "skipped"
        assert result["error_code"] == "already_synced"

        # Must NOT have written a sync_log
        logs, total = list_sync_logs(sync_id=state["sync_id"])
        assert total == 0, "Already-synced records must not produce sync_logs"

    def test_already_synced_does_not_change_state(self):
        """run_task_sync on an already synced record must keep status synced."""
        _insert_task("task_skip_002", sync_enabled=1)
        state = create_sync_state("task_skip_002", "apple_calendar", sync_status="synced")
        update_sync_state(
            state["sync_id"],
            external_id="ext_no_touch",
            last_synced_at="2026-06-01T00:00:00",
        )

        svc = SyncService(adapters=[MockSuccessAdapter()])
        svc.run_task_sync("task_skip_002", "apple_calendar")

        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "synced"
        assert persisted["external_id"] == "ext_no_touch"


# ── Phase 21 — Payload hash / drift wiring ─────────────────────────────


class TestRunTaskSyncPayloadHash:
    """SyncService stores payload_hash on sync_state and in sync_log."""

    def test_success_stores_payload_hash_on_state(self):
        """run_task_sync success stores a non-null SHA-256 payload_hash."""
        _insert_task("task_svc_phash", sync_enabled=1)
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_phash", "apple_calendar")

        assert result["success"] is True
        state = get_sync_state(result["sync_id"])
        assert state["payload_hash"] is not None
        assert len(state["payload_hash"]) == 64

    def test_success_log_includes_payload_hash_after(self):
        """Success sync_log records payload_hash_before and payload_hash_after."""
        _insert_task("task_svc_phash_log", sync_enabled=1)
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_phash_log", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "success"
        assert logs[0]["payload_hash_before"] is None  # first sync
        assert logs[0]["payload_hash_after"] is not None
        assert len(logs[0]["payload_hash_after"]) == 64
        assert logs[0]["external_id_after"] is not None


# ── Phase 22 — Stale resync update closure ─────────────────────────────


class TestStaleResyncService:
    """run_task_sync on stale records with external_id performs update (not create)."""

    def test_stale_resync_legal_path_calls_adapter_with_external_id(self):
        """Direct service call on stale+external_id transitions legally and updates.

        Phase 24 strengthened assertions:
        - payload_hash_before / payload_hash_after in sync_log
        - external_id_before / external_id_after in sync_log
        - last_synced_at is refreshed (non-null)
        - stale update does not lose external_id (preserved by update adapter)
        """

        class PreservingUpdateAdapter(SyncAdapter):
            """Adapter that preserves existing external_id for update semantics."""

            @property
            def target_name(self) -> str:
                return "apple_calendar"

            def validate_config(self) -> tuple[bool, Optional[str]]:
                return (True, None)

            def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
                external_id = sync_state.get("external_id")
                if external_id:
                    return AdapterResult(
                        success=True,
                        external_id=external_id,
                        sync_result="success",
                    )
                return AdapterResult(
                    success=True,
                    external_id="ext_new_fallback",
                    sync_result="success",
                )

            def pull(self, external_id: str) -> Optional[dict]:
                return None

        _insert_task("task_svc_stale_001", sync_enabled=1)
        # Create a synced record with external_id, then mark stale
        state = create_sync_state(
            "task_svc_stale_001", "apple_calendar", sync_status="synced"
        )
        update_sync_state(
            state["sync_id"],
            external_id="ext_stale_update_001",
            payload_hash="old_hash_before_stale_update",
            last_synced_at="2026-06-01T00:00:00",
        )
        transition_sync_state(state["sync_id"], "stale", trigger="trigger")

        adapter = PreservingUpdateAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_svc_stale_001", "apple_calendar")

        assert result["success"] is True
        assert result["sync_status"] == "synced"
        assert result["sync_id"] == state["sync_id"]

        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "synced"
        # stale update must preserve the existing external_id
        assert persisted["external_id"] == "ext_stale_update_001"
        # last_synced_at must be refreshed (non-null after successful push)
        assert persisted["last_synced_at"] is not None
        # payload_hash must be refreshed
        assert persisted["payload_hash"] is not None
        assert len(persisted["payload_hash"]) == 64

        # Verify sync_log diagnostics
        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "success"
        assert logs[0]["payload_hash_before"] == "old_hash_before_stale_update"
        assert logs[0]["payload_hash_after"] == persisted["payload_hash"]
        assert logs[0]["external_id_before"] == "ext_stale_update_001"
        assert logs[0]["external_id_after"] == "ext_stale_update_001"

    def test_stale_resync_success_refreshes_payload_hash(self):
        """Stale resync success replaces old payload_hash with current task hash."""
        _insert_task("task_svc_stale_phash", sync_enabled=1)
        state = create_sync_state(
            "task_svc_stale_phash", "apple_calendar", sync_status="synced"
        )
        update_sync_state(
            state["sync_id"],
            external_id="ext_stale_phash",
            payload_hash="old_hash_to_replace",
            last_synced_at="2026-06-01T00:00:00",
        )
        transition_sync_state(state["sync_id"], "stale", trigger="trigger")

        # Mutate task to change payload
        conn = get_db()
        conn.execute(
            "UPDATE tasks SET title = ? WHERE task_id = ?",
            ("Mutated title", "task_svc_stale_phash"),
        )
        conn.commit()

        svc = SyncService(adapters=[MockSuccessAdapter()])
        result = svc.run_task_sync("task_svc_stale_phash", "apple_calendar")

        assert result["success"] is True
        persisted = get_sync_state(state["sync_id"])
        assert persisted["payload_hash"] is not None
        assert persisted["payload_hash"] != "old_hash_to_replace"
        assert len(persisted["payload_hash"]) == 64

        # Verify hash matches current task data
        from local_api.sync_client.payload import compute_task_payload_hash

        task_row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", ("task_svc_stale_phash",)
        ).fetchone()
        expected = compute_task_payload_hash(dict(task_row))
        assert persisted["payload_hash"] == expected

        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "success"
        assert logs[0]["payload_hash_before"] == "old_hash_to_replace"
        assert logs[0]["payload_hash_after"] == expected

    def test_stale_resync_failure_does_not_mark_synced(self):
        """Stale resync failure preserves external_id and does not mark synced."""
        _insert_task("task_svc_stale_fail", sync_enabled=1)
        state = create_sync_state(
            "task_svc_stale_fail", "apple_calendar", sync_status="synced"
        )
        update_sync_state(
            state["sync_id"],
            external_id="ext_stale_fail_001",
            last_synced_at="2026-06-01T00:00:00",
        )
        transition_sync_state(state["sync_id"], "stale", trigger="trigger")

        svc = SyncService(adapters=[MockFailAdapter()])
        result = svc.run_task_sync("task_svc_stale_fail", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed"
        assert result["external_id"] is None

        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "failed"
        assert persisted["external_id"] == "ext_stale_fail_001"

        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"

    def test_stale_resync_permanent_failure_goes_failed_permanent(self):
        """Stale resync with auth_failed goes failed_permanent, preserves external_id."""
        _insert_task("task_svc_stale_perm", sync_enabled=1)
        state = create_sync_state(
            "task_svc_stale_perm", "apple_calendar", sync_status="synced"
        )
        update_sync_state(
            state["sync_id"],
            external_id="ext_stale_perm_001",
            last_synced_at="2026-06-01T00:00:00",
        )
        transition_sync_state(state["sync_id"], "stale", trigger="trigger")

        svc = SyncService(adapters=[MockPermanentFailAdapter()])
        result = svc.run_task_sync("task_svc_stale_perm", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"

        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "failed_permanent"
        assert persisted["external_id"] == "ext_stale_perm_001"

    def test_stale_without_external_id_still_works(self):
        """Stale record without external_id can still be synced (create path)."""
        _insert_task("task_svc_stale_noext", sync_enabled=1)
        state = create_sync_state(
            "task_svc_stale_noext", "apple_calendar", sync_status="synced"
        )
        # No external_id set — simulate an edge case
        transition_sync_state(state["sync_id"], "stale", trigger="trigger")

        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])

        result = svc.run_task_sync("task_svc_stale_noext", "apple_calendar")

        assert result["success"] is True
        assert result["sync_status"] == "synced"
        assert adapter.push_count == 1

        persisted = get_sync_state(state["sync_id"])
        assert persisted["external_id"] == "ext_abc123"


# ── Phase 23 — Sync state consistency hardening ──────────────────────────


class TestMetadataWriteFailure:
    """metadata write failure must not leave/return synced."""

    def test_metadata_write_failure_does_not_leave_synced(self, monkeypatch):
        """If update_sync_state fails, record must not be synced and
        result must not be success."""
        _insert_task("task_svc_meta_fail", sync_enabled=1)

        # update_sync_state is lazily imported inside run_task_sync from
        # .sync_state_service, so patch the source module.
        def _failing_update(sync_id, **kwargs):
            raise RuntimeError("Simulated DB write failure")

        monkeypatch.setattr(
            "local_api.services.sync_state_service.update_sync_state",
            _failing_update,
        )

        svc = SyncService(adapters=[MockSuccessAdapter()])
        result = svc.run_task_sync("task_svc_meta_fail", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed"
        assert result["error_code"] == "metadata_write_failed"

        # Verify DB state is NOT synced
        state = get_sync_state(result["sync_id"])
        assert state is not None
        assert state["sync_status"] == "failed"
        # payload_hash must not be set (metadata write failed)
        assert state["payload_hash"] is None
        # external_id from the successful push must not be stored
        assert state["external_id"] is None

        # Failure log must exist
        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "metadata_write_failed"

    def test_success_writes_metadata_before_synced_transition(self, monkeypatch):
        """Metadata (payload_hash, external_id) must be written BEFORE
        the synced transition, and success log after both."""
        _insert_task("task_svc_order", sync_enabled=1)

        write_order = []

        # update_sync_state is lazily imported from sync_state_service
        from local_api.services import sync_state_service as sss_mod

        real_update = sss_mod.update_sync_state

        def _tracked_update(sync_id, **kwargs):
            write_order.append("update_sync_state")
            return real_update(sync_id, **kwargs)

        monkeypatch.setattr(sss_mod, "update_sync_state", _tracked_update)

        # _log_sync is a module-level function in sync_service
        from local_api.services import sync_service as svc_mod

        real_log_sync = svc_mod._log_sync

        def _tracked_log_sync(*args, **kwargs):
            write_order.append("_log_sync")
            return real_log_sync(*args, **kwargs)

        monkeypatch.setattr(svc_mod, "_log_sync", _tracked_log_sync)

        svc = SyncService(adapters=[MockSuccessAdapter()])
        result = svc.run_task_sync("task_svc_order", "apple_calendar")

        assert result["success"] is True
        assert "update_sync_state" in write_order
        assert "_log_sync" in write_order

        # update_sync_state must happen before _log_sync
        update_idx = write_order.index("update_sync_state")
        log_idx = write_order.index("_log_sync")
        assert update_idx < log_idx, (
            f"Expected update_sync_state before _log_sync, got: {write_order}"
        )

        state = get_sync_state(result["sync_id"])
        assert state["sync_status"] == "synced"
        assert state["payload_hash"] is not None
        assert state["external_id"] is not None


# ── Phase 27 — Direct sync pre-push failure consistency ────────────────


class TestDirectSyncPrePushFailureConsistency:
    """adapter_not_found / adapter_config_invalid before push must:
    - use existing sync_state (not create new state)
    - write sync_log with triggered_by='sync_service'
    - transition to failed_permanent when legal
    - use max(sync_attempt)+1 attempt semantics
    - preserve current failure return when no sync_state exists
    """

    def test_existing_pending_state_missing_adapter_goes_failed_permanent(self):
        """Direct sync with existing pending state + missing adapter:
        state enters failed_permanent + adapter_not_found log."""
        _insert_task("task_p27_no_adapter", sync_enabled=1)
        state = create_sync_state("task_p27_no_adapter", "apple_calendar")
        assert state["sync_status"] == "pending"

        svc = SyncService()  # no adapters registered
        result = svc.run_task_sync("task_p27_no_adapter", "apple_calendar")

        assert result["success"] is False
        assert result["sync_id"] == state["sync_id"]
        assert result["error_code"] == "adapter_not_found"
        assert result["sync_status"] == "failed_permanent"

        # DB state must be failed_permanent
        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "failed_permanent"

        # adapter_not_found log must exist with triggered_by=sync_service
        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "adapter_not_found"
        assert logs[0]["triggered_by"] == "sync_service"

    def test_existing_pending_state_adapter_config_invalid_goes_failed_permanent(self):
        """Direct sync with existing pending state + adapter config invalid:
        state enters failed_permanent + adapter_config_invalid log."""
        _insert_task("task_p27_config_invalid", sync_enabled=1)
        state = create_sync_state("task_p27_config_invalid", "apple_calendar")
        assert state["sync_status"] == "pending"

        svc = SyncService(adapters=[MockAdapterConfigInvalid()])
        result = svc.run_task_sync("task_p27_config_invalid", "apple_calendar")

        assert result["success"] is False
        assert result["sync_id"] == state["sync_id"]
        assert result["error_code"] == "adapter_config_invalid"
        assert result["sync_status"] == "failed_permanent"

        # DB state must be failed_permanent
        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "failed_permanent"

        # adapter_config_invalid log must exist with triggered_by=sync_service
        logs = _logs(state["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "adapter_config_invalid"
        assert logs[0]["triggered_by"] == "sync_service"

    def test_pending_adapter_not_found_log_attempt_uses_max_plus_one(self):
        """direct sync adapter_not_found with existing logs must use
        max(sync_attempt)+1, matching queued sync semantics."""
        _insert_task("task_p27_attempt_max", sync_enabled=1)
        state = create_sync_state("task_p27_attempt_max", "apple_calendar")
        # Pre-create a log with sync_attempt=2
        create_sync_log(
            sync_id=state["sync_id"],
            local_task_id=state["task_id"],
            sync_target=state["sync_target"],
            sync_attempt=2,
            sync_result="failed",
            error_code="previous_failure",
            triggered_by="test",
        )

        svc = SyncService()  # no adapters
        result = svc.run_task_sync("task_p27_attempt_max", "apple_calendar")

        assert result["success"] is False
        assert result["error_code"] == "adapter_not_found"
        logs = _logs(state["sync_id"])
        # New log must use max(2) + 1 = 3
        assert [log["sync_attempt"] for log in logs] == [3, 2]
        assert logs[0]["error_code"] == "adapter_not_found"

    def test_pending_config_invalid_log_attempt_uses_max_plus_one(self):
        """direct sync adapter_config_invalid with existing logs must use
        max(sync_attempt)+1, matching queued sync semantics."""
        _insert_task("task_p27_config_attempt", sync_enabled=1)
        state = create_sync_state("task_p27_config_attempt", "apple_calendar")
        create_sync_log(
            sync_id=state["sync_id"],
            local_task_id=state["task_id"],
            sync_target=state["sync_target"],
            sync_attempt=3,
            sync_result="failed",
            error_code="previous_failure",
            triggered_by="test",
        )

        svc = SyncService(adapters=[MockAdapterConfigInvalid()])
        result = svc.run_task_sync("task_p27_config_attempt", "apple_calendar")

        assert result["success"] is False
        assert result["error_code"] == "adapter_config_invalid"
        logs = _logs(state["sync_id"])
        assert [log["sync_attempt"] for log in logs] == [4, 3]
        assert logs[0]["error_code"] == "adapter_config_invalid"

    def test_no_existing_state_missing_adapter_creates_nothing(self):
        """Direct sync with no sync_state + missing adapter:
        creates no new state, writes no log, returns error without sync_id."""
        _insert_task("task_p27_no_state", sync_enabled=1)

        svc = SyncService()  # no adapters
        result = svc.run_task_sync("task_p27_no_state", "apple_calendar")

        assert result["success"] is False
        assert result["sync_id"] is None
        assert result["error_code"] == "adapter_not_found"

        # Must not create sync_state
        assert get_sync_state_by_key("task_p27_no_state", "apple_calendar") is None

        # Must not write any sync_log
        all_logs, total = list_sync_logs(limit=1000)
        assert total == 0

    def test_no_existing_state_config_invalid_creates_nothing(self):
        """Direct sync with no sync_state + adapter config invalid:
        creates no new state, writes no log, returns error without sync_id."""
        _insert_task("task_p27_no_state_config", sync_enabled=1)

        svc = SyncService(adapters=[MockAdapterConfigInvalid()])
        result = svc.run_task_sync("task_p27_no_state_config", "apple_calendar")

        assert result["success"] is False
        assert result["sync_id"] is None
        assert result["error_code"] == "adapter_config_invalid"

        # Must not create sync_state
        assert get_sync_state_by_key("task_p27_no_state_config", "apple_calendar") is None

        # Must not write any sync_log
        all_logs, total = list_sync_logs(limit=1000)
        assert total == 0

    def test_apple_reminder_does_not_create_calendar_state_or_log(self):
        """apple_reminder must not route to Calendar adapter and must not
        create any Calendar sync_state or sync_log."""
        _insert_task(
            "task_p27_reminder",
            sync_enabled=1,
            sync_targets='["apple_reminder"]',
        )

        adapter = CountingSuccessAdapter()
        svc = SyncService(adapters=[adapter])
        result = svc.run_task_sync("task_p27_reminder", "apple_reminder")

        # Must return unsupported_target, never reach adapter routing
        assert result["success"] is False
        assert result["error_code"] == "unsupported_target"
        assert adapter.push_count == 0

        # Must not create any Calendar state
        assert get_sync_state_by_key("task_p27_reminder", "apple_calendar") is None
        # Must not create any Reminder state either
        assert get_sync_state_by_key("task_p27_reminder", "apple_reminder") is None

        # Must not write any sync_log
        all_logs, total = list_sync_logs(limit=1000)
        assert total == 0

    def test_adapter_not_found_mid_transition_uses_fresh_db_read(self, monkeypatch):
        """When _transition_to_failed_permanent makes intermediate legal
        transitions then returns None, the result must reflect the persisted
        DB status, not the stale originally-loaded sync_state snapshot."""
        _insert_task("task_mid_trans", sync_enabled=1)
        state = create_sync_state("task_mid_trans", "apple_calendar")
        assert state["sync_status"] == "pending"

        # Mock _transition_to_failed_permanent to simulate an intermediate
        # transition (pending → in_progress succeeds) followed by a later
        # failure (the second step returns None).
        from local_api.services import sync_service as svc_mod

        def _mock_ttpf_mid_transition(sync_id: str):
            transition_sync_state(sync_id, "in_progress", trigger="engine")
            return None

        monkeypatch.setattr(
            svc_mod, "_transition_to_failed_permanent", _mock_ttpf_mid_transition
        )

        svc = SyncService()  # no adapters → adapter_not_found branch
        result = svc.run_task_sync("task_mid_trans", "apple_calendar")

        # Without the fix, result["sync_status"] would be "pending"
        # (the stale originally-loaded snapshot).  With the fix it must
        # reflect the true DB state ("in_progress").
        assert result["success"] is False
        assert result["sync_status"] == "in_progress", (
            f"Expected persisted DB status 'in_progress', got {result['sync_status']}"
        )
        assert result["error_code"] == "adapter_not_found"

        # Verify DB actually shows in_progress
        persisted = get_sync_state(state["sync_id"])
        assert persisted["sync_status"] == "in_progress"
