"""Phase 15 — Unit tests for WeChat notification channel.

Test coverage:
  - Construction: valid modes, invalid mode raises
  - Dry-run: returns success with skipped status
  - Test mode: returns success with simulated status
  - Not configured: returns error with platform_not_configured
  - Missing credentials: returns error with missing_credentials
  - Real push not implemented: returns not_implemented
  - Output structure stability: all required fields present

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_wechat_channel.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from local_api.notify.base import NotifyResult
from local_api.notify.wechat_channel import (
    WeChatNotifyChannel,
    _ENV_APP_ID,
    _ENV_APP_SECRET,
    _ENV_ENABLED,
)


# ── Sample task data ───────────────────────────────────────────────────────

_SAMPLE_TASK: dict = {
    "task_id": "task_100",
    "title": "Team standup reminder",
    "priority": "P2",
    "due_at": "2026-05-29T10:00:00+08:00",
}


# ═══════════════════════════════════════════════════════════════════════════
# Construction tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWeChatNotifyChannelConstruction:
    """WeChatNotifyChannel: constructor validation."""

    def test_default_mode_is_dry_run(self):
        channel = WeChatNotifyChannel()
        assert channel._mode == WeChatNotifyChannel.MODE_DRY_RUN

    def test_explicit_dry_run(self):
        channel = WeChatNotifyChannel(mode="dry_run")
        assert channel._mode == "dry_run"

    def test_explicit_test_mode(self):
        channel = WeChatNotifyChannel(mode="test_mode")
        assert channel._mode == "test_mode"

    def test_explicit_real_mode(self):
        channel = WeChatNotifyChannel(mode="real")
        assert channel._mode == "real"

    def test_channel_name_is_wechat(self):
        channel = WeChatNotifyChannel()
        assert channel.channel_name == "wechat"

    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="WeChatNotifyChannel mode"):
            WeChatNotifyChannel(mode="invalid_mode")

    def test_empty_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="WeChatNotifyChannel mode"):
            WeChatNotifyChannel(mode="")


# ═══════════════════════════════════════════════════════════════════════════
# Dry-run tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWeChatNotifyChannelDryRun:
    """WeChatNotifyChannel: dry_run mode behavior."""

    def test_dry_run_returns_skipped(self):
        """Dry-run must return success with status=skipped."""
        channel = WeChatNotifyChannel(mode="dry_run")
        result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is True
        assert result.mode == "dry_run"
        assert result.channel == "wechat"
        assert result.task_id == "task_100"
        assert result.status == "skipped"
        assert result.error_code is None
        assert result.error_message is None

    def test_dry_run_succeeds_regardless_of_env(self):
        """Dry-run must NOT check env vars — succeeds unconditionally."""
        with patch.dict(os.environ, {}, clear=True):
            channel = WeChatNotifyChannel(mode="dry_run")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is True
        assert result.status == "skipped"


# ═══════════════════════════════════════════════════════════════════════════
# Test-mode tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWeChatNotifyChannelTestMode:
    """WeChatNotifyChannel: test_mode behavior."""

    def test_test_mode_returns_simulated(self):
        """Test mode must return success with status=simulated."""
        channel = WeChatNotifyChannel(mode="test_mode")
        result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is True
        assert result.mode == "test_mode"
        assert result.channel == "wechat"
        assert result.task_id == "task_100"
        assert result.status == "simulated"
        assert result.error_code is None
        assert result.error_message is None

    def test_test_mode_succeeds_regardless_of_env(self):
        """Test mode must NOT check env vars — succeeds unconditionally."""
        with patch.dict(os.environ, {}, clear=True):
            channel = WeChatNotifyChannel(mode="test_mode")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is True
        assert result.status == "simulated"


# ═══════════════════════════════════════════════════════════════════════════
# Real-mode config error tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWeChatNotifyChannelNotConfigured:
    """WeChatNotifyChannel: real mode with platform not configured."""

    def test_not_configured_returns_explicit_error(self):
        """Without WECHAT_REMINDER_ENABLED, must return platform_not_configured."""
        with patch.dict(os.environ, {}, clear=True):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.mode == "real"
        assert result.channel == "wechat"
        assert result.task_id == "task_100"
        assert result.status == "config_error"
        assert result.error_code == "platform_not_configured"
        assert result.error_message is not None
        assert "WECHAT_REMINDER_ENABLED" in result.error_message

    def test_not_configured_with_disabled_flag(self):
        """WECHAT_REMINDER_ENABLED=false must also return platform_not_configured."""
        with patch.dict(
            os.environ,
            {_ENV_ENABLED: "false", _ENV_APP_ID: "", _ENV_APP_SECRET: ""},
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.error_code == "platform_not_configured"

    def test_not_configured_with_credentials_alone(self):
        """Credentials without enabled flag must still be platform_not_configured."""
        with patch.dict(
            os.environ,
            {
                _ENV_ENABLED: "",
                _ENV_APP_ID: "wx_appid_1234",
                _ENV_APP_SECRET: "secret_value",
            },
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.error_code == "platform_not_configured"


class TestWeChatNotifyChannelMissingCredentials:
    """WeChatNotifyChannel: real mode with enabled but missing credentials."""

    def test_missing_both_credentials(self):
        """Enabled + missing both app_id and app_secret must return missing_credentials."""
        with patch.dict(
            os.environ,
            {_ENV_ENABLED: "true", _ENV_APP_ID: "", _ENV_APP_SECRET: ""},
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.status == "config_error"
        assert result.error_code == "missing_credentials"
        assert result.error_message is not None
        assert "WECHAT_APP_ID" in result.error_message

    def test_missing_app_id(self):
        """Enabled + missing app_id must return missing_credentials."""
        with patch.dict(
            os.environ,
            {
                _ENV_ENABLED: "true",
                _ENV_APP_ID: "",
                _ENV_APP_SECRET: "valid_secret",
            },
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.error_code == "missing_credentials"

    def test_missing_app_secret(self):
        """Enabled + missing app_secret must return missing_credentials."""
        with patch.dict(
            os.environ,
            {
                _ENV_ENABLED: "true",
                _ENV_APP_ID: "wx_appid_1234",
                _ENV_APP_SECRET: "",
            },
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.error_code == "missing_credentials"


class TestWeChatNotifyChannelRealNotImplemented:
    """WeChatNotifyChannel: real mode with valid config but push deferred."""

    def test_real_push_returns_not_implemented(self):
        """Fully configured + real mode must return not_implemented."""
        with patch.dict(
            os.environ,
            {
                _ENV_ENABLED: "true",
                _ENV_APP_ID: "wx_appid_1234",
                _ENV_APP_SECRET: "valid_secret_value",
            },
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_100", _SAMPLE_TASK)

        assert result.success is False
        assert result.mode == "real"
        assert result.channel == "wechat"
        assert result.task_id == "task_100"
        assert result.status == "not_implemented"
        assert result.error_code == "not_implemented"
        assert result.error_message is not None
        assert "dry_run" in result.error_message

    def test_real_push_with_all_env_variants(self):
        """Test '1', 'yes', 'true' all count as enabled."""
        for truthy in ("true", "1", "yes", "TRUE", "True"):
            with patch.dict(
                os.environ,
                {
                    _ENV_ENABLED: truthy,
                    _ENV_APP_ID: "wx_appid_1234",
                    _ENV_APP_SECRET: "valid_secret_value",
                },
                clear=True,
            ):
                channel = WeChatNotifyChannel(mode="real")
                result = channel.send_reminder("task_100", _SAMPLE_TASK)

            assert result.error_code == "not_implemented", (
                f"Failed for enabled={truthy!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Output structure stability tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNotifyResultOutputStructure:
    """NotifyResult: output structure must be stable across all modes."""

    _REQUIRED_FIELDS = {
        "success", "mode", "channel", "task_id",
        "status", "error_code", "error_message",
    }

    def _assert_structure(self, result: NotifyResult):
        result_dict = {
            "success": result.success,
            "mode": result.mode,
            "channel": result.channel,
            "task_id": result.task_id,
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
        assert result_dict.keys() == self._REQUIRED_FIELDS, (
            f"Expected fields {self._REQUIRED_FIELDS}, got {set(result_dict.keys())}"
        )

    def test_dry_run_output_has_all_fields(self):
        channel = WeChatNotifyChannel(mode="dry_run")
        self._assert_structure(channel.send_reminder("task_100", _SAMPLE_TASK))

    def test_test_mode_output_has_all_fields(self):
        channel = WeChatNotifyChannel(mode="test_mode")
        self._assert_structure(channel.send_reminder("task_100", _SAMPLE_TASK))

    def test_not_configured_output_has_all_fields(self):
        with patch.dict(os.environ, {}, clear=True):
            channel = WeChatNotifyChannel(mode="real")
            self._assert_structure(channel.send_reminder("task_100", _SAMPLE_TASK))

    def test_missing_credentials_output_has_all_fields(self):
        with patch.dict(
            os.environ,
            {_ENV_ENABLED: "true", _ENV_APP_ID: "", _ENV_APP_SECRET: ""},
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            self._assert_structure(channel.send_reminder("task_100", _SAMPLE_TASK))

    def test_real_not_implemented_output_has_all_fields(self):
        with patch.dict(
            os.environ,
            {
                _ENV_ENABLED: "true",
                _ENV_APP_ID: "wx_appid_1234",
                _ENV_APP_SECRET: "valid_secret_value",
            },
            clear=True,
        ):
            channel = WeChatNotifyChannel(mode="real")
            self._assert_structure(channel.send_reminder("task_100", _SAMPLE_TASK))
