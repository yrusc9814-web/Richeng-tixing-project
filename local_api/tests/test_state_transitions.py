"""Phase 6 tests for the M2 v4 sync transition validator."""

import pytest

from local_api.services.state_transition_validator import (
    get_allowed_transitions,
    get_engine_locked_states,
    is_idempotent,
    is_terminal,
    is_valid_transition,
    validate_transition_or_raise,
)


STATES = [
    "pending",
    "in_progress",
    "synced",
    "failed",
    "failed_permanent",
    "skipped",
    "stale",
    "orphaned",
    "disabled",
    "deleted",
]

ENGINE_PATHS = [
    ("pending", "in_progress"),
    ("in_progress", "synced"),
    ("in_progress", "failed"),
    ("in_progress", "failed_permanent"),
    ("failed", "in_progress"),
    ("failed", "failed_permanent"),
    ("stale", "pending"),
]

MANUAL_PATHS = [
    ("pending", "skipped"),
    ("pending", "orphaned"),
    ("pending", "disabled"),
    ("pending", "deleted"),
    ("in_progress", "orphaned"),
    ("in_progress", "disabled"),
    ("in_progress", "deleted"),
    ("synced", "orphaned"),
    ("synced", "disabled"),
    ("synced", "deleted"),
    ("failed", "orphaned"),
    ("failed", "disabled"),
    ("failed", "deleted"),
    ("failed_permanent", "pending"),
    ("failed_permanent", "orphaned"),
    ("failed_permanent", "disabled"),
    ("failed_permanent", "deleted"),
    ("skipped", "pending"),
    ("skipped", "orphaned"),
    ("skipped", "disabled"),
    ("skipped", "deleted"),
    ("stale", "orphaned"),
    ("stale", "disabled"),
    ("stale", "deleted"),
    ("orphaned", "disabled"),
    ("orphaned", "deleted"),
    ("disabled", "pending"),
    ("disabled", "orphaned"),
    ("disabled", "deleted"),
]


@pytest.mark.parametrize(("from_status", "to_status"), ENGINE_PATHS)
def test_engine_paths(from_status, to_status):
    assert is_valid_transition(from_status, to_status, trigger="engine")


@pytest.mark.parametrize(("from_status", "to_status"), MANUAL_PATHS)
def test_manual_paths(from_status, to_status):
    assert is_valid_transition(
        from_status,
        to_status,
        trigger="manual",
        has_external_id=True,
    )


def test_trigger_synced_to_stale():
    assert is_valid_transition("synced", "stale", trigger="trigger")


@pytest.mark.parametrize("status", STATES)
def test_idempotent_paths(status):
    assert is_idempotent(status, status)
    assert is_valid_transition(status, status, trigger="engine")
    assert is_valid_transition(status, status, trigger="manual")
    assert is_valid_transition(status, status, trigger="trigger")


@pytest.mark.parametrize(
    ("from_status", "to_status", "trigger"),
    [
        ("deleted", "pending", "manual"),
        ("deleted", "disabled", "manual"),
        ("pending", "synced", "engine"),
        ("pending", "failed", "engine"),
        ("in_progress", "pending", "manual"),
        ("in_progress", "stale", "engine"),
        ("synced", "pending", "trigger"),
        ("failed_permanent", "in_progress", "engine"),
        ("skipped", "in_progress", "engine"),
        ("stale", "in_progress", "engine"),
        ("orphaned", "pending", "manual"),
        ("orphaned", "deleted", "engine"),
        ("disabled", "failed", "manual"),
    ],
)
def test_illegal_transitions_grouped(from_status, to_status, trigger):
    assert not is_valid_transition(from_status, to_status, trigger=trigger)
    with pytest.raises(
        ValueError,
        match=f"Cannot transition from '{from_status}' to '{to_status}' via '{trigger}' trigger",
    ):
        validate_transition_or_raise(from_status, to_status, trigger=trigger)


def test_pending_to_orphaned_requires_external_id():
    assert not is_valid_transition("pending", "orphaned", trigger="manual")
    assert is_valid_transition(
        "pending",
        "orphaned",
        trigger="manual",
        has_external_id=True,
    )


def test_public_transition_sets_are_copies():
    engine = get_allowed_transitions("engine")
    manual = get_allowed_transitions("manual")
    trigger = get_allowed_transitions("trigger")

    assert engine["pending"] == {"in_progress"}
    assert manual["disabled"] == {"pending", "orphaned", "deleted"}
    assert trigger["synced"] == {"stale"}

    engine["pending"].add("deleted")
    assert get_allowed_transitions("engine")["pending"] == {"in_progress"}


def test_terminal_and_locked_sets():
    assert get_engine_locked_states() == frozenset({
        "in_progress",
        "failed_permanent",
        "orphaned",
        "disabled",
        "deleted",
    })
    assert is_terminal("failed_permanent")
    assert is_terminal("orphaned")
    assert not is_terminal("synced")
    assert not is_terminal("skipped")


# ─── started_at / locked_at field tests ────────────────────────────────
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from local_api.database import get_db, init_db, reset_db
from local_api.services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    transition_sync_state,
)
from local_api.sync_engine import SyncEngine, _now, _parse_iso
from local_api import config


@pytest.fixture
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


def _insert_task(task_id: str) -> None:
    conn = get_db()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, created_channel, created_at, updated_at
        ) VALUES (?, ?, 'pending', 'P2', 'api_test', ?, ?)""",
        (task_id, f"Task {task_id}", now, now),
    )
    conn.commit()


def _insert_sync_state(task_id: str, status: str = "pending") -> dict:
    _insert_task(task_id)
    return create_sync_state(task_id=task_id, sync_target="apple_calendar", sync_status=status)


@pytest.mark.usefixtures("clean_db")
class TestSyncStateTimestamps:
    """Verify started_at / locked_at behaviour during state transitions."""

    def test_pending_to_in_progress_sets_started_at(self):
        """pending → in_progress should set both started_at and locked_at."""
        state = _insert_sync_state("tsst_01", status="pending")

        # Initially both fields are None
        assert state["started_at"] is None
        assert state["locked_at"] is None

        # Transition to in_progress
        result = transition_sync_state(state["sync_id"], "in_progress", trigger="engine")

        assert result["started_at"] is not None, "started_at should be set"
        assert result["locked_at"] is not None, "locked_at should be set"
        assert result["sync_status"] == "in_progress"

    def test_multiple_in_progress_keeps_started_at_but_updates_locked_at(self):
        """Entering in_progress from failed should NOT change started_at,
        but should update locked_at."""
        state = _insert_sync_state("tsst_02", status="pending")

        # First entry → pending → in_progress
        first = transition_sync_state(state["sync_id"], "in_progress", trigger="engine")
        original_started = first["started_at"]
        original_locked = first["locked_at"]

        # Move to failed (simulate timeout)
        transition_sync_state(state["sync_id"], "failed", trigger="engine")

        # Re-enter in_progress from failed
        reentered = transition_sync_state(state["sync_id"], "in_progress", trigger="engine")

        # started_at must NOT have changed
        assert reentered["started_at"] == original_started, \
            "started_at must not change on re-entry to in_progress"

        # locked_at must have been updated
        assert reentered["locked_at"] != original_locked, \
            "locked_at should be refreshed on every entry to in_progress"
        assert reentered["locked_at"] is not None

    def test_timeout_fallback_to_updated_at_when_started_at_missing(self):
        """_handle_timeouts should fall back to updated_at when started_at is None."""
        state = _insert_sync_state("tsst_03", status="pending")

        # Move to in_progress but then NULL out started_at to simulate legacy record
        first = transition_sync_state(state["sync_id"], "in_progress", trigger="engine")
        sync_id = first["sync_id"]

        conn = get_db()
        old_updated = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(hours=2)).isoformat()
        conn.execute(
            "UPDATE sync_state SET started_at = NULL, updated_at = ? WHERE sync_id = ?",
            (old_updated, sync_id),
        )
        conn.commit()

        engine = SyncEngine()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(config, "SYNC_TIMEOUT_SECONDS", 0)  # force immediate timeout
        try:
            result = engine.scan_once()
        finally:
            monkeypatch.undo()

        # The task should have been timed out (fallback to updated_at)
        assert result.timeout_detected >= 1, \
            "Should detect timeout via updated_at fallback when started_at is None"
