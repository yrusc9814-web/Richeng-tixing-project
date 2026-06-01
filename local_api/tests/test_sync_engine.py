"""Phase 6 integration tests for the sync engine scanner."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from local_api import config
from local_api.database import get_db, init_db, reset_db
from local_api.services.sync_log_service import create_sync_log, list_sync_logs
from local_api.services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    transition_sync_state,
    update_sync_state,
)
from local_api.sync_engine import ScanResult, SyncEngine


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    reset_db()
    init_db()
    monkeypatch.setattr(config, "SYNC_JITTER_ENABLED", False)
    monkeypatch.setattr(config, "SYNC_BATCH_SIZE", 10)
    monkeypatch.setattr(config, "SYNC_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(config, "SYNC_SCAN_INTERVAL", 1)
    yield
    reset_db()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _insert_task(task_id: str) -> None:
    conn = get_db()
    now = _iso(_now())
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, start_time, due_time,
            created_channel, created_at, updated_at, sync_enabled, sync_targets
        ) VALUES (?, ?, 'pending', 'P2', ?, ?, 'api_test', ?, ?, 1, ?)""",
        (
            task_id,
            f"Task {task_id}",
            "2026-06-01T10:00:00+08:00",
            "2026-06-01T10:30:00+08:00",
            now,
            now,
            '["apple_calendar"]',
        ),
    )
    conn.commit()


def _sync(
    task_id: str,
    *,
    status: str = "pending",
    target: str = "apple_calendar",
) -> dict:
    _insert_task(task_id)
    return create_sync_state(task_id=task_id, sync_target=target, sync_status=status)


def _set_updated_at(sync_id: str, dt: datetime) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE sync_state SET updated_at = ? WHERE sync_id = ?",
        (_iso(dt), sync_id),
    )
    conn.commit()


def _logs(sync_id: str) -> list[dict]:
    records, _ = list_sync_logs(sync_id=sync_id)
    return records


def test_empty_scan_returns_all_zeroes():
    result = SyncEngine().scan_once()

    assert result == ScanResult()
    assert result.errors == []


def test_pick_pending_promotes_to_in_progress_without_log():
    sync = _sync("task_engine_pending")

    result = SyncEngine().scan_once()

    assert result.pending_picked == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "in_progress"
    assert _logs(sync["sync_id"]) == []


def test_retry_failed_after_backoff_promotes_without_log():
    sync = _sync("task_engine_retry", status="failed")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=1,
        sync_result="failed",
        error_code="network",
        triggered_by="test",
    )
    conn = get_db()
    conn.execute(
        "UPDATE sync_logs SET created_at = ? WHERE sync_id = ?",
        (_iso(_now() - timedelta(seconds=31)), sync["sync_id"]),
    )
    conn.commit()

    result = SyncEngine().scan_once()

    assert result.retry_triggered == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "in_progress"
    assert len(_logs(sync["sync_id"])) == 1


def test_no_retry_before_backoff_expires():
    sync = _sync("task_engine_no_retry", status="failed")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=1,
        sync_result="failed",
        error_code="network",
        triggered_by="test",
    )

    result = SyncEngine().scan_once()

    assert result.retry_triggered == 0
    assert get_sync_state(sync["sync_id"])["sync_status"] == "failed"
    assert len(_logs(sync["sync_id"])) == 1


def test_timeout_first_attempt_goes_failed_and_logs_attempt_1():
    sync = _sync("task_engine_timeout_1", status="in_progress")
    _set_updated_at(sync["sync_id"], _now() - timedelta(seconds=301))

    result = SyncEngine().scan_once()

    assert result.timeout_detected == 1
    assert result.failed == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "failed"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "failed"
    assert logs[0]["error_code"] == "timeout"
    assert logs[0]["sync_attempt"] == 1


