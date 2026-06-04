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
    assert state["external_id"] is not None
    # Phase 31: verify success finalization persisted metadata
    assert state["payload_hash"] is not None
    assert len(state["payload_hash"]) == 64
    assert state["last_synced_at"] is not None
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
    """apple_reminder pending must not be picked by engine lifecycle."""

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

    # Engine lifecycle filters to apple_calendar only — reminder stays pending
    assert result.pending_picked == 0
    assert adapter.push_count == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "pending"
    # No log should be generated because engine never touched this row
    logs = _logs(sync["sync_id"])
    assert len(logs) == 0


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


def test_skipped_state_is_not_picked_or_pushed_by_scan():
    sync = _sync("task_engine_skipped_scan", status="skipped")
    engine = SyncEngine(adapters=_adapters())

    result = engine.scan_once()

    assert result.pending_picked == 0
    assert get_sync_state(sync["sync_id"])["sync_status"] == "skipped"
    assert _logs(sync["sync_id"]) == []


def test_adapter_cycle_refuses_skipped_without_success_log():
    class CountingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_skipped_direct", status="skipped")
    adapter = CountingCalendarAdapter()
    engine = SyncEngine(adapters=[adapter])

    engine._adapter_push_cycle([sync])

    assert adapter.push_count == 0
    assert get_sync_state(sync["sync_id"])["sync_status"] == "skipped"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "skipped"
    assert logs[0]["error_code"] == "invalid_transition"


# ── Phase 21 — Payload hash / drift wiring ──────────────────────────────


def test_pending_to_synced_stores_payload_hash():
    """Adapter success stores a non-null payload_hash on sync_state."""
    sync = _sync("task_engine_phash_store")
    engine = SyncEngine(adapters=_adapters())

    engine.scan_once()

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    assert state["payload_hash"] is not None
    assert len(state["payload_hash"]) == 64  # SHA-256 hex


def test_sync_success_log_includes_payload_hashes():
    """Success sync_log records payload_hash_before and payload_hash_after."""
    sync = _sync("task_engine_phash_log")
    engine = SyncEngine(adapters=_adapters())

    engine.scan_once()

    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "success"
    # Before first sync, payload_hash_before should be None
    assert logs[0]["payload_hash_before"] is None
    assert logs[0]["payload_hash_after"] is not None
    assert len(logs[0]["payload_hash_after"]) == 64
    # external_id_after should be set
    assert logs[0]["external_id_after"] is not None


# ── Phase 22 — Stale resync update closure ──────────────────────────────


from local_api.sync_client.payload import compute_task_payload_hash


def test_stale_update_success_refreshes_payload_hash():
    """Stale resync with task mutation refreshes payload_hash on sync_state."""

    class PhashPreservingAdapter(MockAppleAdapter):
        """Adapter that preserves existing external_id (update semantics)."""

        def push(self, task_data: dict, sync_state: dict):
            external_id = sync_state.get("external_id")
            if external_id:
                return AdapterResult(
                    success=True,
                    external_id=external_id,
                    sync_result="success",
                )
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_stale_phash_refresh", status="synced")
    update_sync_state(
        sync["sync_id"],
        external_id="calendar_event_phash",
        payload_hash="old_hash_should_be_replaced",
    )
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")

    # Mutate the task to change its payload_hash
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET title = ? WHERE task_id = ?",
        ("Updated title for phash", sync["task_id"]),
    )
    conn.commit()

    engine = SyncEngine(adapters=[PhashPreservingAdapter()])

    # First scan: stale → pending
    stale_result = engine.scan_once()
    assert stale_result.stale_triggered == 1

    # Second scan: pending → push → synced
    push_result = engine.scan_once()
    assert push_result.pending_picked == 1

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    assert state["external_id"] == "calendar_event_phash"
    assert state["payload_hash"] is not None
    assert state["payload_hash"] != "old_hash_should_be_replaced"
    assert len(state["payload_hash"]) == 64

    # Verify the hash matches the current (mutated) task data
    conn = get_db()
    task_row = conn.execute(
        "SELECT * FROM tasks WHERE task_id = ?", (sync["task_id"],)
    ).fetchone()
    expected_hash = compute_task_payload_hash(dict(task_row))
    assert state["payload_hash"] == expected_hash

    # Verify success log has payload_hash fields
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "success"
    assert logs[0]["payload_hash_before"] == "old_hash_should_be_replaced"
    assert logs[0]["payload_hash_after"] == expected_hash
    assert logs[0]["external_id_before"] == "calendar_event_phash"
    assert logs[0]["external_id_after"] == "calendar_event_phash"


