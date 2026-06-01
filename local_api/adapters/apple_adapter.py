"""Phase 8A — Apple Calendar / Reminders adapters.

Contains two adapter implementations:

1. MockAppleAdapter (Phase 8A): Always-succeeds mock for tests.
2. AppleSyncAdapter (Phase 14+18B): Production adapter with safety layer,
   dry-run mode, test markers, platform/error checking, and real Calendar
   writes via macOS EventKit.

AppleSyncAdapter safety properties:
  - dry_run mode: validates config, returns skipped (no external calls)
  - test_mode: simulated push with [SYNC-TEST] title prefix
  - Non-macOS platforms: returns platform_unsupported error
  - Real EventKit push: supports Calendar only; Reminders are explicitly out
    of scope for Phase 18B
  - All failure paths return structured AdapterResult with error_code/error_message
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta
from typing import Optional, cast
from zoneinfo import ZoneInfo

from .base import AdapterResult, SyncAdapter


# ── Mock adapter (backward compat) ───────────────────────────────────────


class MockAppleAdapter(SyncAdapter):
    """Mock adapter for Apple Calendar/Reminders.

    Always succeeds. Generates deterministic external_id from sync_id + target.
    Used by main.py and existing tests for pipeline validation.
    """

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        raw = f"{sync_state['sync_id']}:{sync_state['sync_target']}"
        external_id = hashlib.md5(raw.encode()).hexdigest()[:12]
        return AdapterResult(
            success=True,
            external_id=external_id,
            sync_result="success",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return {
            "external_id": external_id,
            "source": "apple_calendar",
            "title": "[Mock] Synced Task",
            "status": "completed",
        }


# ── Production adapter (Phase 14) ────────────────────────────────────────


_SYNC_TEST_PREFIX = "[SYNC-TEST] "


class AppleSyncAdapter(SyncAdapter):
    """Apple Calendar/Reminders adapter with safety layer.

    Designed as a drop-in replacement for MockAppleAdapter when integrating
    real Apple EventKit. Provides three operating modes:

    dry_run (dry_run=True)
        Validates config and returns skipped result. No external calls.
        Safe for cron/LaunchAgent validation without side effects.

    test mode (test_mode=True)
        Simulates a successful push with a [SYNC-TEST] marker on the title.
        Generates test-prefixed external_id for traceability.
        No real Apple Calendar/Reminders entries are created.

    real mode (default)
        Validates platform (requires macOS) and performs a real EventKit
        Calendar write for target='apple_calendar'. Reminders are not
        supported in Phase 18B and return a structured unsupported_target
        error without touching Reminders.

    Args:
        target: 'apple_calendar' or 'apple_reminder'.
        dry_run: If True, validate config only — no push simulation.
        test_mode: If True, simulate push with test markers.

    Raises:
        ValueError: If target is not a valid Apple sync target.
    """

    def __init__(
        self,
        target: str = "apple_calendar",
        dry_run: bool = False,
        test_mode: bool = False,
    ):
        valid_targets = {"apple_calendar", "apple_reminder"}
        if target not in valid_targets:
            raise ValueError(
                f"AppleSyncAdapter target must be one of {valid_targets}, got: {target}"
            )
        self._target = target
        self._dry_run = dry_run
        self._test_mode = test_mode

    # ── SyncAdapter interface ───────────────────────────────────────────

    @property
    def target_name(self) -> str:
        return self._target

    def validate_config(self) -> tuple[bool, Optional[str]]:
        """Validate adapter configuration.

        Checks:
        - Platform is macOS (EventKit requires Darwin)
        - (Future) EventKit permissions granted

        Returns:
            (True, None) if valid; (False, error_message) otherwise.
        """
        if not self._is_macos():
            return (
                False,
                "Apple Calendar/Reminders sync requires macOS with EventKit framework",
            )
        # Future: EKEventStore.requestAccess(accessEntityType:)
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        """Push a task to Apple Calendar/Reminders.

        Mode-dependent behavior:
        - dry_run: Returns skipped, no external calls.
        - test_mode: Simulated success with [SYNC-TEST] marker.
        - real (non-macOS): Returns platform_unsupported error.
        - real (macOS): Executes EventKit Calendar write.

        Args:
            task_data: Task row as dict (from tasks table).
            sync_state: sync_state record as dict.

        Returns:
            AdapterResult with appropriate error/success fields.
        """
        # 1. Dry-run → skip immediately (no platform check needed)
        if self._dry_run:
            return AdapterResult(
                success=True,
                external_id="dry_run_noop",
                sync_result="skipped",
            )

        # 2. Test mode: simulate with test marker (no platform check needed)
        if self._test_mode:
            return self._simulate_test_push(task_data, sync_state)

        # 3. Platform check — macOS required for real EventKit calls
        if not self._is_macos():
            return AdapterResult(
                success=False,
                sync_result="failed",
                error_code="platform_unsupported",
                error_message=(
                    "Apple Calendar/Reminders sync requires macOS with EventKit "
                    "framework. Current platform: " + sys.platform
                ),
            )

        # 4. Real mode: EventKit integration (macOS only path)
        return self._push_real(task_data, sync_state)

    def pull(self, external_id: str) -> Optional[dict]:
        """Pull a record from Apple Calendar/Reminders.

        On macOS with EventKit, looks up the event by its eventIdentifier
        using predicate-based search (calendarItemWithIdentifier_ is
        unreliable in PyObjC). Returns its title, status, and source.
        Returns None if the event no longer exists or if EventKit is
        unavailable.
        """
        if not external_id or not self._is_macos():
            return None
        try:
            import EventKit  # type: ignore[import-not-found]
            from Foundation import NSDate  # type: ignore[import-not-found]

            store = EventKit.EKEventStore.alloc().init()
            if not bool(store.accessGrantedForEntityType_(EventKit.EKEntityTypeEvent)):
                return None

            # Use predicate search with a wide 7-day window instead of
            # calendarItemWithIdentifier_, which is unreliable in PyObjC
            now = NSDate.date()
            pred = store.predicateForEventsWithStartDate_endDate_calendars_(
                NSDate.dateWithTimeIntervalSinceNow_(-86400 * 3),  # 3 days back
                NSDate.dateWithTimeIntervalSinceNow_(86400 * 4),   # 4 days ahead
                None,
            )
            events = store.eventsMatchingPredicate_(pred)
            if events is None:
                return None
            for e in events:
                if e.eventIdentifier() == external_id:
                    return {
                        "external_id": external_id,
                        "source": self._target,
                        "title": str(e.title() or ""),
                        "status": "completed",
                    }
            return None
        except Exception:
            return None

    def remove_event(self, external_id: str) -> bool:
        """Delete a Calendar event by its eventIdentifier.

        Only supports apple_calendar target; Reminders are not touched.
        Returns True if the event was successfully deleted or no longer
        exists. Returns False on error or if Calendar permission is missing.

        Uses predicate-based search instead of calendarItemWithIdentifier_
        (unreliable in PyObjC).

        This method is intentionally not part of the base SyncAdapter
        interface — it is a Phase 18B convenience for cleanup.
        """
        if not external_id or self._target != "apple_calendar":
            return False
        try:
            import EventKit  # type: ignore[import-not-found]
            from Foundation import NSDate  # type: ignore[import-not-found]

            store = EventKit.EKEventStore.alloc().init()
            if not bool(store.accessGrantedForEntityType_(EventKit.EKEntityTypeEvent)):
                return False

            # Find the event by predicate search
            pred = store.predicateForEventsWithStartDate_endDate_calendars_(
                NSDate.dateWithTimeIntervalSinceNow_(-86400 * 3),
                NSDate.dateWithTimeIntervalSinceNow_(86400 * 4),
                None,
            )
            events = store.eventsMatchingPredicate_(pred)
            if events is None:
                return True  # No events at all — already gone
            target = None
            for e in events:
                if e.eventIdentifier() == external_id:
                    target = e
                    break
            if target is None:
                return True  # Already gone

            success, _ = store.removeEvent_span_error_(
                target, EventKit.EKSpanThisEvent, None
            )
            return bool(success)
        except Exception:
            return False

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _is_macos() -> bool:
        """Return True if running on macOS (Darwin)."""
        return sys.platform == "darwin"

    def _simulate_test_push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        """Simulate a push with test markers.

        Generates a deterministic external_id from sync_id and target,
        and simulates the title being prefixed with [SYNC-TEST] for
        traceability.
        """
        title = task_data.get("title", "")

        # Compute test-prefixed title (for audit/log purposes)
        _marked_title = _SYNC_TEST_PREFIX + title

        # Generate test external_id
        raw = f"test_{sync_state.get('sync_id', 'unknown')}:{self._target}"
        external_id = f"test_{hashlib.md5(raw.encode()).hexdigest()[:12]}"

        return AdapterResult(
            success=True,
            external_id=external_id,
            sync_result="success",
        )

    def _push_real(self, task_data: dict, sync_state: dict) -> AdapterResult:
        """Create or update one real Apple Calendar event via EventKit.

        Phase 18B intentionally supports Calendar only. Reminders are not
        touched from this method.
        """
        if self._target != "apple_calendar":
            return AdapterResult(
                success=False,
                sync_result="failed",
                error_code="unsupported_target",
                error_message="Phase 18B supports Calendar writes only; Reminders are disabled.",
            )

        try:
            import EventKit  # type: ignore[import-not-found]
            import Foundation  # type: ignore[import-not-found]
        except ImportError as exc:
            return AdapterResult(
                success=False,
                sync_result="failed",
                error_code="dependency_missing",
                error_message=f"EventKit/PyObjC dependency missing: {exc}",
            )

        try:
            store = EventKit.EKEventStore.alloc().init()
            if not bool(store.accessGrantedForEntityType_(EventKit.EKEntityTypeEvent)):
                return AdapterResult(
                    success=False,
                    sync_result="failed",
                    error_code="auth_failed",
                    error_message="Calendar TCC permission is not granted for this Python process.",
                )

            title = self._real_event_title(task_data)
            start_dt, end_dt = self._real_event_window(task_data)

            event = self._find_real_event_by_external_id(
                store,
                Foundation,
                str(sync_state.get("external_id") or ""),
            )
            if event is None:
                default_calendar = store.defaultCalendarForNewEvents()
                if default_calendar is None:
                    return AdapterResult(
                        success=False,
                        sync_result="failed",
                        error_code="calendar_unavailable",
                        error_message="No default Apple Calendar is available for new events.",
                    )
                event = EventKit.EKEvent.eventWithEventStore_(store)
                event.setCalendar_(default_calendar)

            event.setTitle_(title)
            event.setStartDate_(Foundation.NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp()))
            event.setEndDate_(Foundation.NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp()))

            notes = self._real_event_notes(task_data, sync_state)
            if notes:
                event.setNotes_(notes)

            location = task_data.get("location")
            if location:
                event.setLocation_(str(location))

            success, error = store.saveEvent_span_error_(event, EventKit.EKSpanThisEvent, None)
            if not success:
                return AdapterResult(
                    success=False,
                    sync_result="failed",
                    error_code="calendar_save_failed",
                    error_message=str(error) if error else "EventKit saveEvent returned False.",
                )

            return AdapterResult(
                success=True,
                external_id=str(event.eventIdentifier()),
                sync_result="success",
            )
        except Exception as exc:
            return AdapterResult(
                success=False,
                sync_result="failed",
                error_code="calendar_exception",
                error_message=str(exc),
            )

    @staticmethod
    def _real_event_title(task_data: dict) -> str:
        title = str(task_data.get("title") or "Untitled task")
        if title.startswith(_SYNC_TEST_PREFIX):
            return title
        return title

    @staticmethod
    def _find_real_event_by_external_id(store, foundation, external_id: str):
        if not external_id:
            return None

        if hasattr(store, "calendarItemWithIdentifier_"):
            event = store.calendarItemWithIdentifier_(external_id)
            if event is not None:
                return event

        pred = store.predicateForEventsWithStartDate_endDate_calendars_(
            foundation.NSDate.dateWithTimeIntervalSinceNow_(-86400 * 30),
            foundation.NSDate.dateWithTimeIntervalSinceNow_(86400 * 365),
            None,
        )
        events = store.eventsMatchingPredicate_(pred)
        if events is None:
            return None
        for event in events:
            if str(event.eventIdentifier()) == external_id:
                return event
        return None

    @staticmethod
    def _real_event_notes(task_data: dict, sync_state: dict) -> str:
        parts = []
        description = task_data.get("description")
        if description:
            parts.append(str(description))
        sync_id = sync_state.get("sync_id")
        task_id = task_data.get("task_id") or sync_state.get("task_id")
        audit = ["Created by AppleSyncAdapter"]
        if task_id:
            audit.append(f"task_id={task_id}")
        if sync_id:
            audit.append(f"sync_id={sync_id}")
        parts.append("; ".join(audit))
        return "\n\n".join(parts)

    @staticmethod
    def _real_event_window(task_data: dict) -> tuple[datetime, datetime]:
        tz = ZoneInfo(str(task_data.get("timezone") or "Asia/Shanghai"))
        start_raw = task_data.get("start_time")
        due_raw = task_data.get("due_time")

        start_dt = _parse_task_datetime(start_raw, tz) if start_raw else None
        end_dt = _parse_task_datetime(due_raw, tz) if due_raw else None

        if start_dt is None and end_dt is None:
            start_dt = datetime.now(tz) + timedelta(minutes=30)
            end_dt = start_dt + timedelta(minutes=30)
        elif start_dt is None:
            end_dt = cast(datetime, end_dt)
            start_dt = end_dt - timedelta(minutes=30)
        elif end_dt is None:
            start_dt = cast(datetime, start_dt)
            end_dt = start_dt + timedelta(minutes=30)

        start_dt = cast(datetime, start_dt)
        end_dt = cast(datetime, end_dt)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=30)
        return start_dt, end_dt


def _parse_task_datetime(value: str, tz: ZoneInfo) -> datetime:
    normalized = str(value).strip().replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed
