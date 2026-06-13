"""Phase 43 — Tests for create_task CLI script."""

import pytest
from local_api.database import get_db, reset_db, init_db
from local_api.scripts import create_task


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


def test_create_task_success(capsys):
    rc = create_task.main([
        "--title", "Test Task P43",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
        "--notes", "My notes",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Task created successfully!" in out
    
    # Verify in DB
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks").fetchone()
    assert task["title"] == "Test Task P43"
    assert task["description"] == "My notes"
    
    sync_state = conn.execute("SELECT * FROM sync_state WHERE task_id = ?", (task["task_id"],)).fetchone()
    assert sync_state["sync_target"] == "apple_calendar"
    assert sync_state["sync_status"] == "pending"


def test_create_task_empty_title(capsys):
    rc = create_task.main([
        "--title", "  ",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--title cannot be empty" in err


def test_create_task_invalid_date(capsys):
    rc = create_task.main([
        "--title", "Test",
        "--start", "not-a-date",
        "--end", "2026-06-13T11:00:00",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Invalid date format" in err


def test_create_task_end_before_start(capsys):
    rc = create_task.main([
        "--title", "Test",
        "--start", "2026-06-13T11:00:00",
        "--end", "2026-06-13T10:00:00",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--end must be strictly after --start" in err