def test_stale_update_failure_does_not_mark_synced():
    """Stale resync with adapter failure preserves external_id and logs failure."""

    class FailingUpdateAdapter(MockAppleAdapter):
        def push(self, task_data: dict, sync_state: dict):
            return AdapterResult(
                success=False,
                error_code="network",
                error_message="Simulated network error",
                sync_result="failed",
            )

    sync = _sync("task_engine_stale_fail", status="synced")
    update_sync_state(sync["sync_id"], external_id="calendar_event_fail")
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")
    adapter = FailingUpdateAdapter()
    engine = SyncEngine(adapters=[adapter])

    # First scan: stale → pending
    stale_result = engine.scan_once()
    assert stale_result.stale_triggered == 1

    # Second scan: pending → push → failed
    push_result = engine.scan_once()
    assert push_result.pending_picked == 1

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed"
    assert state["external_id"] == "calendar_event_fail"

    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "failed"
    assert logs[0]["error_code"] == "network"
    assert logs[0]["sync_attempt"] == 1


def test_stale_update_failure_uses_max_plus_one_attempt():
    """queued stale→pending→push failure should log max(sync_attempt)+1."""

    class FailingUpdateAdapter(MockAppleAdapter):
        def push(self, task_data: dict, sync_state: dict):
            return AdapterResult(
                success=False,
                error_code="network",
                error_message="Simulated network error",
                sync_result="failed",
            )

    sync = _sync("task_engine_stale_fail_attempt", status="synced")
    update_sync_state(sync["sync_id"], external_id="calendar_event_fail_attempt")
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=2,
        sync_result="failed",
        error_code="previous_failure",
        triggered_by="test",
    )
    engine = SyncEngine(adapters=[FailingUpdateAdapter()])

    stale_result = engine.scan_once()
    push_result = engine.scan_once()

    assert stale_result.stale_triggered == 1
    assert push_result.pending_picked == 1
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed"
    assert state["external_id"] == "calendar_event_fail_attempt"
    logs = _logs(sync["sync_id"])
    assert [log["sync_attempt"] for log in logs] == [3, 2]
    assert logs[0]["error_code"] == "network"


def test_stale_update_permanent_failure_goes_failed_permanent():
    """Stale resync with permanent adapter error (auth_failed) goes failed_permanent."""

    class AuthFailAdapter(MockAppleAdapter):
        def push(self, task_data: dict, sync_state: dict):
            return AdapterResult(
                success=False,
                error_code="auth_failed",
                error_message="Invalid credentials",
                sync_result="failed",
            )

    sync = _sync("task_engine_stale_permfail", status="synced")
    update_sync_state(sync["sync_id"], external_id="calendar_event_permfail")
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")
    adapter = AuthFailAdapter()
    engine = SyncEngine(adapters=[adapter])

    # First scan: stale → pending
    engine.scan_once()
    # Second scan: pending → push → failed_permanent
    engine.scan_once()

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed_permanent"
    assert state["external_id"] == "calendar_event_permfail"

    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "failed"
    assert logs[0]["error_code"] == "auth_failed"


def test_apple_reminder_stale_does_not_route_to_calendar_adapter():
    """apple_reminder stale must not be processed by engine lifecycle."""

    class CountingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_reminder_stale", target="apple_reminder", status="synced")
    update_sync_state(sync["sync_id"], external_id="reminder_ext_stale")
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")
    adapter = CountingCalendarAdapter()
    engine = SyncEngine(adapters=[adapter])

    # Engine lifecycle filters to apple_calendar only — reminder stays stale
    result = engine.scan_once()
    assert result.stale_triggered == 0

    # Reminder remains stale, no adapter calls, no logs generated
    assert adapter.push_count == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "stale"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 0


# ── Phase 29 — Reminder queue lifecycle isolation ─────────────────────────