def test_timeout_fourth_attempt_goes_failed_permanent_and_logs_attempt_4():
    sync = _sync("task_engine_timeout_4", status="in_progress")
    for attempt in (1, 2, 3):
        create_sync_log(
            sync_id=sync["sync_id"],
            local_task_id=sync["task_id"],
            sync_target=sync["sync_target"],
            sync_attempt=attempt,
            sync_result="failed",
            error_code="network",
            triggered_by="test",
        )
    _set_updated_at(sync["sync_id"], _now() - timedelta(seconds=301))

    result = SyncEngine().scan_once()

    assert result.timeout_detected == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "failed_permanent"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 4
    timeout_log = [log for log in logs if log["error_code"] == "timeout"][0]
    assert timeout_log["sync_attempt"] == 4
    assert timeout_log["sync_result"] == "failed"


def test_full_cycle_pending_to_synced_with_mock_completion_log():
    sync = _sync("task_engine_full_cycle")

    result = SyncEngine().scan_once()
    assert result.pending_picked == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "in_progress"

    transition_sync_state(sync["sync_id"], "synced", trigger="engine")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=1,
        sync_result="success",
        triggered_by="test",
    )

    assert get_sync_state(sync["sync_id"])["sync_status"] == "synced"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "success"
    assert logs[0]["sync_attempt"] == 1


def test_unretryable_error_goes_failed_permanent_with_log_attempt():
    sync = _sync("task_engine_unretryable", status="in_progress")

    transition_sync_state(sync["sync_id"], "failed_permanent", trigger="engine")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=1,
        sync_result="failed",
        error_code="auth_failed",
        triggered_by="test",
    )

    assert get_sync_state(sync["sync_id"])["sync_status"] == "failed_permanent"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["error_code"] == "auth_failed"


def test_retry_cap_failed_attempt_4_goes_failed_permanent_and_logs():
    sync = _sync("task_engine_retry_cap", status="failed")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=4,
        sync_result="failed",
        error_code="network",
        triggered_by="test",
    )

    result = SyncEngine().scan_once()

    assert result.failed == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "failed_permanent"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 2
    retry_cap_log = [log for log in logs if log["error_code"] == "retry_cap"][0]
    assert retry_cap_log["sync_attempt"] == 4
    assert retry_cap_log["sync_result"] == "failed"


def test_stale_promotes_to_pending_without_log():
    sync = _sync("task_engine_stale", status="stale")

    result = SyncEngine().scan_once()

    assert result.stale_triggered == 1
    assert get_sync_state(sync["sync_id"])["sync_status"] == "pending"
    assert _logs(sync["sync_id"]) == []


def test_timeout_priority_beats_pending_in_same_cycle():
    timed_out = _sync("task_engine_priority_timeout", status="in_progress")
    pending = _sync("task_engine_priority_pending")
    _set_updated_at(timed_out["sync_id"], _now() - timedelta(seconds=301))

    result = SyncEngine().scan_once()

    assert result.timeout_detected == 1
    assert result.pending_picked == 0
    assert get_sync_state(timed_out["sync_id"])["sync_status"] == "failed"
    assert get_sync_state(pending["sync_id"])["sync_status"] == "pending"


def test_start_stop_lifecycle():
    engine = SyncEngine()

    engine.start()
    assert engine.is_running

    engine.stop()
    assert not engine.is_running


# ── Phase 8A — Adapter integration tests ────────────────────────────────────

from local_api.adapters import AdapterResult
from local_api.adapters.apple_adapter import MockAppleAdapter
from local_api.adapters.weather_adapter import MockWeatherAdapter


def _adapters():
    """Helper to create the standard mock adapter set."""
    return [MockAppleAdapter()]


def test_pending_to_synced_through_adapter():
    """Adapter push cycle transitions pending → in_progress → synced."""
    sync = _sync("task_adapter_synced")
    engine = SyncEngine(adapters=_adapters())

    result = engine.scan_once()

    assert result.pending_picked == 1
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "success"


