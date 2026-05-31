"""Phase 22 — Calendar sync eligibility guardrail tests."""

from __future__ import annotations

from local_api.services.sync_eligibility import eligible_for_calendar_sync


def _task(**overrides):
    task = {
        "task_id": "task_guard_001",
        "title": "Guarded task",
        "status": "pending",
        "sync_enabled": 1,
        "sync_targets": '["apple_calendar"]',
        "start_time": "2026-06-01T10:00:00+08:00",
        "due_time": "2026-06-01T10:30:00+08:00",
        "timezone": "Asia/Shanghai",
    }
    task.update(overrides)
    return task


def test_allows_eligible_calendar_task():
    result = eligible_for_calendar_sync(_task(), "apple_calendar")

    assert result.allowed is True
    assert result.reason == "eligible"


def test_blocks_sync_enabled_false():
    result = eligible_for_calendar_sync(_task(sync_enabled=0), "apple_calendar")

    assert result.allowed is False
    assert result.reason == "sync_disabled"


def test_blocks_non_apple_calendar_target():
    result = eligible_for_calendar_sync(_task(), "apple_reminder")

    assert result.allowed is False
    assert result.reason == "unsupported_target"


def test_blocks_no_time_fields():
    result = eligible_for_calendar_sync(_task(start_time=None, due_time=None), "apple_calendar")

    assert result.allowed is False
    assert result.reason == "missing_time"


def test_blocks_due_time_not_after_start_time():
    result = eligible_for_calendar_sync(
        _task(
            start_time="2026-06-01T10:30:00+08:00",
            due_time="2026-06-01T10:00:00+08:00",
        ),
        "apple_calendar",
    )

    assert result.allowed is False
    assert result.reason == "invalid_time_window"


def test_blocks_non_pending_task_status():
    result = eligible_for_calendar_sync(_task(status="completed"), "apple_calendar")

    assert result.allowed is False
    assert result.reason == "invalid_task_status"


def test_requires_sync_targets_to_include_apple_calendar():
    result = eligible_for_calendar_sync(_task(sync_targets='["apple_reminder"]'), "apple_calendar")

    assert result.allowed is False
    assert result.reason == "target_not_enabled"


def test_accepts_only_start_time():
    result = eligible_for_calendar_sync(_task(due_time=None), "apple_calendar")

    assert result.allowed is True
    assert result.reason == "eligible"


def test_accepts_only_due_time():
    result = eligible_for_calendar_sync(_task(start_time=None), "apple_calendar")

    assert result.allowed is True
    assert result.reason == "eligible"
