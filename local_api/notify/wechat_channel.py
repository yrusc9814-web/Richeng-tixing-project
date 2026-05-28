"""Phase 15 — WeChat reminder notification channel (safety layer).

WeChat is used as a notification/reminder exit point for the personal
schedule reminder hub system. This module provides a safety layer with
three operating modes, explicit error codes, and no real push capability
until a later phase.

Safety properties:
  - dry_run mode: validates nothing, returns skipped (no external calls)
  - test_mode: simulated notification with deterministic result
  - real mode: validates config/credentials, returns not_implemented
  - All failure paths return structured NotifyResult with error_code/error_message
"""

from __future__ import annotations

import os
from typing import Optional

from .base import NotifyChannel, NotifyResult


# ── Environment variable names ─────────────────────────────────────────────

_ENV_ENABLED = "WECHAT_REMINDER_ENABLED"
_ENV_APP_ID = "WECHAT_APP_ID"
_ENV_APP_SECRET = "WECHAT_APP_SECRET"


class WeChatNotifyChannel(NotifyChannel):
    """WeChat reminder notification channel with safety layer.

    Designed as a notification/reminder exit point, NOT a sync adapter.
    Provides three operating modes:

    dry_run (mode='dry_run')
        Returns success with status='skipped'. No config/credential checks.
        Safe for cron/scheduler validation without side effects.

    test_mode (mode='test_mode')
        Simulates a successful notification with status='simulated'.
        Returns a deterministic result for testing output structure stability.
        No real WeChat message is sent.

    real (mode='real')
        Validates platform configuration and credentials, then either
        returns not_implemented (real push deferred) or a config error.

    Args:
        mode: Operating mode — 'dry_run', 'test_mode', or 'real'.

    Raises:
        ValueError: If mode is not a valid operating mode.
    """

    # ── Constants ──────────────────────────────────────────────────────────

    MODE_DRY_RUN = "dry_run"
    MODE_TEST = "test_mode"
    MODE_REAL = "real"

    _VALID_MODES = frozenset({MODE_DRY_RUN, MODE_TEST, MODE_REAL})

    # ── Construction ───────────────────────────────────────────────────────

    def __init__(self, mode: str = MODE_DRY_RUN):
        if mode not in self._VALID_MODES:
            raise ValueError(
                f"WeChatNotifyChannel mode must be one of "
                f"{sorted(self._VALID_MODES)}, got: {mode}"
            )
        self._mode = mode

    # ── NotifyChannel interface ────────────────────────────────────────────

    @property
    def channel_name(self) -> str:
        return "wechat"

    def send_reminder(self, task_id: str, task_data: dict) -> NotifyResult:
        """Send a WeChat reminder notification for the given task.

        Mode-dependent behavior:
        - dry_run: Returns skipped, no checks or external calls.
        - test_mode: Returns simulated success with deterministic result.
        - real: Validates config/credentials, then returns not_implemented.

        Args:
            task_id: Identifier of the task to remind about.
            task_data: Task details as a dict (title, priority, etc.).

        Returns:
            NotifyResult — see class docstring for per-mode behavior.
        """
        # 1. Dry-run → skip immediately (no config/credential checks)
        if self._mode == self.MODE_DRY_RUN:
            return NotifyResult(
                success=True,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="skipped",
            )

        # 2. Test mode → simulated notification (no config/credential checks)
        if self._mode == self.MODE_TEST:
            return NotifyResult(
                success=True,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="simulated",
            )

        # 3. Real mode → validate configuration
        assert self._mode == self.MODE_REAL

        # 3a. Platform not configured
        if not self._is_platform_configured():
            return NotifyResult(
                success=False,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="config_error",
                error_code="platform_not_configured",
                error_message=(
                    "WeChat notification channel is not configured. "
                    f"Set {_ENV_ENABLED}=true to enable."
                ),
            )

        # 3b. Credentials missing
        if not self._has_credentials():
            return NotifyResult(
                success=False,
                mode=self._mode,
                channel="wechat",
                task_id=task_id,
                status="config_error",
                error_code="missing_credentials",
                error_message=(
                    "WeChat app credentials not found. "
                    f"Set {_ENV_APP_ID} and {_ENV_APP_SECRET}."
                ),
            )

        # 3c. Real push: not implemented (deferred to later phase)
        return NotifyResult(
            success=False,
            mode=self._mode,
            channel="wechat",
            task_id=task_id,
            status="not_implemented",
            error_code="not_implemented",
            error_message=(
                "Real WeChat push not yet implemented. "
                "Use mode='dry_run' or mode='test_mode' for safe validation."
            ),
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _is_platform_configured() -> bool:
        """Return True if the WeChat notification platform is configured.

        Checks for the WECHAT_REMINDER_ENABLED environment variable set to
        a truthy value ('true', '1', 'yes').
        """
        return os.environ.get(_ENV_ENABLED, "").strip().lower() in {
            "true", "1", "yes",
        }

    @staticmethod
    def _has_credentials() -> bool:
        """Return True if WeChat app credentials are present.

        Checks for non-empty WECHAT_APP_ID and WECHAT_APP_SECRET
        environment variables.
        """
        app_id = os.environ.get(_ENV_APP_ID, "").strip()
        app_secret = os.environ.get(_ENV_APP_SECRET, "").strip()
        return bool(app_id) and bool(app_secret)
