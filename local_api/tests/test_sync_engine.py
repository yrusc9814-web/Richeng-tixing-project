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
    """apple_reminder stale/pending must never route through the Calendar adapter."""

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

    # First scan: stale → pending
    stale_result = engine.scan_once()
    assert stale_result.stale_triggered == 1

    # Second scan: pending — but reminder has no adapter → failed_permanent
    push_result = engine.scan_once()
    assert push_result.pending_picked == 1

    assert adapter.push_count == 0
    state = get_sync_state(sync["sync_id"])
    assert state["sync_status"] == "failed_permanent"
    logs = _logs(sync["sync_id"])
    assert len(logs) == 1
    assert logs[0]["error_code"] == "adapter_not_found"


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