def test_apple_reminder_failed_not_retried_or_capped():
    """apple_reminder failed rows must not be retried or capped by engine."""

    class CountingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_reminder_failed", target="apple_reminder", status="failed")
    # Add 4 failed attempts — if engine processed this, it would hit retry cap
    for attempt in (1, 2, 3, 4):
        create_sync_log(
            sync_id=sync["sync_id"],
            local_task_id=sync["task_id"],
            sync_target=sync["sync_target"],
            sync_attempt=attempt,
            sync_result="failed",
            error_code="network",
            triggered_by="test",
        )
    # Set log timestamps old enough for backoff to have expired
    conn = get_db()
    conn.execute(
        "UPDATE sync_logs SET created_at = ? WHERE sync_id = ?",
        (_iso(_now() - timedelta(seconds=3600)), sync["sync_id"]),
    )
    conn.commit()

    adapter = CountingCalendarAdapter()
    result = SyncEngine(adapters=[adapter]).scan_once()

    # Engine must not retry or cap the reminder row
    assert result.retry_triggered == 0
    assert result.failed == 0
    assert adapter.push_count == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed"
    # No new logs beyond the 4 synthetic ones
    logs = _logs(sync["sync_id"])
    assert len(logs) == 4


def test_apple_reminder_timeout_not_processed():
    """apple_reminder timed-out in_progress must not be failed by engine."""

    sync = _sync(
        "task_engine_reminder_timeout", target="apple_reminder", status="in_progress"
    )
    _set_updated_at(sync["sync_id"], _now() - timedelta(seconds=301))

    result = SyncEngine().scan_once()

    # Timeout handler filters to apple_calendar only — reminder stays in_progress
    assert result.timeout_detected == 0
    assert result.failed == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "in_progress"
    # No timeout log generated
    logs = _logs(sync["sync_id"])
    assert len(logs) == 0


def test_mixed_calendar_and_reminder_pending_processes_only_calendar():
    """Mixed pending scan picks calendar, leaves reminder untouched."""

    class TrackingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0
            self.pushed_task_ids: list[str] = []

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            self.pushed_task_ids.append(task_data["task_id"])
            return super().push(task_data, sync_state)

    cal_sync = _sync("task_engine_mixed_cal", target="apple_calendar")
    rem_sync = _sync("task_engine_mixed_rem", target="apple_reminder")
    adapter = TrackingCalendarAdapter()

    result = SyncEngine(adapters=[adapter]).scan_once()

    # Only the calendar record should be picked
    assert result.pending_picked == 1

    # Calendar: picked → adapter push → synced
    cal_state = get_sync_state(cal_sync["sync_id"])
    assert cal_state["sync_status"] == "synced"
    cal_logs = _logs(cal_sync["sync_id"])
    assert len(cal_logs) == 1
    assert cal_logs[0]["sync_result"] == "success"

    # Reminder: not picked, stays pending, no logs, no adapter call
    rem_state = get_sync_state(rem_sync["sync_id"])
    assert rem_state["sync_status"] == "pending"
    rem_logs = _logs(rem_sync["sync_id"])
    assert len(rem_logs) == 0

    # Adapter only called for calendar, not reminder
    assert adapter.push_count == 1
    assert adapter.pushed_task_ids == ["task_engine_mixed_cal"]


# ── Phase 23 — Sync state consistency hardening ──────────────────────────


def test_metadata_write_failure_does_not_leave_synced(monkeypatch):
    """If update_sync_state fails during success path, record must not be synced."""

    class MetadataFailingAdapter(MockAppleAdapter):
        """Adapter that succeeds at push to trigger metadata write path."""

        def push(self, task_data: dict, sync_state: dict):
            return AdapterResult(
                success=True,
                external_id="ext_should_not_persist",
                sync_result="success",
            )

    sync = _sync("task_engine_meta_fail")
    # Transition to in_progress so adapter push cycle picks it up
    transition_sync_state(sync["sync_id"], "in_progress", trigger="engine")

    # Make update_sync_state raise to simulate metadata write failure
    call_count = [0]

    def _failing_update(sync_id, **kwargs):
        call_count[0] += 1
        raise RuntimeError("Simulated DB write failure")

    monkeypatch.setattr(
        "local_api.sync_engine.update_sync_state",
        _failing_update,
    )

    engine = SyncEngine(adapters=[MetadataFailingAdapter()])
    engine._adapter_push_cycle([get_sync_state(sync["sync_id"])])

    assert call_count[0] == 1

    state = get_sync_state(sync["sync_id"])
    # Must NOT be synced — metadata write failed, so state should be failed
    assert state["sync_status"] == "failed"
    assert state["payload_hash"] is None
    assert state["external_id"] is None

    # Failure log must be written
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "failed"
    assert logs[0]["error_code"] == "metadata_write_failed"


