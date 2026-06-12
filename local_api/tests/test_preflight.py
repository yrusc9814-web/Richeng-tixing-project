"""Phase 17 — Unit tests for preflight checks.

Tests cover:
  - Construction: check item fields, report structure
  - Preflight on non-macOS: fails apple_platform, skips Apple checks
  - Preflight with WeChat configured: passes wechat_config
  - Preflight with partial WeChat config: warns with specific message
  - Preflight output structure: all required fields present
  - Readiness computation: not_ready / partial / ready levels
  - Direct check functions: no external calls made

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_preflight.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_calendar_helper_auth(monkeypatch):
    """Unit tests should not launch the GUI helper app."""
    monkeypatch.setattr(
        "local_api.preflight._check_calendar_helper_auth",
        lambda helper_app: {
            "ok": True,
            "mode": "check_access",
            "bundleIdentifier": "com.vanta.lifesync.calendar-helper",
            "authorizationStatus": "authorized",
            "calendarAccessGranted": True,
            "calendarCount": 1,
        },
    )

from local_api.preflight import (
    PreflightCheckItem,
    PreflightReport,
    PreflightStatus,
    ReadinessLevel,
    run_preflight,
    _check_eventkit_deps,
    _check_calendar_helper_app,
    _check_calendar_helper_auth,
    _check_rollback_cleanup_rules,
    CHECK_APPLE_PLATFORM,
    CHECK_APPLE_EVENTKIT_DEPS,
    CHECK_APPLE_CALENDAR_PERM,
    CHECK_APPLE_HELPER_APP,
    CHECK_APPLE_HELPER_AUTH,
    CHECK_APPLE_PYTHON_CALENDAR_PERM,
    CHECK_APPLE_REMINDER_PERM,
    CHECK_APPLE_DRY_RUN,
    CHECK_APPLE_TEST_MODE,
    CHECK_WECHAT_CONFIG,
    CHECK_WECHAT_DRY_RUN,
    CHECK_WECHAT_TEST_MODE,
    CHECK_TEST_DATA_MARKING,
    CHECK_ROLLBACK_CLEANUP,
)


# ═══════════════════════════════════════════════════════════════════════════
# Construction tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPreflightDataTypes:
    """PreflightCheckItem and PreflightReport construction."""

    def test_check_item_construction(self):
        item = PreflightCheckItem(
            name="test_check",
            status=PreflightStatus.PASS,
            detail="All good",
            recommendation="Do nothing",
        )
        assert item.name == "test_check"
        assert item.status == PreflightStatus.PASS
        assert item.detail == "All good"
        assert item.recommendation == "Do nothing"

    def test_report_add_item(self):
        report = PreflightReport()
        report.add("check_1", PreflightStatus.PASS, detail="ok")
        assert len(report.checks) == 1
        assert report.checks[0].name == "check_1"
        assert report.checks[0].status == PreflightStatus.PASS

    def test_report_to_dict_contains_all_fields(self):
        report = PreflightReport()
        report.add("check_x", PreflightStatus.FAIL, detail="broken", recommendation="fix it")
        d = report.to_dict()
        assert "readiness" in d
        assert "summary" in d
        assert "checks" in d
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "check_x"
        assert d["checks"][0]["status"] == "fail"
        assert d["checks"][0]["detail"] == "broken"
        assert d["checks"][0]["recommendation"] == "fix it"

    def test_report_to_dict_has_correct_type(self):
        report = PreflightReport()
        report.add("a", PreflightStatus.WARN)
        d = report.to_dict()
        assert isinstance(d["checks"], list)
        assert isinstance(d["readiness"], str)
        assert isinstance(d["summary"], str)

    def test_env_constants_defined(self):
        """Expected check name constants are well-formed strings."""
        names = [
            CHECK_APPLE_PLATFORM,
            CHECK_APPLE_EVENTKIT_DEPS,
            CHECK_APPLE_CALENDAR_PERM,
            CHECK_APPLE_HELPER_APP,
            CHECK_APPLE_HELPER_AUTH,
            CHECK_APPLE_PYTHON_CALENDAR_PERM,
            CHECK_APPLE_REMINDER_PERM,
            CHECK_APPLE_DRY_RUN,
            CHECK_APPLE_TEST_MODE,
            CHECK_WECHAT_CONFIG,
            CHECK_WECHAT_DRY_RUN,
            CHECK_WECHAT_TEST_MODE,
            CHECK_TEST_DATA_MARKING,
            CHECK_ROLLBACK_CLEANUP,
        ]
        for name in names:
            assert isinstance(name, str) and len(name) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Preflight on non-macOS platform
# ═══════════════════════════════════════════════════════════════════════════


class TestPreflightOnNonMacOS:
    """Preflight behavior when not running on macOS."""

    @pytest.mark.skipif(sys.platform == "darwin", reason="Non-macOS platform test; on macOS apple_platform passes")
    def test_apple_platform_fails_on_non_macos(self):
        report = run_preflight()
        apple_platform = next(
            (c for c in report.checks if c.name == CHECK_APPLE_PLATFORM), None
        )
        assert apple_platform is not None
        assert apple_platform.status == PreflightStatus.FAIL
        assert "macOS" in apple_platform.detail

    @pytest.mark.skipif(sys.platform == "darwin", reason="Non-macOS platform test; on macOS EventKit is available")
    def test_apple_checks_are_skipped_on_non_macos(self):
        report = run_preflight()
        skip_checks = [
            CHECK_APPLE_EVENTKIT_DEPS,
            CHECK_APPLE_HELPER_APP,
            CHECK_APPLE_HELPER_AUTH,
            CHECK_APPLE_CALENDAR_PERM,
            CHECK_APPLE_PYTHON_CALENDAR_PERM,
            CHECK_APPLE_REMINDER_PERM,
        ]
        for name in skip_checks:
            check = next((c for c in report.checks if c.name == name), None)
            assert check is not None, f"Missing check: {name}"
            assert check.status == PreflightStatus.SKIP, (
                f"{name} should be SKIP, got {check.status}"
            )

    def test_dry_run_and_test_mode_pass_regardless(self):
        """dry-run and test-mode checks always pass — they're built-in."""
        report = run_preflight()
        for name in [
            CHECK_APPLE_DRY_RUN,
            CHECK_APPLE_TEST_MODE,
            CHECK_WECHAT_DRY_RUN,
            CHECK_WECHAT_TEST_MODE,
        ]:
            check = next((c for c in report.checks if c.name == name), None)
            assert check is not None
            assert check.status == PreflightStatus.PASS

    def test_test_data_marking_pass(self):
        report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_TEST_DATA_MARKING), None
        )
        assert check is not None
        assert check.status == PreflightStatus.PASS

    def test_rollback_cleanup_pass(self):
        report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_ROLLBACK_CLEANUP), None
        )
        assert check is not None
        assert check.status == PreflightStatus.PASS

    @pytest.mark.skipif(sys.platform == "darwin", reason="Non-macOS platform test; on macOS EventKit / PyObjC availability varies")
    def test_readiness_not_ready_on_non_macos(self):
        report = run_preflight()
        assert report.readiness == ReadinessLevel.NOT_READY
        assert "1 check(s) failed" in report.summary


