"""Phase 16 — Local integration tests for sync + notify chain.

Exercises the full local pipeline WITHOUT real external services:
  sync trigger → scheduler → SyncService → Apple safety adapter
  └─ then separately → WeChat notify safety layer

This test verifies end-to-end that:
  - AppleSyncAdapter dry-run/test-mode produce correct results (direct call)
  - SyncService correctly routes and validates with AppleSyncAdapter
  - SyncScheduler + SyncService integration handles all target paths
  - WeChatNotifyChannel dry-run/test-mode produce correct results
  - Error paths produce explicit error_code output (no silent success)
  - Skip/duplicate detection prevents redundant work

Design principle: no helper/orchestration module needed — the test
directly instantiates adapters, services, and channels, and exercises
them programmatically. This keeps the integration footprint minimal.

Note on platform: AppleSyncAdapter.validate_config() returns (False, ...)
on non-macOS platforms. Through SyncService this manifests as
adapter_config_invalid. The adapter's dry-run/test-mode mode logic is
tested via direct calls (bypasses SyncService validation).

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_local_integration.py -v
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from local_api.adapters.apple_adapter import AppleSyncAdapter, MockAppleAdapter
from local_api.config import ALLOWED_SYNC_TARGETS
from local_api.database import get_db, init_db, reset_db
from local_api.notify.wechat_channel import (
    WeChatNotifyChannel,
    _ENV_APP_ID,
    _ENV_APP_SECRET,
    _ENV_ENABLED,
)
from local_api.scheduler.sync_scheduler import SyncScheduler
from local_api.services.sync_service import SyncService
from local_api.services.sync_state_service import (
    create_sync_state,
    transition_sync_state,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


# ── Test helpers ──────────────────────────────────────────────────────────


def _insert_task(task_id: str, title: str = "Test integration task",
                 status: str = "pending") -> None:
    conn = get_db()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    conn.execute(
        "INSERT INTO tasks (task_id, title, status, priority, "
        "created_channel, created_at, updated_at) "
        "VALUES (?, ?, ?, 'P2', 'api_test', ?, ?)",
        (task_id, title, status, now, now),
    )
    conn.commit()


def _make_service(adapter_target: str = "apple_calendar",
                  dry_run: bool = False,
                  test_mode: bool = False) -> SyncService:
    adapter = AppleSyncAdapter(target=adapter_target,
                                dry_run=dry_run,
                                test_mode=test_mode)
    return SyncService(adapters=[adapter])


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Apple adapter direct safety verification
# ═══════════════════════════════════════════════════════════════════════════


class TestAppleAdapterDirectSafety:
    """AppleSyncAdapter dry-run/test-mode/real-mode — direct calls.

    Tests the adapter's internal mode logic without going through
    SyncService (which calls validate_config first and will reject
    on non-macOS).
    """

    def test_dry_run_push_returns_skipped(self):
        adapter = AppleSyncAdapter(target="apple_calendar", dry_run=True)
        result = adapter.push({"title": "test"}, {"sync_id": "s1", "sync_target": "apple_calendar"})
        assert result.success is True
        assert result.sync_result == "skipped"

    def test_test_mode_push_returns_success_with_test_prefix(self):
        adapter = AppleSyncAdapter(target="apple_calendar", test_mode=True)
        result = adapter.push({"title": "Standup"}, {"sync_id": "s1", "sync_target": "apple_calendar"})
        assert result.success is True
        assert result.sync_result == "success"
        assert result.external_id.startswith("test_")

    def test_real_mode_on_non_macos_returns_platform_unsupported(self):
        adapter = AppleSyncAdapter(target="apple_calendar")
        result = adapter.push({"title": "test"}, {"sync_id": "s1", "sync_target": "apple_calendar"})
        assert result.success is False
        assert result.error_code == "platform_unsupported"

    def test_validate_config_on_non_macos_returns_false(self):
        adapter = AppleSyncAdapter(target="apple_calendar")
        valid, msg = adapter.validate_config()
        assert valid is False
        assert "macOS" in msg

    def test_apple_reminder_routes_to_calendar_adapter(self):
        """Verify that apple_reminder target routes correctly through SyncService._route_adapter."""
        adapter = AppleSyncAdapter(target="apple_calendar", dry_run=True)
        service = SyncService(adapters=[adapter])
        routed = service._route_adapter("apple_reminder")
        assert routed is not None
        assert routed.target_name == "apple_calendar"


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — SyncService + SyncScheduler integration
# ═══════════════════════════════════════════════════════════════════════════


class TestSyncServiceIntegration:
    """SyncScheduler → SyncService integration on non-macOS.

    On non-macOS, AppleSyncAdapter.validate_config() returns (False, ...),
    so SyncService returns adapter_config_invalid. This IS the correct
    safety behavior — the test verifies the integration chain works.
    """

    def test_apple_calendar_through_service_returns_config_error(self):
        """SyncService routes apple_calendar, fails at validate_config on non-macOS."""
        _insert_task("task_sc_001")
        service = _make_service(dry_run=True)
        result = service.run_task_sync("task_sc_001", "apple_calendar")
        assert result["success"] is False
        assert result["error_code"] == "adapter_config_invalid"
        assert "macOS" in (result.get("error_message") or "")

    def test_apple_reminder_through_service_also_config_error(self):
        """apple_reminder routes via apple_ prefix, same validate_config failure."""
        _insert_task("task_sc_002")
        service = _make_service(dry_run=True)
        result = service.run_task_sync("task_sc_002", "apple_reminder")
        assert result["success"] is False
        assert result["error_code"] == "adapter_config_invalid"

    def test_mock_adapter_through_service_succeeds(self):
        """MockAppleAdapter (no platform check) works through SyncService."""
        _insert_task("task_sc_003")
        adapter = MockAppleAdapter()
        service = SyncService(adapters=[adapter])
        result = service.run_task_sync("task_sc_003", "apple_calendar")
        assert result["success"] is True
        assert result["sync_status"] == "synced"

    def test_sync_scheduler_invalid_target_has_reason_field(self):
        """Scheduler sync_task with invalid target returns success=False + reason."""
        _insert_task("task_sc_004")
        scheduler = SyncScheduler(sync_service=_make_service(dry_run=True))
        result = scheduler.sync_task("task_sc_004", "bad_target")
        assert result["success"] is False
        assert result["status"] == "skipped"
        assert "reason" in result
        assert "Invalid" in result["reason"]

    def test_sync_scheduler_nonexistent_task(self):
        """Scheduler sync_task with nonexistent task returns error."""
        scheduler = SyncScheduler(sync_service=_make_service(dry_run=True))
        result = scheduler.sync_task("no_such_task", "apple_calendar")
        assert result["success"] is False
        assert result.get("error") is not None

    def test_sync_scheduler_all_targets_with_mock_adapter(self):
        """sync_task_all_targets with MockAppleAdapter succeeds."""
        _insert_task("task_sc_005")
        adapter = MockAppleAdapter()
        scheduler = SyncScheduler(sync_service=SyncService(adapters=[adapter]))
        result = scheduler.sync_task_all_targets("task_sc_005")
        assert result["task_id"] == "task_sc_005"
        assert result["total"] == len(ALLOWED_SYNC_TARGETS)
        assert result["success_count"] == len(ALLOWED_SYNC_TARGETS)
        assert result["skip_count"] == 0

    def test_sync_pending_with_mock_adapter(self):
        """sync_pending with MockAppleAdapter processes pending records."""
        _insert_task("task_sc_006")
        adapter = MockAppleAdapter()
        scheduler = SyncScheduler(sync_service=SyncService(adapters=[adapter]))
        create_sync_state("task_sc_006", "apple_calendar")
        result = scheduler.sync_pending(limit=10)
        assert result["processed"] >= 1
        assert result["success_count"] >= 1

    def test_skip_detection_with_mock_adapter(self):
        """sync_task_all_targets skips already-terminal tasks."""
        _insert_task("task_sc_007")
        adapter = MockAppleAdapter()
        scheduler = SyncScheduler(sync_service=SyncService(adapters=[adapter]))

        # First sync: succeeds
        first = scheduler.sync_task_all_targets("task_sc_007")
        assert first["success_count"] == len(ALLOWED_SYNC_TARGETS)

        # Second sync: skips all (already synced)
        second = scheduler.sync_task_all_targets("task_sc_007")
        assert second["skip_count"] >= 1
        for r in second["results"]:
            if r["status"] == "skipped":
                assert "Already" in (r.get("reason") or "")

    def test_skip_count_in_output(self):
        """sync_task_all_targets output always has skip_count field."""
        _insert_task("task_sc_008")
        adapter = MockAppleAdapter()
        scheduler = SyncScheduler(sync_service=SyncService(adapters=[adapter]))
        result = scheduler.sync_task_all_targets("task_sc_008")
        assert "skip_count" in result
        assert isinstance(result["skip_count"], int)


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — WeChat notify integration
# ═══════════════════════════════════════════════════════════════════════════


class TestWeChatNotifyIntegration:
    """WeChatNotifyChannel in local integration context."""

    def test_dry_run_returns_skipped(self):
        channel = WeChatNotifyChannel(mode="dry_run")
        result = channel.send_reminder("task_wi_001", {"title": "Test"})
        assert result.success is True
        assert result.mode == "dry_run"
        assert result.channel == "wechat"
        assert result.status == "skipped"

    def test_test_mode_returns_simulated(self):
        channel = WeChatNotifyChannel(mode="test_mode")
        result = channel.send_reminder("task_wi_002", {"title": "Test"})
        assert result.success is True
        assert result.mode == "test_mode"
        assert result.status == "simulated"

    def test_real_not_configured_returns_explicit_error(self):
        with patch.dict(os.environ, {}, clear=True):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_wi_003", {"title": "x"})
        assert result.success is False
        assert result.error_code == "platform_not_configured"
        assert result.error_message is not None

    def test_real_missing_credentials_returns_explicit_error(self):
        with patch.dict(os.environ,
                        {_ENV_ENABLED: "true", _ENV_APP_ID: "", _ENV_APP_SECRET: ""},
                        clear=True):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_wi_004", {"title": "x"})
        assert result.success is False
        assert result.error_code == "missing_credentials"

    def test_real_not_implemented_with_full_config(self):
        with patch.dict(os.environ,
                        {_ENV_ENABLED: "true",
                         _ENV_APP_ID: "wx_id",
                         _ENV_APP_SECRET: "secret"},
                        clear=True):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_wi_005", {"title": "x"})
        assert result.success is False
        assert result.error_code == "not_implemented"
        assert result.error_message is not None


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Cross-layer output structure stability
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossLayerOutputStructure:
    """Verify sync and notify outputs have correct fields."""

    def test_sync_service_result_has_required_fields(self):
        _insert_task("task_cs_001")
        service = _make_service(dry_run=True)
        result = service.run_task_sync("task_cs_001", "apple_calendar")
        required = {"success", "sync_id", "sync_status", "external_id",
                    "error_code", "error_message"}
        assert required.issubset(result.keys())

    def test_scheduler_result_has_required_fields(self):
        _insert_task("task_cs_002")
        scheduler = SyncScheduler(sync_service=_make_service(dry_run=True))
        result = scheduler.sync_task("task_cs_002", "apple_calendar")
        required = {"success", "task_id", "target", "status"}
        assert required.issubset(result.keys())

    def test_scheduler_all_targets_has_summary_fields(self):
        _insert_task("task_cs_003")
        adapter = MockAppleAdapter()
        scheduler = SyncScheduler(sync_service=SyncService(adapters=[adapter]))
        result = scheduler.sync_task_all_targets("task_cs_003")
        required = {"task_id", "total", "success_count", "skip_count", "results"}
        assert required.issubset(result.keys())

    def test_notify_result_has_all_required_fields(self):
        channel = WeChatNotifyChannel(mode="dry_run")
        result = channel.send_reminder("task_cs_004", {"title": "test"})
        expected = {"success", "mode", "channel", "task_id",
                    "status", "error_code", "error_message"}
        actual = {k: v for k, v in result.__dict__.items() if k in expected}
        assert set(actual.keys()) == expected

    def test_wechat_error_has_code_and_message(self):
        """Every WeChat error path includes both error_code and error_message."""
        with patch.dict(os.environ, {}, clear=True):
            channel = WeChatNotifyChannel(mode="real")
            result = channel.send_reminder("task_cs_005", {"title": "x"})
        assert result.error_code is not None
        assert result.error_message is not None