def test_adapter_cycle_error_isolation():
    """Error in one record does not affect another.

    Uses PRAGMA foreign_keys=OFF to bypass FK CASCADE DELETE
    when removing the task row (database.py schema not modified).
    """
    sync_orphan = _sync("task_adapter_orphan")
    conn = get_db()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM tasks WHERE task_id = 'task_adapter_orphan'")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    # Normal record
    sync_ok = _sync("task_adapter_ok")

    engine = SyncEngine(adapters=_adapters())
    engine.scan_once()

    # Good record should be synced
    state_ok = get_sync_state(sync_ok["sync_id"])
    assert state_ok["sync_status"] == "synced"

    # Orphan should be failed_permanent (task_not_found)
    state_orphan = get_sync_state(sync_orphan["sync_id"])
    assert state_orphan is not None
    assert state_orphan["sync_status"] == "failed_permanent"


def test_route_adapter_returns_none_for_no_match():
    """Direct test of _route_adapter: no adapter matches -> None."""
    from local_api.sync_engine import SyncEngine

    engine = SyncEngine(adapters=[MockAppleAdapter()])
    # MockAppleAdapter matches apple_* targets
    route = engine._route_adapter("whatever")
    assert route is None


def test_route_adapter_returns_apple_for_apple_targets():
    """_route_adapter routes Apple targets only by exact adapter target."""
    engine = SyncEngine(adapters=[MockAppleAdapter()])

    route_cal = engine._route_adapter("apple_calendar")
    assert route_cal is not None
    assert route_cal.target_name == "apple_calendar"

    route_rem = engine._route_adapter("apple_reminder")
    assert route_rem is None


def test_apple_reminder_pending_does_not_route_to_calendar_adapter():
    """apple_reminder must not fall through to the Calendar adapter."""

    class CountingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_reminder_isolated", target="apple_reminder")
    adapter = CountingCalendarAdapter()
    engine = SyncEngine(adapters=[adapter])

    result = engine.scan_once()

    assert result.pending_picked == 1
    assert adapter.push_count == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed_permanent"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["error_code"] == "adapter_not_found"


def test_adapter_cycle_rechecks_calendar_eligibility_before_push():
    """A pending record whose task is no longer eligible must not be pushed."""

    class CountingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            return super().push(task_data, sync_state)

    blocked = _sync("task_engine_recheck_blocked")
    allowed = _sync("task_engine_recheck_allowed")
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET sync_enabled = 0 WHERE task_id = ?",
        (blocked["task_id"],),
    )
    conn.commit()
    adapter = CountingCalendarAdapter()

    result = SyncEngine(adapters=[adapter]).scan_once()

    assert result.pending_picked == 2
    assert adapter.push_count == 1
    blocked_state = get_sync_state(blocked["sync_id"])
    allowed_state = get_sync_state(allowed["sync_id"])
    assert blocked_state["sync_status"] == "failed_permanent"
    assert blocked_state["external_id"] is None
    assert allowed_state["sync_status"] == "synced"
    logs = _logs(blocked["sync_id"])
    assert len(logs) == 1
    assert logs[0]["error_code"] == "sync_disabled"
    assert logs[0]["sync_result"] == "failed"


def test_stale_repush_preserves_existing_external_id_for_calendar_update():
    """Stale Calendar records must update by external_id instead of duplicating."""

    class UpdatingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.seen_external_ids: list[str | None] = []
            self.created_count = 0

        def push(self, task_data: dict, sync_state: dict):
            external_id = sync_state.get("external_id")
            self.seen_external_ids.append(external_id)
            if external_id:
                return AdapterResult(
                    success=True,
                    external_id=external_id,
                    sync_result="success",
                )
            self.created_count += 1
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_stale_idempotent", status="synced")
    update_sync_state(sync["sync_id"], external_id="calendar_event_existing")
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")
    adapter = UpdatingCalendarAdapter()
    engine = SyncEngine(adapters=[adapter])

    stale_result = engine.scan_once()
    push_result = engine.scan_once()

    assert stale_result.stale_triggered == 1
    assert push_result.pending_picked == 1
    assert adapter.seen_external_ids == ["calendar_event_existing"]
    assert adapter.created_count == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    assert state["external_id"] == "calendar_event_existing"