# ═══════════════════════════════════════════════════════════════════════════
# WeChat config variations
# ═══════════════════════════════════════════════════════════════════════════


class TestPreflightWeChatConfig:
    """Preflight behavior with various WeChat env configurations."""

    def test_wechat_not_set_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_WECHAT_CONFIG), None
        )
        assert check is not None
        assert check.status == PreflightStatus.SKIP
        assert "outside Phase 18B" in check.detail

    def test_wechat_enabled_with_credentials_passes(self):
        with patch.dict(
            os.environ,
            {
                "WECHAT_REMINDER_ENABLED": "true",
                "WECHAT_APP_ID": "wx_test_id",
                "WECHAT_APP_SECRET": "test_secret_value",
            },
            clear=True,
        ):
            report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_WECHAT_CONFIG), None
        )
        assert check is not None
        assert check.status == PreflightStatus.PASS

    def test_wechat_enabled_missing_both_credentials(self):
        with patch.dict(
            os.environ,
            {"WECHAT_REMINDER_ENABLED": "true"},
            clear=True,
        ):
            report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_WECHAT_CONFIG), None
        )
        assert check is not None
        assert check.status == PreflightStatus.WARN
        assert "both" in check.detail.lower()

    def test_wechat_enabled_missing_app_id(self):
        with patch.dict(
            os.environ,
            {
                "WECHAT_REMINDER_ENABLED": "true",
                "WECHAT_APP_SECRET": "secret_val",
            },
            clear=True,
        ):
            report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_WECHAT_CONFIG), None
        )
        assert check is not None
        assert check.status == PreflightStatus.WARN
        assert "WECHAT_APP_ID" in check.detail

    def test_wechat_enabled_missing_app_secret(self):
        with patch.dict(
            os.environ,
            {
                "WECHAT_REMINDER_ENABLED": "true",
                "WECHAT_APP_ID": "wx_id",
            },
            clear=True,
        ):
            report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_WECHAT_CONFIG), None
        )
        assert check is not None
        assert check.status == PreflightStatus.WARN
        assert "WECHAT_APP_SECRET" in check.detail

    def test_wechat_disabled_non_truthy(self):
        with patch.dict(
            os.environ,
            {"WECHAT_REMINDER_ENABLED": "false"},
            clear=True,
        ):
            report = run_preflight()
        check = next(
            (c for c in report.checks if c.name == CHECK_WECHAT_CONFIG), None
        )
        assert check is not None
        assert check.status == PreflightStatus.SKIP


# ═══════════════════════════════════════════════════════════════════════════
# Readiness computation
# ═══════════════════════════════════════════════════════════════════════════


