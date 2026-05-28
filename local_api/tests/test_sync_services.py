"""Phase 4.4 — Tests for sync services (sync state + sync log CRUD).

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_services.py -v
"""

import pytest

from local_api.database import reset_db, init_db, get_db
from local_api.services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    get_sync_state_by_key,
    list_sync_states,
    update_sync_state,
    delete_sync_state,
    _build_sync_key,
)
from local_api.services.sync_log_service import (
    create_sync_log,
    get_sync_log,
    list_sync_logs,
    delete_logs_by_sync_id,
)
from local_api.sync_client.payload import (
    compute_payload_hash,
    extract_task_payload,
)


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the database before each test."""
    reset_db()
    init_db()
    yield
    reset_db()


def _insert_test_task(task_id="task_test_001"):
    """Insert a minimal test task into the database."""
    conn = get_db()
    conn.execute(
        """INSERT INTO tasks (task_id, title, status, priority, created_channel, created_at, updated_at)
           VALUES (?, ?, 'pending', 'P2', 'api_test', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
        (task_id, f"Test task {task_id}"),
    )
    conn.commit()


# ── Sync State Service Tests ────────────────────────────────────────────────


class TestSyncStateService:
    """Unit tests for sync_state_service functions."""

    def test_create_sync_state(self):
        _insert_test_task("task_ss_01")
        record = create_sync_state(
            task_id="task_ss_01",
            sync_target="apple_calendar",
            sync_status="pending",
        )
        assert record["task_id"] == "task_ss_01"
        assert record["sync_target"] == "apple_calendar"
        assert record["sync_status"] == "pending"
        assert record["sync_version"] == 1
        assert record["sync_key"] == "task_ss_01:apple_calendar"
        assert record["sync_id"].startswith("sync_")

    def test_create_sync_state_duplicate_key(self):
        _insert_test_task("task_ss_02")
        create_sync_state(task_id="task_ss_02", sync_target="apple_calendar")
        with pytest.raises(ValueError, match="already exists"):
            create_sync_state(task_id="task_ss_02", sync_target="apple_calendar")

    def test_create_sync_state_invalid_target(self):
        _insert_test_task("task_ss_03")
        with pytest.raises(ValueError, match="Invalid sync_target"):
            create_sync_state(task_id="task_ss_03", sync_target="invalid_target")

    def test_create_sync_state_invalid_status(self):
        _insert_test_task("task_ss_04")
        with pytest.raises(ValueError, match="Invalid sync_status"):
            create_sync_state(
                task_id="task_ss_04",
                sync_target="apple_calendar",
                sync_status="invalid_status",
            )

    def test_create_sync_state_missing_task(self):
        with pytest.raises(ValueError, match="Task not found"):
            create_sync_state(
                task_id="task_missing_001",
                sync_target="apple_calendar",
            )

    def test_get_sync_state(self):
        _insert_test_task("task_ss_05")
        record = create_sync_state(task_id="task_ss_05", sync_target="apple_reminder")
        fetched = get_sync_state(record["sync_id"])
        assert fetched is not None
        assert fetched["sync_id"] == record["sync_id"]

    def test_get_sync_state_not_found(self):
        result = get_sync_state("sync_nonexistent_999")
        assert result is None

    def test_get_sync_state_by_key(self):
        _insert_test_task("task_ss_06")
        create_sync_state(task_id="task_ss_06", sync_target="apple_calendar")
        fetched = get_sync_state_by_key("task_ss_06", "apple_calendar")
        assert fetched is not None
        assert fetched["sync_key"] == "task_ss_06:apple_calendar"

    def test_get_sync_state_by_key_invalid_target(self):
        with pytest.raises(ValueError, match="Invalid sync_target"):
            get_sync_state_by_key("task_ss_06", "google_calendar")

    def test_list_sync_states_all(self):
        _insert_test_task("task_ss_07")
        create_sync_state(task_id="task_ss_07", sync_target="apple_calendar")
        create_sync_state(task_id="task_ss_07", sync_target="apple_reminder")
        records, total = list_sync_states()
        assert total >= 2

    def test_list_sync_states_filtered(self):
        _insert_test_task("task_ss_08")
        create_sync_state(task_id="task_ss_08", sync_target="apple_calendar")
        records, total = list_sync_states(sync_target="apple_calendar")
        assert total >= 1
        for r in records:
            assert r["sync_target"] == "apple_calendar"

    def test_update_sync_state(self):
        _insert_test_task("task_ss_09")
        record = create_sync_state(task_id="task_ss_09", sync_target="apple_calendar")
        updated = update_sync_state(
            record["sync_id"],
            sync_status="synced",
            external_id="ext_999",
        )
        assert updated is not None
        assert updated["sync_status"] == "synced"
        assert updated["external_id"] == "ext_999"

    def test_update_sync_state_not_found(self):
        result = update_sync_state("sync_nonexistent_999", sync_status="synced")
        assert result is None

    def test_delete_sync_state(self):
        _insert_test_task("task_ss_10")
        record = create_sync_state(task_id="task_ss_10", sync_target="apple_calendar")
        deleted = delete_sync_state(record["sync_id"])
        assert deleted is True
        assert get_sync_state(record["sync_id"]) is None

    def test_delete_sync_state_not_found(self):
        result = delete_sync_state("sync_nonexistent_999")
        assert result is False

    def test_build_sync_key(self):
        key = _build_sync_key("task_123", "apple_calendar")
        assert key == "task_123:apple_calendar"
        key2 = _build_sync_key("task_456", "apple_reminder")
        assert key2 == "task_456:apple_reminder"

    def test_all_10_sync_statuses_accepted(self):
        """Verify all 10 sync statuses can be created."""
        statuses = [
            "pending", "in_progress", "synced", "failed", "failed_permanent",
            "skipped", "stale", "orphaned", "disabled", "deleted",
        ]
        for i, status in enumerate(statuses):
            task_id = f"task_status_{i}"
            _insert_test_task(task_id)
            record = create_sync_state(
                task_id=task_id,
                sync_target="apple_calendar",
                sync_status=status,
            )
            assert record["sync_status"] == status


