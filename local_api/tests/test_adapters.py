"""Phase 8A/14 — Unit tests for adapters.

Phase 14 additions:
  - AppleSyncAdapter: dry-run, test mode, platform unsupported, permissions,
    invalid target, pull not supported, real mode deferred.

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_adapters.py -v
"""

from __future__ import annotations

import sys
import types
from typing import Optional

import pytest

from local_api.adapters.apple_adapter import (
    AppleSyncAdapter,
    MockAppleAdapter,
    _SYNC_TEST_PREFIX,
)
from local_api.adapters.weather_adapter import MockWeatherAdapter
from local_api.database import init_db, reset_db


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


# ── Push / pull sample data ──────────────────────────────────────────────


_SAMPLE_TASK = {"title": "Team standup", "task_id": "task_001", "priority": "P2"}
_SAMPLE_STATE = {"sync_id": "sync_001", "sync_target": "apple_calendar"}
_SAMPLE_STATE_REMINDER = {"sync_id": "sync_002", "sync_target": "apple_reminder"}


class _FakeNSDate:
    @staticmethod
    def dateWithTimeIntervalSince1970_(timestamp):
        return ("date", timestamp)

    @staticmethod
    def dateWithTimeIntervalSinceNow_(seconds):
        return ("date_now", seconds)


class _FakeEvent:
    def __init__(self, identifier: str = "new_event"):
        self._identifier = identifier
        self.title_value = None
        self.notes_value = None
        self.location_value = None
        self.calendar_value = None

    def eventIdentifier(self):
        return self._identifier

    def setTitle_(self, value):
        self.title_value = value

    def setStartDate_(self, value):
        self.start_date_value = value

    def setEndDate_(self, value):
        self.end_date_value = value

    def setCalendar_(self, value):
        self.calendar_value = value

    def setNotes_(self, value):
        self.notes_value = value

    def setLocation_(self, value):
        self.location_value = value


class _FakeEKEvent:
    created_count = 0

    @staticmethod
    def eventWithEventStore_(store):
        _FakeEKEvent.created_count += 1
        event = _FakeEvent(f"new_event_{_FakeEKEvent.created_count}")
        store.created_events.append(event)
        return event


class _FakeStore:
    def __init__(self, events=None, default_calendar="default_calendar"):
        self.events = list(events or [])
        self.saved_events = []
        self.created_events = []
        self.default_calendar = default_calendar

    def accessGrantedForEntityType_(self, entity_type):
        return True

    def defaultCalendarForNewEvents(self):
        return self.default_calendar

    def predicateForEventsWithStartDate_endDate_calendars_(self, start, end, calendars):
        return ("predicate", start, end, calendars)

    def eventsMatchingPredicate_(self, predicate):
        return self.events

    def saveEvent_span_error_(self, event, span, error):
        self.saved_events.append(event)
        return (True, None)


class _FakeStoreFactory:
    def __init__(self, store):
        self.store = store

    def alloc(self):
        return self

    def init(self):
        return self.store