class TestPreflightReadiness:
    """Overall readiness level computation."""

    def test_not_ready_when_helper_auth_fails(self, monkeypatch):
        """Helper Calendar auth failure blocks real Calendar readiness."""
        monkeypatch.setattr(
            "local_api.preflight._check_calendar_helper_auth",
            lambda helper_app: {"ok": False, "detail": "Helper Calendar auth is denied."},
        )
        report = run_preflight()
        assert report.readiness == ReadinessLevel.NOT_READY
        assert CHECK_APPLE_HELPER_AUTH in report.summary or CHECK_APPLE_CALENDAR_PERM in report.summary

    def test_partial_when_warn_only(self):
        """If only warnings exist, readiness is PARTIAL."""
        report = PreflightReport()
        report.add("check_warn", PreflightStatus.WARN, detail="Something suspicious")
        from local_api.preflight import _compute_readiness
        _compute_readiness(report)
        assert report.readiness == ReadinessLevel.PARTIAL

    def test_ready_when_all_pass_only(self):
        """If all checks pass/skip, readiness is READY."""
        report = PreflightReport()
        report.add("check_pass", PreflightStatus.PASS)
        report.add("check_skip", PreflightStatus.SKIP)
        from local_api.preflight import _compute_readiness
        _compute_readiness(report)
        assert report.readiness == ReadinessLevel.READY


# ═══════════════════════════════════════════════════════════════════════════
# Direct check function tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPreflightInternalCheckFunctions:
    """Internal check functions — no external calls made."""

    @pytest.mark.skipif(sys.platform == "darwin", reason="Non-macOS test; on macOS EventKit imports successfully")
    def test_eventkit_deps_on_non_macos_returns_false(self):
        """On non-macOS, EventKit deps check must return False without error."""
        result = _check_eventkit_deps()
        assert result is False

    def test_calendar_helper_app_check_uses_bundle_executable(self, tmp_path):
        helper_app = tmp_path / "LifeSyncCalendarHelper.app"
        helper_bin = helper_app / "Contents" / "MacOS" / "LifeSyncCalendarHelper"
        helper_bin.parent.mkdir(parents=True)
        helper_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        helper_bin.chmod(0o755)
        assert _check_calendar_helper_app(helper_app) is True

    def test_calendar_helper_auth_missing_app_fails(self, tmp_path):
        result = _check_calendar_helper_auth(tmp_path / "missing.app")
        assert result["ok"] is False
        assert "missing" in result["detail"] or "not executable" in result["detail"]

    def test_rollback_cleanup_returns_true(self):
        """Rollback/cleanup check returns True on valid codebase."""
        result = _check_rollback_cleanup_rules()
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# Output structure stability
# ═══════════════════════════════════════════════════════════════════════════


class TestPreflightOutputStructure:
    """Preflight output structure must be stable."""

    _REQUIRED_CHECK_FIELDS = {"name", "status", "detail", "recommendation"}
    _REQUIRED_REPORT_FIELDS = {"readiness", "summary", "checks"}

    def test_all_checks_have_required_fields(self):
        report = run_preflight()
        for check in report.checks:
            as_dict = {
                "name": check.name,
                "status": check.status.value,
                "detail": check.detail,
                "recommendation": check.recommendation,
            }
            assert set(as_dict.keys()) == self._REQUIRED_CHECK_FIELDS

    def test_report_to_dict_has_all_fields(self):
        report = run_preflight()
        d = report.to_dict()
        assert set(d.keys()) == self._REQUIRED_REPORT_FIELDS
        assert isinstance(d["checks"], list)
        assert isinstance(d["readiness"], str)
        assert isinstance(d["summary"], str)

    def test_check_names_are_unique(self):
        report = run_preflight()
        names = [c.name for c in report.checks]
        assert len(names) == len(set(names)), "Duplicate check names found"

    def test_expected_check_names_present(self):
        """Preflight includes the required Calendar/helper readiness checks."""
        report = run_preflight()
        names = {c.name for c in report.checks}
        expected = {
            CHECK_APPLE_PLATFORM,
            CHECK_APPLE_EVENTKIT_DEPS,
            CHECK_APPLE_HELPER_APP,
            CHECK_APPLE_HELPER_AUTH,
            CHECK_APPLE_CALENDAR_PERM,
            CHECK_APPLE_PYTHON_CALENDAR_PERM,
            CHECK_APPLE_REMINDER_PERM,
            CHECK_APPLE_DRY_RUN,
            CHECK_APPLE_TEST_MODE,
            CHECK_WECHAT_CONFIG,
            CHECK_WECHAT_DRY_RUN,
            CHECK_WECHAT_TEST_MODE,
            CHECK_TEST_DATA_MARKING,
            CHECK_ROLLBACK_CLEANUP,
        }
        assert expected.issubset(names)

    def test_all_statuses_are_valid_enum_values(self):
        report = run_preflight()
        valid = {s.value for s in PreflightStatus}
        for check in report.checks:
            assert check.status.value in valid, (
                f"Invalid status {check.status} for {check.name}"
            )
