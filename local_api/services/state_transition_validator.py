"""M2 v4 sync state transition validator.

Pure transition rules extracted from local_api/docs/m2-state-machine-design.md.
"""

from __future__ import annotations


STATE_ENGINE_LOCKED = frozenset({
    "in_progress",
    "failed_permanent",
    "orphaned",
    "disabled",
    "deleted",
})

TERMINAL_STATES = frozenset({
    "failed_permanent",
    "orphaned",
    "disabled",
    "deleted",
})

_STATES = frozenset({
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
})

_ENGINE_TRANSITIONS = {
    "pending": {"in_progress"},
    "in_progress": {"synced", "failed", "failed_permanent"},
    "failed": {"in_progress", "failed_permanent"},
    "stale": {"pending"},
}

_MANUAL_TRANSITIONS = {
    "pending": {"skipped", "orphaned", "disabled", "deleted"},
    "in_progress": {"orphaned", "disabled", "deleted"},
    "synced": {"orphaned", "disabled", "deleted"},
    "failed": {"orphaned", "disabled", "deleted"},
    "failed_permanent": {"pending", "orphaned", "disabled", "deleted"},
    "skipped": {"pending", "orphaned", "disabled", "deleted"},
    "stale": {"orphaned", "disabled", "deleted"},
    "orphaned": {"disabled", "deleted"},
    "disabled": {"pending", "orphaned", "deleted"},
    "deleted": set(),
}

_TRIGGER_TRANSITIONS = {
    "synced": {"stale"},
}

_TRANSITIONS_BY_TRIGGER = {
    "engine": _ENGINE_TRANSITIONS,
    "manual": _MANUAL_TRANSITIONS,
    "trigger": _TRIGGER_TRANSITIONS,
}


def is_idempotent(from_status: str, to_status: str) -> bool:
    """Return True when the transition is X->X."""
    return from_status == to_status


def is_terminal(status: str) -> bool:
    """Return True for states the engine never auto-advances."""
    return status in TERMINAL_STATES


def get_engine_locked_states() -> frozenset[str]:
    """Return states the scanner will not pick up for auto-advancement."""
    return STATE_ENGINE_LOCKED


def get_allowed_transitions(trigger: str = "engine") -> dict[str, set[str]]:
    """Return a copy of allowed non-idempotent transitions for a trigger."""
    transitions = _TRANSITIONS_BY_TRIGGER.get(trigger)
    if transitions is None:
        return {}
    return {state: set(targets) for state, targets in transitions.items()}


def is_valid_transition(
    from_status: str,
    to_status: str,
    *,
    trigger: str = "engine",
    has_external_id: bool = False,
) -> bool:
    """Validate an M2 v4 transition matrix edge."""
    if from_status not in _STATES or to_status not in _STATES:
        return False

    if is_idempotent(from_status, to_status):
        return True

    transitions = _TRANSITIONS_BY_TRIGGER.get(trigger)
    if transitions is None:
        return False

    if to_status not in transitions.get(from_status, set()):
        return False

    if from_status == "pending" and to_status == "orphaned":
        return has_external_id

    return True


def validate_transition_or_raise(
    from_status: str,
    to_status: str,
    *,
    trigger: str = "engine",
    has_external_id: bool = False,
) -> None:
    """Raise ValueError when a transition is illegal."""
    if not is_valid_transition(
        from_status,
        to_status,
        trigger=trigger,
        has_external_id=has_external_id,
    ):
        raise ValueError(
            f"Cannot transition from '{from_status}' to '{to_status}' via '{trigger}' trigger"
        )