def _install_fake_eventkit(monkeypatch, store):
    _FakeEKEvent.created_count = 0
    eventkit = types.SimpleNamespace(
        EKEntityTypeEvent="event",
        EKSpanThisEvent="this_event",
        EKEventStore=_FakeStoreFactory(store),
        EKEvent=_FakeEKEvent,
    )
    foundation = types.SimpleNamespace(NSDate=_FakeNSDate)
    monkeypatch.setitem(sys.modules, "EventKit", eventkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    return eventkit


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — MockAppleAdapter (backward compat)
# ═══════════════════════════════════════════════════════════════════════════


class TestMockAppleAdapter:
    """MockAppleAdapter: push, pull, validate_config."""

    def test_push_returns_success_and_external_id(self):
        adapter = MockAppleAdapter()
        result = adapter.push(
            {"title": "Team standup"},
            {"sync_id": "sync_001", "sync_target": "apple_calendar"},
        )
        assert result.success is True
        assert result.external_id is not None
        assert len(result.external_id) == 12
        assert result.sync_result == "success"

    def test_pull_returns_dict(self):
        adapter = MockAppleAdapter()
        result = adapter.pull("ext_abc123")
        assert isinstance(result, dict)
        assert result["source"] == "apple_calendar"
        assert "external_id" in result

    def test_validate_config_returns_valid(self):
        adapter = MockAppleAdapter()
        valid, err = adapter.validate_config()
        assert valid is True
        assert err is None


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — MockWeatherAdapter
# ═══════════════════════════════════════════════════════════════════════════


class TestMockWeatherAdapter:
    """MockWeatherAdapter: push, pull, validate_config."""

    def test_push_returns_weather_inline(self):
        adapter = MockWeatherAdapter()
        result = adapter.push(
            {"title": "Check weather"},
            {"sync_id": "sync_002", "sync_target": "weather"},
        )
        assert result.success is True
        assert result.external_id == "weather_inline"
        assert result.sync_result == "success"

    def test_pull_returns_dict(self):
        adapter = MockWeatherAdapter()
        result = adapter.pull("weather_inline")
        assert isinstance(result, dict)
        assert result["source"] == "weather"
        assert "temp" in result
        assert "condition" in result

    def test_validate_config_returns_valid(self):
        adapter = MockWeatherAdapter()
        valid, err = adapter.validate_config()
        assert valid is True
        assert err is None


# ═══════════════════════════════════════════════════════════════════════════
# Phase 14 — AppleSyncAdapter Safety Layer Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterConstruction:
    """AppleSyncAdapter: constructor validation."""

    def test_default_target_is_apple_calendar(self):
        adapter = AppleSyncAdapter()
        assert adapter.target_name == "apple_calendar"

    def test_explicit_apple_calendar(self):
        adapter = AppleSyncAdapter(target="apple_calendar")
        assert adapter.target_name == "apple_calendar"

    def test_explicit_apple_reminder(self):
        adapter = AppleSyncAdapter(target="apple_reminder")
        assert adapter.target_name == "apple_reminder"

    def test_invalid_target_raises_value_error(self):
        with pytest.raises(ValueError, match="AppleSyncAdapter target"):
            AppleSyncAdapter(target="invalid_platform")

    def test_empty_target_raises_value_error(self):
        with pytest.raises(ValueError, match="AppleSyncAdapter target"):
            AppleSyncAdapter(target="")

    def test_dry_run_flag_stored(self):
        adapter = AppleSyncAdapter(dry_run=True)
        # dry_run is internal, verified through behavior
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert result.sync_result == "skipped"
        assert result.external_id == "dry_run_noop"

    def test_test_mode_flag_stored(self):
        adapter = AppleSyncAdapter(test_mode=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert result.success is True
        assert result.external_id.startswith("test_")


# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterValidateConfig:
    """AppleSyncAdapter: validate config for different platforms."""

    def test_validate_config_returns_error_on_non_macos(self):
        """On non-macOS platforms, validate_config must return error."""
        adapter = AppleSyncAdapter()
        valid, err = adapter.validate_config()
        if sys.platform == "darwin":
            pytest.skip("This test requires non-macOS platform")
        assert valid is False
        assert err is not None
        assert "macOS" in err or "EventKit" in err

    def test_validate_config_error_message_mentions_eventkit(self):
        """Error message should mention EventKit framework."""
        adapter = AppleSyncAdapter()
        valid, err = adapter.validate_config()
        if sys.platform == "darwin":
            pytest.skip("Error path only on non-macOS")
        assert "EventKit" in err


# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterDryRun:
    """AppleSyncAdapter: dry-run mode must skip all external operations."""

    def test_dry_run_returns_skipped(self):
        adapter = AppleSyncAdapter(dry_run=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert result.success is True
        assert result.external_id == "dry_run_noop"
        assert result.sync_result == "skipped"
        assert result.error_code is None
        assert result.error_message is None

    def test_dry_run_works_with_apple_reminder(self):
        adapter = AppleSyncAdapter(target="apple_reminder", dry_run=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE_REMINDER)
        assert result.success is True
        assert result.external_id == "dry_run_noop"
        assert result.sync_result == "skipped"

    def test_dry_run_ignores_test_mode_flag(self):
        """dry_run takes precedence over test_mode."""
        adapter = AppleSyncAdapter(dry_run=True, test_mode=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert result.sync_result == "skipped"
        assert result.external_id == "dry_run_noop"


# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterPlatformCheck:
    """AppleSyncAdapter: platform check must return clear error on non-macOS."""

    def test_push_returns_platform_error_on_non_macos(self):
        """push on non-macOS must return failed with error_code='platform_unsupported'."""
        adapter = AppleSyncAdapter()
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        if sys.platform == "darwin":
            pytest.skip("Platform error only on non-macOS")
        assert result.success is False
        assert result.sync_result == "failed"
        assert result.error_code == "platform_unsupported"
        assert result.error_message is not None
        assert "macOS" in result.error_message

    def test_platform_error_includes_current_platform(self):
        """Error message must identify the current platform."""
        adapter = AppleSyncAdapter()
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        if sys.platform == "darwin":
            pytest.skip("Platform error only on non-macOS")
        assert sys.platform in result.error_message

    def test_platform_error_apple_reminder(self):
        """Both apple_calendar and apple_reminder return platform error."""
        adapter = AppleSyncAdapter(target="apple_reminder")
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE_REMINDER)
        if sys.platform == "darwin":
            pytest.skip("Platform error only on non-macOS")
        assert result.success is False
        assert result.error_code == "platform_unsupported"

    def test_no_side_effects_on_platform_error(self):
        """Platform error must not have external_id or success=True."""
        adapter = AppleSyncAdapter()
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        if sys.platform == "darwin":
            pytest.skip("Platform error only on non-macOS")
        assert result.success is False
        assert result.external_id is None


# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterTestMode:
    """AppleSyncAdapter: test mode must simulate push with test markers."""

    def test_test_mode_returns_success(self):
        adapter = AppleSyncAdapter(test_mode=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert result.success is True
        assert result.sync_result == "success"
        assert result.error_code is None
        assert result.error_message is None

    def test_test_mode_external_id_has_test_prefix(self):
        """Test mode external_id must start with 'test_'."""
        adapter = AppleSyncAdapter(test_mode=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert result.external_id is not None
        assert result.external_id.startswith("test_")

    def test_test_mode_external_id_deterministic_for_same_sync(self):
        """Same sync_id + target produces same external_id."""
        adapter = AppleSyncAdapter(test_mode=True)
        r1 = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        r2 = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        assert r1.external_id == r2.external_id

    def test_test_mode_external_id_differs_for_diff_target(self):
        """Different targets produce different external_ids."""
        adapter_cal = AppleSyncAdapter(target="apple_calendar", test_mode=True)
        adapter_rem = AppleSyncAdapter(target="apple_reminder", test_mode=True)
        r_cal = adapter_cal.push(
            {"title": "Test"}, {"sync_id": "sync_test", "sync_target": "apple_calendar"}
        )
        r_rem = adapter_rem.push(
            {"title": "Test"}, {"sync_id": "sync_test", "sync_target": "apple_reminder"}
        )
        assert r_cal.external_id != r_rem.external_id

    def test_test_mode_works_for_apple_reminder(self):
        adapter = AppleSyncAdapter(target="apple_reminder", test_mode=True)
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE_REMINDER)
        assert result.success is True
        assert result.external_id.startswith("test_")


# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterRealMode:
    """AppleSyncAdapter: real mode (not dry_run, not test_mode) behavior."""

    def test_real_mode_returns_platform_unsupported_on_non_macos(self):
        """Real mode on non-macOS must return platform_unsupported.

        The platform check takes priority — non-macOS platforms cannot
        reach the EventKit integration path.
        """
        adapter = AppleSyncAdapter()
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)
        if sys.platform == "darwin":
            pytest.skip("platform_unsupported path only on non-macOS")
        assert result.success is False
        assert result.sync_result == "failed"
        assert result.error_code == "platform_unsupported"
        assert result.error_message is not None
        assert "macOS" in result.error_message

    def test_real_mode_apple_reminder_is_disabled_on_macos(self, monkeypatch):
        """Phase 18B forbids real Reminders writes even on macOS."""
        monkeypatch.setattr(AppleSyncAdapter, "_is_macos", staticmethod(lambda: True))
        adapter = AppleSyncAdapter(target="apple_reminder")

        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE_REMINDER)

        assert result.success is False
        assert result.sync_result == "failed"
        assert result.error_code == "unsupported_target"
        assert "Reminders" in result.error_message

    def test_real_mode_calendar_delegates_to_push_real_on_macos(self, monkeypatch):
        """Calendar real mode must no longer return not_implemented."""
        monkeypatch.setattr(AppleSyncAdapter, "_is_macos", staticmethod(lambda: True))

        def fake_push_real(self, task_data, sync_state):
            return MockAppleAdapter().push(task_data, sync_state)

        monkeypatch.setattr(AppleSyncAdapter, "_push_real", fake_push_real)
        adapter = AppleSyncAdapter(target="apple_calendar")

        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)

        assert result.success is True
        assert result.sync_result == "success"
        assert result.error_code != "not_implemented"

    def test_real_mode_calendar_missing_eventkit_is_structured_error(self, monkeypatch):
        """Missing EventKit must be reported, not surfaced as an exception."""
        monkeypatch.setattr(AppleSyncAdapter, "_is_macos", staticmethod(lambda: True))
        adapter = AppleSyncAdapter(target="apple_calendar")

        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE)

        if sys.platform == "darwin":
            pytest.skip("EventKit may be installed on macOS acceptance machines")
        assert result.success is False
        assert result.sync_result == "failed"
        assert result.error_code == "dependency_missing"

    def test_real_mode_calendar_updates_existing_external_id_without_new_event(self, monkeypatch):
        """A stale/pending push with external_id updates the existing Calendar event."""
        existing = _FakeEvent("event_existing_001")
        store = _FakeStore(events=[existing])
        _install_fake_eventkit(monkeypatch, store)
        adapter = AppleSyncAdapter(target="apple_calendar")
        state = {
            "sync_id": "sync_existing",
            "sync_target": "apple_calendar",
            "external_id": "event_existing_001",
        }

        result = adapter._push_real(
            {
                "task_id": "task_existing",
                "title": "Updated title",
                "start_time": "2026-06-01T10:00:00+08:00",
                "due_time": "2026-06-01T10:30:00+08:00",
            },
            state,
        )

        assert result.success is True
        assert result.external_id == "event_existing_001"
        assert _FakeEKEvent.created_count == 0
        assert store.saved_events == [existing]
        assert existing.title_value == "Updated title"

    def test_real_mode_calendar_update_does_not_require_default_calendar(self, monkeypatch):
        """Updating an existing event must not require a calendar for new events."""
        existing = _FakeEvent("event_no_default_calendar")
        store = _FakeStore(events=[existing], default_calendar=None)
        _install_fake_eventkit(monkeypatch, store)
        adapter = AppleSyncAdapter(target="apple_calendar")

        result = adapter._push_real(
            {
                "task_id": "task_existing_no_default",
                "title": "Updated without default calendar",
                "start_time": "2026-06-01T10:00:00+08:00",
                "due_time": "2026-06-01T10:30:00+08:00",
            },
            {
                "sync_id": "sync_existing_no_default",
                "sync_target": "apple_calendar",
                "external_id": "event_no_default_calendar",
            },
        )

        assert result.success is True
        assert result.external_id == "event_no_default_calendar"
        assert _FakeEKEvent.created_count == 0
        assert store.saved_events == [existing]

    def test_real_mode_calendar_creates_only_when_external_id_not_found(self, monkeypatch):
        """Missing external_id target falls back to one new Calendar event."""
        store = _FakeStore(events=[_FakeEvent("different_event")])
        _install_fake_eventkit(monkeypatch, store)
        adapter = AppleSyncAdapter(target="apple_calendar")

        result = adapter._push_real(
            {
                "task_id": "task_new",
                "title": "New title",
                "start_time": "2026-06-01T10:00:00+08:00",
                "due_time": "2026-06-01T10:30:00+08:00",
            },
            {
                "sync_id": "sync_new",
                "sync_target": "apple_calendar",
                "external_id": "missing_event",
            },
        )

        assert result.success is True
        assert result.external_id == "new_event_1"
        assert _FakeEKEvent.created_count == 1
        assert store.saved_events == store.created_events

    def test_real_mode_save_event_returns_false_is_calendar_save_failed(self, monkeypatch):
        """When EventKit saveEvent returns False, result must be calendar_save_failed."""

        class _FailingSaveStore(_FakeStore):
            def saveEvent_span_error_(self, event, span, error):
                self.saved_events.append(event)
                return (False, None)

        store = _FailingSaveStore()
        _install_fake_eventkit(monkeypatch, store)
        adapter = AppleSyncAdapter(target="apple_calendar")

        result = adapter._push_real(
            {
                "task_id": "task_save_fail",
                "title": "Will fail to save",
                "start_time": "2026-06-01T10:00:00+08:00",
                "due_time": "2026-06-01T10:30:00+08:00",
            },
            {
                "sync_id": "sync_save_fail",
                "sync_target": "apple_calendar",
            },
        )

        assert result.success is False
        assert result.sync_result == "failed"
        assert result.error_code == "calendar_save_failed"
        assert result.external_id is None
        assert "EventKit saveEvent returned False" in result.error_message

    def test_dry_run_and_test_mode_do_not_reach_real_calendar_write(self, monkeypatch):
        def fail_push_real(self, task_data, sync_state):
            raise AssertionError("real Calendar write must not be reached")

        monkeypatch.setattr(AppleSyncAdapter, "_is_macos", staticmethod(lambda: True))
        monkeypatch.setattr(AppleSyncAdapter, "_push_real", fail_push_real)

        dry_run = AppleSyncAdapter(target="apple_calendar", dry_run=True)
        test_mode = AppleSyncAdapter(target="apple_calendar", test_mode=True)

        assert dry_run.push(_SAMPLE_TASK, _SAMPLE_STATE).sync_result == "skipped"
        assert test_mode.push(_SAMPLE_TASK, _SAMPLE_STATE).success is True


# ═══════════════════════════════════════════════════════════════════════════


class TestAppleSyncAdapterPull:
    """AppleSyncAdapter: pull is not yet supported."""

    def test_pull_returns_none(self):
        """Pull must return None (not implemented)."""
        adapter = AppleSyncAdapter()
        result = adapter.pull("ext_001")
        assert result is None

    def test_pull_returns_none_for_apple_reminder(self):
        adapter = AppleSyncAdapter(target="apple_reminder")
        result = adapter.pull("ext_002")
        assert result is None

    def test_pull_returns_none_regardless_of_dry_run(self):
        """Pull behavior is independent of dry_run flag."""
        adapter = AppleSyncAdapter(dry_run=True)
        assert adapter.pull("ext_003") is None

    def test_pull_returns_none_regardless_of_test_mode(self):
        adapter = AppleSyncAdapter(test_mode=True)
        assert adapter.pull("ext_004") is None
