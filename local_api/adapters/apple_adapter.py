"""Phase 8A — Apple Calendar / Reminders adapters.

Contains two adapter implementations:

1. MockAppleAdapter (Phase 8A): Always-succeeds mock for tests.
2. AppleSyncAdapter (Phase 14): Production-ready adapter with safety layer,
   dry-run mode, test markers, and platform/error checking.

AppleSyncAdapter safety properties:
  - dry_run mode: validates config, returns skipped (no external calls)
  - test_mode: simulated push with [SYNC-TEST] title prefix
  - Non-macOS platforms: returns platform_unsupported error
  - Real EventKit push: deferred (requires macOS build)
  - All failure paths return structured AdapterResult with error_code/error_message
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from typing import Optional

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
        Validates platform (requires macOS) and attempts EventKit push.
        Currently returns not_implemented — real EventKit integration is
        deferred to a later phase targeting macOS builds.

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
        - real (macOS): Deferred — returns not_implemented.

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

        # 4. Real mode: EventKit integration deferred (macOS only path)
        return AdapterResult(
            success=False,
            sync_result="failed",
            error_code="not_implemented",
            error_message=(
                "Real Apple EventKit push not yet implemented. "
                "Use --dry-run or test_mode=True for safe validation."
            ),
        )

    def pull(self, external_id: str) -> Optional[dict]:
        """Pull a record from Apple Calendar/Reminders.

        Real EventKit pull is deferred to a later phase.
        Currently returns None (not supported).
        """
        return None

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