# ── Sync Log Service Tests ──────────────────────────────────────────────────


class TestSyncLogService:
    """Unit tests for sync_log_service functions."""

    def _create_sync_record(self, task_id="task_log_00"):
        _insert_test_task(task_id)
        return create_sync_state(task_id=task_id, sync_target="apple_calendar")

    def test_create_sync_log(self):
        sync = self._create_sync_record("task_log_01")
        record = create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="success",
        )
        assert record["sync_id"] == sync["sync_id"]
        assert record["sync_result"] == "success"
        assert record["sync_attempt"] == 1
        assert record["log_id"].startswith("log_")

    def test_create_sync_log_full(self):
        sync = self._create_sync_record("task_log_02")
        record = create_sync_log(
            sync_id=sync["sync_id"],
            local_task_id="task_log_02",
            sync_target="apple_calendar",
            sync_attempt=2,
            sync_result="drift_detected",
            error_code="DRIFT_001",
            error_message="Title changed",
            drift_detected=True,
            drift_fields="title",
            payload_hash_before="hash_before",
            payload_hash_after="hash_after",
            external_id_before="ext_before",
            external_id_after="ext_after",
            request_id="req_001",
            triggered_by="test",
        )
        assert record["sync_result"] == "drift_detected"
        assert record["drift_detected"] == 1
        assert record["error_code"] == "DRIFT_001"

    def test_create_sync_log_invalid_target(self):
        sync = self._create_sync_record("task_log_03")
        with pytest.raises(ValueError, match="Invalid sync_target"):
            create_sync_log(
                sync_id=sync["sync_id"],
                sync_target="invalid_target",
                sync_result="success",
            )

    def test_create_sync_log_invalid_result(self):
        sync = self._create_sync_record("task_log_04")
        with pytest.raises(ValueError, match="Invalid sync_result"):
            create_sync_log(
                sync_id=sync["sync_id"],
                sync_target="apple_calendar",
                sync_result="invalid_result",
            )

    def test_create_sync_log_missing_sync_id(self):
        with pytest.raises(ValueError, match="Sync state not found"):
            create_sync_log(
                sync_id="sync_missing_001",
                sync_target="apple_calendar",
                sync_result="failed",
            )

    def test_create_sync_log_redacts_error_message(self):
        sync = self._create_sync_record("task_log_redact")
        record = create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="failed",
            error_message="User typed private meeting title",
        )
        assert record["error_message"] == "REDACTED"
        assert "private meeting title" not in str(record)

    def test_get_sync_log(self):
        sync = self._create_sync_record("task_log_05")
        record = create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="success",
        )
        fetched = get_sync_log(record["log_id"])
        assert fetched is not None
        assert fetched["log_id"] == record["log_id"]

    def test_get_sync_log_not_found(self):
        result = get_sync_log("log_nonexistent_999")
        assert result is None

    def test_list_sync_logs(self):
        sync = self._create_sync_record("task_log_06")
        create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="success",
        )
        create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="failed",
        )
        records, total = list_sync_logs()
        assert total >= 2

    def test_list_sync_logs_filtered(self):
        sync = self._create_sync_record("task_log_07")
        create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="success",
        )
        records, total = list_sync_logs(sync_result="success")
        assert total >= 1
        for r in records:
            assert r["sync_result"] == "success"

    def test_delete_logs_by_sync_id(self):
        sync = self._create_sync_record("task_log_08")
        create_sync_log(
            sync_id=sync["sync_id"],
            sync_target="apple_calendar",
            sync_result="success",
        )
        count = delete_logs_by_sync_id(sync["sync_id"])
        assert count >= 1
        # Verify gone
        records, total = list_sync_logs(sync_id=sync["sync_id"])
        assert total == 0


# ── Payload Tests ───────────────────────────────────────────────────────────


class TestPayload:
    """Unit tests for sync_client/payload.py"""

    def test_compute_payload_hash_consistent(self):
        data1 = {"title": "Test", "priority": "P1"}
        data2 = {"priority": "P1", "title": "Test"}
        h1 = compute_payload_hash(data1)
        h2 = compute_payload_hash(data2)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_compute_payload_hash_different(self):
        h1 = compute_payload_hash({"title": "A"})
        h2 = compute_payload_hash({"title": "B"})
        assert h1 != h2

    def test_extract_task_payload(self):
        row = {
            "task_id": "task_001",
            "title": "Meeting",
            "description": "Team standup",
            "priority": "P1",
            "status": "pending",
            "start_time": "2026-06-01T09:00:00",
            "due_time": "2026-06-01T10:00:00",
            "timezone": "Asia/Shanghai",
            "location": "Room 3F",
            "extra_field": "ignored",
        }
        payload = extract_task_payload(row)
        assert payload["task_id"] == "task_001"
        assert payload["title"] == "Meeting"
        assert "extra_field" not in payload
        assert len(payload) == 9
