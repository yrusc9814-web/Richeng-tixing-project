"""Phase 8B — Tests for SyncService adapter bridge.

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_service.py -v
"""

from __future__ import annotations

from typing import Optional

import pytest

from local_api import config
from local_api.adapters import AdapterResult, SyncAdapter
from local_api.database import get_db, init_db, reset_db
from local_api.services.sync_service import SyncService
from local_api.services.sync_state_service import get_sync_state, get_sync_state_by_key
from local_api.services.sync_log_service import list_sync_logs


# ── Mock adapters ─────────────────────────────────────────────────────


class MockSuccessAdapter(SyncAdapter):
    """Adapter that always succeeds and returns a deterministic external_id."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=True,
            external_id="ext_abc123",
            sync_result="success",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


class MockFailAdapter(SyncAdapter):
    """Adapter that fails with a retryable error (network)."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=False,
            error_code="network",
            error_message="Connection refused",
            sync_result="failed",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


class MockPermanentFailAdapter(SyncAdapter):
    """Adapter that fails with a permanent error (auth_failed)."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=False,
            error_code="auth_failed",
            error_message="Invalid credentials",
            sync_result="failed",
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


class MockAdapterConfigInvalid(SyncAdapter):
    """Adapter that fails config validation."""

    @property
    def target_name(self) -> str:
        return "apple_calendar"

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (False, "Missing API key")

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        raise AssertionError("push should not be called when config is invalid")

    def pull(self, external_id: str) -> Optional[dict]:
        return None


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


def _insert_task(task_id: str) -> None:
    """Insert a minimal task row."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    conn = get_db()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, created_channel, created_at, updated_at
        ) VALUES (?, ?, 'pending', 'P2', 'api_test', ?, ?)""",
        (task_id, f"Task {task_id}", now, now),
    )
    conn.commit()


def _logs(sync_id: str) -> list[dict]:
    records, _ = list_sync_logs(sync_id=sync_id)
    return records


# ── Tests: Routing ────────────────────────────────────────────────────


class TestRouteAdapter:
    """Verify the adapter routing logic."""

    def test_routes_apple_calendar(self):
        svc = SyncService(adapters=[MockSuccessAdapter()])
        adapter = svc._route_adapter("apple_calendar")
        assert adapter is not None
        assert adapter.target_name == "apple_calendar"

    def test_routes_apple_reminder_via_prefix_match(self):
        """apple_reminder routes to apple_calendar adapter (prefix match)."""
        svc = SyncService(adapters=[MockSuccessAdapter()])
        adapter = svc._route_adapter("apple_reminder")
        assert adapter is not None
        assert adapter.target_name == "apple_calendar"

    def test_returns_none_for_unregistered_target(self):
        svc = SyncService(adapters=[MockSuccessAdapter()])
        assert svc._route_adapter("unknown_target") is None

    def test_returns_none_when_no_adapters(self):
        svc = SyncService()
        assert svc._route_adapter("apple_calendar") is None


# ── Tests: run_task_sync — success path ───────────────────────────────


class TestRunTaskSyncSuccess:
    """SyncService fully succeeds with a working adapter."""

    def test_routes_and_records_success(self):
        _insert_task("task_svc_001")
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_001", "apple_calendar")

        assert result["success"] is True
        assert result["sync_status"] == "synced"
        assert result["external_id"] == "ext_abc123"
        assert result["sync_id"] is not None

    def test_creates_sync_state_and_persists(self):
        _insert_task("task_svc_002")
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_002", "apple_calendar")

        # Verify persisted in DB
        record = get_sync_state(result["sync_id"])
        assert record is not None
        assert record["sync_status"] == "synced"
        assert record["external_id"] == "ext_abc123"

    def test_writes_sync_log_on_success(self):
        _insert_task("task_svc_003")
        svc = SyncService(adapters=[MockSuccessAdapter()])

        result = svc.run_task_sync("task_svc_003", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "success"
        assert logs[0]["triggered_by"] == "sync_service"

    def test_reuses_existing_sync_state(self):
        _insert_task("task_svc_004")
        svc = SyncService(adapters=[MockSuccessAdapter()])

        # First call creates sync_state
        r1 = svc.run_task_sync("task_svc_004", "apple_calendar")
        # Second call reuses it
        r2 = svc.run_task_sync("task_svc_004", "apple_calendar")

        assert r1["sync_id"] == r2["sync_id"]
        logs = _logs(r1["sync_id"])
        assert len(logs) == 2  # one per call


# ── Tests: run_task_sync — error paths ────────────────────────────────


class TestRunTaskSyncError:
    """SyncService error handling."""

    def test_invalid_target_returns_error(self):
        _insert_task("task_svc_inv")
        svc = SyncService()

        result = svc.run_task_sync("task_svc_inv", "invalid_target")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "invalid_target"
        assert "invalid_target" in (result["error_message"] or "")

    def test_no_adapter_returns_adapter_not_found(self):
        _insert_task("task_svc_no_adapter")
        svc = SyncService()

        result = svc.run_task_sync("task_svc_no_adapter", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "adapter_not_found"

    def test_retryable_failure_returns_failed_status(self):
        _insert_task("task_svc_retry")
        svc = SyncService(adapters=[MockFailAdapter()])

        result = svc.run_task_sync("task_svc_retry", "apple_calendar")

        assert result["success"] is False
        # network error → retryable → "failed"
        assert result["sync_status"] == "failed"
        assert result["error_code"] == "network"

    def test_retryable_failure_writes_log(self):
        _insert_task("task_svc_retry_log")
        svc = SyncService(adapters=[MockFailAdapter()])

        result = svc.run_task_sync("task_svc_retry_log", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "network"

    def test_permanent_failure_returns_failed_permanent(self):
        _insert_task("task_svc_perm")
        svc = SyncService(adapters=[MockPermanentFailAdapter()])

        result = svc.run_task_sync("task_svc_perm", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "auth_failed"

    def test_permanent_failure_writes_log(self):
        _insert_task("task_svc_perm_log")
        svc = SyncService(adapters=[MockPermanentFailAdapter()])

        result = svc.run_task_sync("task_svc_perm_log", "apple_calendar")

        logs = _logs(result["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "failed"
        assert logs[0]["error_code"] == "auth_failed"

    def test_config_invalid_returns_error_before_push(self):
        _insert_task("task_svc_config")
        svc = SyncService(adapters=[MockAdapterConfigInvalid()])

        result = svc.run_task_sync("task_svc_config", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "adapter_config_invalid"

    def test_task_not_found_error(self):
        """Sync where task was deleted after sync_state creation."""
        # Create task + sync_state, then delete the task
        _insert_task("task_svc_orphan")
        svc = SyncService(adapters=[MockSuccessAdapter()])
        first_result = svc.run_task_sync("task_svc_orphan", "apple_calendar")
        assert first_result["success"] is True

        # Delete the task behind SyncService's back
        conn = get_db()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM tasks WHERE task_id = 'task_svc_orphan'")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        # Second call: task exists in sync_state but not in tasks table
        result = svc.run_task_sync("task_svc_orphan", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed_permanent"
        assert result["error_code"] == "task_not_found"
        assert result["sync_id"] == first_result["sync_id"]

    def test_adapter_exception_is_caught(self):
        """Adapter that raises during push is caught and logged."""
        _insert_task("task_svc_exc")

        class CrashingAdapter(SyncAdapter):
            @property
            def target_name(self) -> str:
                return "apple_calendar"

            def validate_config(self) -> tuple[bool, Optional[str]]:
                return (True, None)

            def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
                raise RuntimeError("Adapter crashed!")

            def pull(self, external_id: str) -> Optional[dict]:
                return None

        svc = SyncService(adapters=[CrashingAdapter()])
        result = svc.run_task_sync("task_svc_exc", "apple_calendar")

        assert result["success"] is False
        assert result["sync_status"] == "failed"
        assert result["error_code"] == "adapter_exception"


# ── Tests: add_adapter ────────────────────────────────────────────────


class TestAddAdapter:
    """Registration of adapters after construction."""

    def test_add_adapter_before_call(self):
        svc = SyncService()
        svc.add_adapter(MockSuccessAdapter())
        assert svc._route_adapter("apple_calendar") is not None

    def test_add_multiple_adapters(self):
        svc = SyncService()
        svc.add_adapter(MockSuccessAdapter())
        svc.add_adapter(MockFailAdapter())
        assert len(svc._adapters) == 2
