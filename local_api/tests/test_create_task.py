"""Phase 44 — Tests for create_task CLI script."""

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
        "--title", "Test Task P44",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
        "--notes", "My notes",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "任务已成功创建！" in out
    assert "任务 ID (task_id) :" in out
    assert "标题              : Test Task P44" in out
    assert "开始时间          : 2026-06-13T10:00:00" in out
    assert "结束时间          : 2026-06-13T11:00:00" in out
    assert "目标日历          : apple_calendar" in out
    assert "等待同步 (pending)" in out
    assert "系统会在后台自动同步" in out
    
    # Verify in DB
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks").fetchone()
    assert task["title"] == "Test Task P44"
    assert task["description"] == "My notes"
    
    sync_state = conn.execute("SELECT * FROM sync_state WHERE task_id = ?", (task["task_id"],)).fetchone()
    assert sync_state["sync_target"] == "apple_calendar"
    assert sync_state["sync_status"] == "pending"


def test_create_task_success_without_notes(capsys):
    rc = create_task.main([
        "--title", "Task No Notes",
        "--start", "2026-06-14T10:00:00",
        "--end", "2026-06-14T11:00:00",
    ])
    assert rc == 0
    
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks").fetchone()
    assert task["title"] == "Task No Notes"
    assert task["description"] is None


def test_create_task_empty_title(capsys):
    rc = create_task.main([
        "--title", "  ",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "任务标题(--title)不能为空" in err


def test_create_task_invalid_date(capsys):
    rc = create_task.main([
        "--title", "Test",
        "--start", "not-a-date",
        "--end", "2026-06-13T11:00:00",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "时间格式无效" in err


def test_create_task_end_before_start(capsys):
    rc = create_task.main([
        "--title", "Test",
        "--start", "2026-06-13T11:00:00",
        "--end", "2026-06-13T10:00:00",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "结束时间(--end)必须晚于" in err


def test_create_task_unknown_target(capsys):
    rc = create_task.main([
        "--title", "Test Target",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
        "--target", "google_calendar",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "不支持的同步目标" in err


def test_create_task_with_sync(monkeypatch, capsys):
    # Mock the sync execution
    def mock_build_services(dry_run, explicit_target):
        class MockScheduler:
            def sync_task(self, task_id, target):
                conn = get_db()
                conn.execute("UPDATE sync_state SET sync_status='synced', external_id='ext-123' WHERE task_id=?", (task_id,))
                conn.commit()
                return {"success": True, "status": "synced"}
        return None, MockScheduler()

    monkeypatch.setattr("local_api.scripts.sync_trigger.build_services", mock_build_services)

    rc = create_task.main([
        "--title", "Test Sync P45",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
        "--sync",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "初始同步状态      : 等待同步 (pending)" in out
    assert "正在立即同步到 Apple Calendar..." in out
    assert "最终同步状态      : synced" in out
    assert "日历外部 ID       : ext-123" in out

def test_create_task_with_sync_failure(monkeypatch, capsys):
    # Mock the sync execution to fail
    def mock_build_services(dry_run, explicit_target):
        class MockScheduler:
            def sync_task(self, task_id, target):
                conn = get_db()
                conn.execute("UPDATE sync_state SET sync_status='failed' WHERE task_id=?", (task_id,))
                conn.commit()
                return {"success": False, "status": "failed", "error": "Mock sync error"}
        return None, MockScheduler()

    monkeypatch.setattr("local_api.scripts.sync_trigger.build_services", mock_build_services)

    rc = create_task.main([
        "--title", "Test Sync Failure P45",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
        "--sync",
    ])
    assert rc == 1
    outerr = capsys.readouterr()
    assert "初始同步状态      : 等待同步 (pending)" in outerr.out
    assert "最终同步状态      : failed" in outerr.out
    assert "同步失败: Mock sync error" in outerr.err

def test_create_task_sync_skipped_on_validation_failure(capsys):
    rc = create_task.main([
        "--title", "  ",
        "--start", "2026-06-13T10:00:00",
        "--end", "2026-06-13T11:00:00",
        "--sync",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "任务标题(--title)不能为空" in err
    
    # Sync shouldn't be triggered or checked
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks").fetchone()
    assert task is None

