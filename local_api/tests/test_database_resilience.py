"""P1 SQLite resilience tests."""

from __future__ import annotations

import pytest

from local_api.database import get_db, init_db, reset_db
from local_api.services import sync_state_service
from local_api.services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    transition_sync_state,
)


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


def _insert_task(task_id: str = "task_db_resilience") -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, created_channel, created_at, updated_at
        ) VALUES (?, ?, 'pending', 'P2', 'api_test', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
        (task_id, f"Task {task_id}"),
    )
    conn.commit()


def test_sqlite_connection_sets_busy_timeout():
    timeout = get_db().execute("PRAGMA busy_timeout").fetchone()[0]

    assert timeout == 5000


def test_update_write_exception_rolls_back(monkeypatch):
    class FakeCursor:
        def fetchone(self):
            return {
                "sync_id": "sync_fake",
                "sync_status": "pending",
                "external_id": None,
            }

    class FakeConn:
        def __init__(self):
            self.rollback_called = False
            self.commit_called = False
            self.execute_calls = 0

        def execute(self, *_args, **_kwargs):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return FakeCursor()
            raise RuntimeError("write failed")

        def commit(self):
            self.commit_called = True

        def rollback(self):
            self.rollback_called = True

    fake_conn = FakeConn()
    monkeypatch.setattr(sync_state_service, "get_db", lambda: fake_conn)

    with pytest.raises(RuntimeError, match="write failed"):
        sync_state_service.update_sync_state("sync_fake", external_id="ext_failed")

    assert fake_conn.rollback_called is True
    assert fake_conn.commit_called is False


def test_failed_transition_write_keeps_original_state():
    _insert_task()
    state = create_sync_state("task_db_resilience", "apple_calendar")
    conn = get_db()
    conn.execute(
        "CREATE TRIGGER fail_sync_state_update BEFORE UPDATE ON sync_state BEGIN SELECT RAISE(ABORT, 'forced update failure'); END"
    )
    conn.commit()

    with pytest.raises(Exception, match="forced update failure"):
        transition_sync_state(state["sync_id"], "in_progress", trigger="engine")

    conn.execute("DROP TRIGGER fail_sync_state_update")
    conn.commit()
    persisted = get_sync_state(state["sync_id"])
    assert persisted["sync_status"] == "pending"
    assert persisted["started_at"] is None
    assert persisted["locked_at"] is None