def test_sync_success_writes_metadata_before_synced_transition(monkeypatch):
    """Metadata (payload_hash, external_id) must be written BEFORE synced transition."""

    write_order = []

    real_update = update_sync_state

    def _tracked_update(sync_id, **kwargs):
        write_order.append("update_sync_state")
        return real_update(sync_id, **kwargs)

    monkeypatch.setattr(
        "local_api.sync_engine.update_sync_state",
        _tracked_update,
    )

    from local_api.services.sync_state_service import transition_sync_state as real_transition

    def _tracked_transition(sync_id, to_status, *, trigger="engine"):
        write_order.append(f"transition_to_{to_status}")
        return real_transition(sync_id, to_status, trigger=trigger)

    monkeypatch.setattr(
        "local_api.sync_engine.transition_sync_state",
        _tracked_transition,
    )

    sync = _sync("task_engine_order_check")
    transition_sync_state(sync["sync_id"], "in_progress", trigger="engine")

    engine = SyncEngine(adapters=_adapters())
    engine._adapter_push_cycle([get_sync_state(sync["sync_id"])])

    # Verify: metadata written BEFORE synced transition
    assert "update_sync_state" in write_order
    synced_idx = write_order.index("transition_to_synced")
    update_idx = write_order.index("update_sync_state")
    assert update_idx < synced_idx, (
        f"Expected update_sync_state before transition_to_synced, got: {write_order}"
    )

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    assert state["payload_hash"] is not None
    assert state["external_id"] is not None


# ── Phase 24 — Apple Calendar sync state consistency regression ───────────


def test_pending_no_time_at_push_goes_failed_permanent_without_adapter_call():
    """Pending apple_calendar record whose task loses time before push must fail.

    When the eligibility recheck inside _adapter_push_cycle finds the task
    no longer has start_time/due_time, the engine must:
    - NOT call adapter.push
    - transition sync_state to failed_permanent
    - leave external_id, payload_hash, last_synced_at unset
    - log a failed sync_log with error_code=missing_time
    """

    class CountingCalendarAdapter(MockAppleAdapter):
        def __init__(self):
            self.push_count = 0

        def push(self, task_data: dict, sync_state: dict):
            self.push_count += 1
            return super().push(task_data, sync_state)

    sync = _sync("task_engine_no_time_push")
    # Mutate task to remove time fields AFTER sync_state creation but
    # BEFORE scan — simulates task becoming no-time between enqueue and push.
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET start_time = NULL, due_time = NULL WHERE task_id = ?",
        (sync["task_id"],),
    )
    conn.commit()

    adapter = CountingCalendarAdapter()
    result = SyncEngine(adapters=[adapter]).scan_once()

    assert result.pending_picked == 1
    assert adapter.push_count == 0

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed_permanent"
    assert state["external_id"] is None
    assert state["payload_hash"] is None
    assert state["last_synced_at"] is None

    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "failed"
    assert logs[0]["error_code"] == "missing_time"


# ── Phase 30 — Calendar success requires durable external_id ──────────────


class MockSuccessNoExternalIdAdapter(MockAppleAdapter):
    """Adapter that returns success=True but external_id=None (simulating a
    buggy or misconfigured adapter that reports success without a durable id)."""

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=True,
            external_id=None,
            sync_result="success",
        )


def test_queued_new_calendar_success_without_external_id_fails_permanent():
    """Queued engine: success without external_id for new/pending Calendar
    does not mark synced; transitions failed_permanent; failed log
    missing_external_id; no payload_hash; no last_synced_at."""
    sync = _sync("task_engine_p30_noext")

    engine = SyncEngine(adapters=[MockSuccessNoExternalIdAdapter()])
    result = engine.scan_once()

    assert result.pending_picked == 1

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed_permanent"
    assert state["external_id"] is None
    assert state["payload_hash"] is None
    assert state["last_synced_at"] is None

    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "failed"
    assert logs[0]["error_code"] == "missing_external_id"
    assert logs[0]["triggered_by"] == "sync_engine"
    # Success log fields must not be present
    assert logs[0].get("payload_hash_before") is None
    assert logs[0].get("payload_hash_after") is None
    assert logs[0].get("external_id_before") is None
    assert logs[0].get("external_id_after") is None


