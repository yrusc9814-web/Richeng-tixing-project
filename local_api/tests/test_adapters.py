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

    def test_real_mode_apple_reminder_also_platform_unsupported(self):
        """Real mode with apple_reminder target also returns platform_unsupported."""
        adapter = AppleSyncAdapter(target="apple_reminder")
        result = adapter.push(_SAMPLE_TASK, _SAMPLE_STATE_REMINDER)
        if sys.platform == "darwin":
            pytest.skip("platform_unsupported path only on non-macOS")
        assert result.success is False
        assert result.error_code == "platform_unsupported"


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
