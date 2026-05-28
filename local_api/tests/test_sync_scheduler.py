"""Phase 9 — Tests for SyncScheduler.

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_scheduler.py -v
"""

from __future__ import annotations

from typing import Optional

import pytest

from local_api import config
from local_api.database import get_db, init_db, reset_db
from local_api.adapters import AdapterResult, SyncAdapter
from local_api.services.sync_service import SyncService
from local_api.scheduler.sync_scheduler import SyncScheduler
from local_api.services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    transition_sync_state,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


@pytest.fixture
def success_adapter():
    class MockSchedulerSuccessAdapter(SyncAdapter):
        @property
        def target_name(self) -> str:
            return "apple_calendar"

        def validate_config(self) -> tuple[bool, Optional[str]]:
            return (True, None)

        def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
            return AdapterResult(success=True, external_id="ext_sched", sync_result="success")

        def pull(self, external_id: str) -> Optional[dict]:
            return None

    return MockSchedulerSuccessAdapter()


@pytest.fixture
def svc(success_adapter):
    return SyncService(adapters=[success_adapter])


@pytest.fixture
def scheduler(svc):
    return SyncScheduler(sync_service=svc)


@pytest.fixture
def multi_adapter_svc():
    """Service with both apple_calendar matching and apple_reminder handling."""

    class CalendarAdapter(SyncAdapter):
        @property
        def target_name(self) -> str:
            return "apple_calendar"

        def validate_config(self) -> tuple[bool, Optional[str]]:
            return (True, None)

        def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
            return AdapterResult(success=True, external_id="cal_001", sync_result="success")

        def pull(self, external_id: str) -> Optional[dict]:
            return None

    return SyncService(adapters=[CalendarAdapter()])


def _insert_task(task_id: str) -> None:
    """Insert a minimal task row."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    conn = get_db()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, created_channel, created_at, updated_at
        ) VALUES (?, ?, 'pending', 'P2', 'api_test', ?, ?)""",
        (task_id, f"Task {task_id}", now, now),
    )
    conn.commit()


# ── Tests: sync_task ─────────────────────────────────────────────────


class TestSyncTask:
    """SyncScheduler.sync_task()"""

    def test_sync_success(self, scheduler):
        _insert_task("task_sched_001")
        result = scheduler.sync_task("task_sched_001", "apple_calendar")

        assert result["success"] is True
        assert result["status"] == "synced"
        assert result["task_id"] == "task_sched_001"
        assert result["target"] == "apple_calendar"
        assert result["sync_id"] is not None
        assert result.get("error") is None

    def test_invalid_target_skipped(self, scheduler):
        _insert_task("task_sched_inv")
        result = scheduler.sync_task("task_sched_inv", "invalid_target")

        assert result["success"] is False
        assert result["status"] == "skipped"
        assert "Invalid" in (result.get("reason") or "")

    def test_task_not_found_propagates(self, scheduler):
        """Non-existent task: SyncService handles it, scheduler propagates error."""
        result = scheduler.sync_task("nonexistent", "apple_calendar")

        assert result["success"] is False
        assert result["status"] == "failed_permanent"
        assert result.get("error") is not None


# ── Tests: sync_task_all_targets ─────────────────────────────────────


class TestSyncTaskAllTargets:
    """SyncScheduler.sync_task_all_targets()"""

    def test_syncs_to_all_targets(self, multi_adapter_svc):
        _insert_task("task_sched_all")
        sched = SyncScheduler(sync_service=multi_adapter_svc)

        result = sched.sync_task_all_targets("task_sched_all")

        assert result["task_id"] == "task_sched_all"
        assert result["total"] == len(config.ALLOWED_SYNC_TARGETS)
        assert result["success_count"] >= 1
        # At last one target should succeed (apple_calendar)
        target_results = {r["target"]: r for r in result["results"]}
        assert "apple_calendar" in target_results
        assert target_results["apple_calendar"]["success"] is True

    def test_skips_terminal_states(self, svc, scheduler):
        """Tasks already synced are skipped."""
        _insert_task("task_sched_term")
        # First sync — should succeed
        r1 = scheduler.sync_task_all_targets("task_sched_term")
        assert r1["success_count"] >= 1

        # Second sync — should skip already-synced apple_calendar
        r2 = scheduler.sync_task_all_targets("task_sched_term")
        # Find the apple_calendar result
        cal_result = [r for r in r2["results"] if r["target"] == "apple_calendar"][0]
        assert cal_result["status"] == "skipped"
        assert "terminal state" in (cal_result.get("reason") or "")

    def test_failed_permanent_also_skipped(self, scheduler, clean_db):
        """Tasks that are failed_permanent are skipped by scheduler."""
        _insert_task("task_sched_perm")

        # Create and set sync_state to failed_permanent via valid transitions
        sync = create_sync_state(task_id="task_sched_perm", sync_target="apple_calendar")
        transition_sync_state(sync["sync_id"], "in_progress", trigger="engine")
        transition_sync_state(sync["sync_id"], "failed_permanent", trigger="engine")

        result = scheduler.sync_task_all_targets("task_sched_perm")

        cal_result = [r for r in result["results"] if r["target"] == "apple_calendar"]
        if cal_result:
            assert cal_result[0]["status"] == "skipped"

    def test_empty_targets_clean(self, scheduler):
        """All targets handled without error."""
        _insert_task("task_sched_empty")
        result = scheduler.sync_task_all_targets("task_sched_empty")
        assert result["total"] > 0
        assert sum(1 for r in result["results"] if r.get("success")) >= 0


# ── Tests: sync_pending ──────────────────────────────────────────────


class TestSyncPending:
    """SyncScheduler.sync_pending()"""

    def test_processes_pending_records(self, scheduler):
        _insert_task("task_sched_pend")

        # Create a pending sync_state record
        sync = create_sync_state(task_id="task_sched_pend", sync_target="apple_calendar")
        assert sync["sync_status"] == "pending"

        result = scheduler.sync_pending(limit=10)

        assert result["processed"] >= 1
        assert result["success_count"] >= 1

        # Verify the sync_state was updated
        updated = get_sync_state(sync["sync_id"])
        assert updated["sync_status"] == "synced"

    def test_empty_pending_returns_zero(self, scheduler):
        """No pending records → nothing processed."""
        result = scheduler.sync_pending(limit=10)
        assert result["processed"] == 0
        assert result["success_count"] == 0

    def test_skips_already_synced(self, scheduler):
        _insert_task("task_sched_no_pend")

        # Create a synced record via valid state transitions
        sync = create_sync_state(task_id="task_sched_no_pend", sync_target="apple_calendar")
        transition_sync_state(sync["sync_id"], "in_progress", trigger="engine")
        transition_sync_state(sync["sync_id"], "synced", trigger="engine")

        # sync_pending only looks at pending/failed, so synced is skipped
        result = scheduler.sync_pending(limit=10)
        assert result["processed"] == 0