def test_queued_stale_update_existing_external_id_preserved_when_adapter_returns_none():
    """Queued engine: stale/update path with existing external_id and adapter
    success external_id=None preserves existing external_id; marks synced;
    success log external_id_after is existing id."""
    sync = _sync("task_engine_p30_stale_preserve", status="synced")
    update_sync_state(
        sync["sync_id"],
        external_id="ext_engine_p30_preserve",
        payload_hash="old_hash_engine_p30",
    )
    transition_sync_state(sync["sync_id"], "stale", trigger="trigger")

    engine = SyncEngine(adapters=[MockSuccessNoExternalIdAdapter()])

    # First scan: stale → pending
    stale_result = engine.scan_once()
    assert stale_result.stale_triggered == 1

    # Second scan: pending → push → synced (with preserved external_id)
    push_result = engine.scan_once()
    assert push_result.pending_picked == 1

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    assert state["external_id"] == "ext_engine_p30_preserve"
    assert state["payload_hash"] is not None
    assert state["last_synced_at"] is not None

    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "success"
    assert logs[0]["external_id_before"] == "ext_engine_p30_preserve"
    assert logs[0]["external_id_after"] == "ext_engine_p30_preserve"
    assert logs[0]["triggered_by"] == "sync_engine"


def test_queued_missing_external_id_uses_max_plus_one_attempt():
    """Queued missing_external_id log must use max(sync_attempt)+1 semantics."""
    sync = _sync("task_engine_p30_attempt")
    create_sync_log(
        sync_id=sync["sync_id"],
        local_task_id=sync["task_id"],
        sync_target=sync["sync_target"],
        sync_attempt=2,
        sync_result="failed",
        error_code="previous_failure",
        triggered_by="test",
    )

    engine = SyncEngine(adapters=[MockSuccessNoExternalIdAdapter()])
    engine.scan_once()

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed_permanent"

    logs = _logs(sync["sync_id"])
    assert [log["sync_attempt"] for log in logs] == [3, 2]
    assert logs[0]["error_code"] == "missing_external_id"


# ── Phase 31 — Success finalization consistency ────────────────────────────


def test_success_log_finalization_failure_does_not_leave_synced(monkeypatch):
    """Queued engine: if success log creation raises during adapter push
    cycle, sync_state must not be synced, no success log must exist,
    and a failure log with 'success_finalize_failed' must be written."""

    sync = _sync("task_engine_p31_finalize_fail")

    import local_api.sync_engine

    real_create = local_api.sync_engine.create_sync_log

    def _failing_create(*args, **kwargs):
        if kwargs.get("sync_result") == "success":
            raise RuntimeError("Simulated log write failure")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(
        local_api.sync_engine, "create_sync_log", _failing_create
    )

    engine = SyncEngine(adapters=_adapters())
    result = engine.scan_once()

    assert result.pending_picked == 1

    # State must NOT be synced
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed", (
        f"Expected 'failed', got '{state['sync_status']}'"
    )

    # No success log must exist
    logs = _logs(sync["sync_id"])
    success_logs = [log for log in logs if log["sync_result"] == "success"]
    assert len(success_logs) == 0, (
        "Success log must not exist when finalization fails"
    )

    # A failed log with success_finalize_failed must exist
    failed_logs = [log for log in logs if log["sync_result"] == "failed"]
    assert len(failed_logs) >= 1
    assert failed_logs[0]["error_code"] == "success_finalize_failed"
    assert failed_logs[0]["triggered_by"] == "sync_engine"


