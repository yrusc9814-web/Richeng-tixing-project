"""Phase 22 — Calendar sync eligibility guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SyncEligibilityResult:
    allowed: bool
    reason: str
    message: str


_ALLOWED_TARGET = "apple_calendar"
_DEFAULT_TZ = "Asia/Shanghai"


def eligible_for_calendar_sync(task: dict[str, Any], target: str) -> SyncEligibilityResult:
    """Return whether a task may be pushed to Apple Calendar.

    This is a pre-write safety gate. It must run before sync_state creation and
    again before processing pending sync_state records.
    """
    if target != _ALLOWED_TARGET:
        return _blocked("unsupported_target", "Only apple_calendar sync is allowed")

    if not _truthy(task.get("sync_enabled")):
        return _blocked("sync_disabled", "Task has not enabled Calendar sync")

    targets = _parse_sync_targets(task.get("sync_targets"))
    if _ALLOWED_TARGET not in targets:
        return _blocked("target_not_enabled", "Task sync_targets does not include apple_calendar")

    if str(task.get("status") or "").lower() != "pending":
        return _blocked("invalid_task_status", "Only pending tasks may be synced")

    start_raw = task.get("start_time")
    due_raw = task.get("due_time")
    if not start_raw and not due_raw:
        return _blocked("missing_time", "Task must have start_time or due_time")

    tz = _timezone(task.get("timezone"))
    start_dt = _parse_datetime(start_raw, tz) if start_raw else None
    due_dt = _parse_datetime(due_raw, tz) if due_raw else None
    if start_dt is None and start_raw:
        return _blocked("invalid_start_time", "start_time is not a valid ISO datetime")
    if due_dt is None and due_raw:
        return _blocked("invalid_due_time", "due_time is not a valid ISO datetime")
    if start_dt is not None and due_dt is not None and due_dt <= start_dt:
        return _blocked("invalid_time_window", "due_time must be after start_time")

    return SyncEligibilityResult(True, "eligible", "Task is eligible for Apple Calendar sync")


def _blocked(reason: str, message: str) -> SyncEligibilityResult:
    return SyncEligibilityResult(False, reason, message)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _parse_sync_targets(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return set()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {stripped}
        value = parsed
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return {str(value)}


def _timezone(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or _DEFAULT_TZ))
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def _parse_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    try:
        normalized = str(value).strip().replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed
    except Exception:
        return None