def test_synced_transition_failure_cleans_up_orphaned_success_log(monkeypatch):
    """Queued engine: if transition_sync_state to 'synced' raises after
    metadata + success log have been committed, the engine must:
    - not leave sync_state=synced
    - delete the orphaned success log (no success log remains)
    - write a failed log with error_code='success_finalize_failed'
    - leave state as failed
    """
    sync = _sync("task_engine_p31_transition_fail")

    import local_api.sync_engine

    real_transition = local_api.sync_engine.transition_sync_state

    def _failing_transition(sync_id, to_status, *, trigger="engine"):
        if to_status == "synced":
            raise ValueError("Simulated synced transition failure")
        return real_transition(sync_id, to_status, trigger=trigger)

    monkeypatch.setattr(
        local_api.sync_engine, "transition_sync_state", _failing_transition
    )

    engine = SyncEngine(adapters=_adapters())
    result = engine.scan_once()

    assert result.pending_picked == 1

    # State must NOT be synced
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] != "synced", (
        f"Expected non-synced state, got '{state['sync_status']}'"
    )

    # No success log must remain (orphaned log was cleaned up)
    logs = _logs(sync["sync_id"])
    success_logs = [log for log in logs if log["sync_result"] == "success"]
    assert len(success_logs) == 0, (
        "Orphaned success log must be deleted when synced "
        "transition fails"
    )

    # A failed log with success_finalize_failed must exist
    failed_logs = [log for log in logs if log["sync_result"] == "failed"]
    assert len(failed_logs) >= 1
    assert failed_logs[0]["error_code"] == "success_finalize_failed"
    assert failed_logs[0]["triggered_by"] == "sync_engine"


def test_synced_transition_failure_cleanup_delete_itself_fails(monkeypatch):
    """Queued engine: if the orphaned success log cleanup DELETE itself
    fails, the engine must NOT claim consistency is restored:
    - error_code must be 'orphan_success_log_cleanup_failed'
    - state must not be synced
    - no success log may remain? (Not guaranteed — cleanup failed.)
    """
    sync = _sync("task_engine_p31_cleanup_fail")

    import local_api.sync_engine

    # Mock transition_sync_state to raise on synced (same as existing test)
    real_transition = local_api.sync_engine.transition_sync_state

    def _failing_transition(sync_id, to_status, *, trigger="engine"):
        if to_status == "synced":
            raise ValueError("Simulated synced transition failure")
        return real_transition(sync_id, to_status, trigger=trigger)

    monkeypatch.setattr(
        local_api.sync_engine, "transition_sync_state", _failing_transition
    )

    # Make the cleanup DELETE fail — patch the standalone helper
    # _delete_success_log instead of sqlite3.Connection.execute
    # (which is read-only in C and cannot be monkeypatched).
    def _failing_delete(sync_id, attempt):
        raise RuntimeError("Simulated cleanup DELETE failure")

    monkeypatch.setattr(
        local_api.sync_engine, "_delete_success_log", _failing_delete
    )

    engine = SyncEngine(adapters=_adapters())
    result = engine.scan_once()

    assert result.pending_picked == 1

    # State must NOT be synced
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] != "synced", (
        f"Expected non-synced state, got '{state['sync_status']}'"
    )

    # A failed log with orphan_success_log_cleanup_failed must exist
    logs = _logs(sync["sync_id"])
    failed_logs = [log for log in logs if log["sync_result"] == "failed"]
    assert len(failed_logs) >= 1
    assert (
        failed_logs[0]["error_code"]
        == "orphan_success_log_cleanup_failed"
    ), (
        f"Expected 'orphan_success_log_cleanup_failed', "
        f"got '{failed_logs[0]['error_code']}'"
    )
    assert failed_logs[0]["triggered_by"] == "sync_engine"


def test_queued_success_finalization_persists_consistent_state():
    """A healthy queued success path must persist:
    - sync_state.sync_status == 'synced'
    - non-empty external_id
    - current payload_hash (64-char SHA-256)
    - refreshed last_synced_at
    - exactly one success sync_log
    """
    sync = _sync("task_engine_p31_healthy")
    engine = SyncEngine(adapters=_adapters())

    result = engine.scan_once()

    assert result.pending_picked == 1

    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "synced"
    assert state["external_id"] is not None
    assert state["payload_hash"] is not None
    assert len(state["payload_hash"]) == 64
    assert state["last_synced_at"] is not None

    # Verify payload_hash matches current task data
    conn = get_db()
    task_row = conn.execute(
        "SELECT * FROM tasks WHERE task_id = ?", (sync["task_id"],)
    ).fetchone()
    expected_hash = compute_task_payload_hash(dict(task_row))
    assert state["payload_hash"] == expected_hash

    # Exactly one success log
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["sync_result"] == "success"
    assert logs[0]["triggered_by"] == "sync_engine"
