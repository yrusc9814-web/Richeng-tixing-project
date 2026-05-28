"""Phase 10 — App-level sync integration tests.

Verifies that sync_service, sync_scheduler, and sync routes are properly
wired through the FastAPI app lifespan and accessible via app.state.

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_app_sync_integration.py -v

Uses FastAPI TestClient with lifespan to verify real app wiring.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# Force test database + auth token before importing app
os.environ["HERMES_API_TOKEN"] = "test-token-hermes-local-4.3"

from local_api.main import app
from local_api.database import get_db, init_db, reset_db
from local_api.scheduler.sync_scheduler import SyncScheduler
from local_api.services.sync_service import SyncService

TEST_TOKEN = "test-token-hermes-local-4.3"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_TOKEN}"}


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the database before each test."""
    reset_db()
    init_db()
    yield
    reset_db()


@pytest.fixture
def client():
    """TestClient with lifespan active — app.state populated by lifespan."""
    with TestClient(app) as c:
        yield c


# ── Helpers ────────────────────────────────────────────────────────────────


def _create_task(client: TestClient, title: str = "Phase 10 test") -> str:
    """Create a task via the API and return its ID."""
    resp = client.post("/api/tasks", json={"title": title}, headers=AUTH_HEADER)
    assert resp.status_code == 201, f"Create task failed: {resp.text}"
    return resp.json()["task_id"]


def _insert_task_direct(task_id: str) -> None:
    """Insert a minimal task row directly into the DB."""
    conn = get_db()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    conn.execute(
        """INSERT INTO tasks (
            task_id, title, status, priority, created_channel, created_at, updated_at
        ) VALUES (?, ?, 'pending', 'P2', 'api_test', ?, ?)""",
        (task_id, f"Task {task_id}", now, now),
    )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — App state wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestAppStateWiring:
    """Verify the lifespan correctly populates app.state."""

    def test_sync_service_on_app_state(self, client):
        """sync_service must be a SyncService instance."""
        svc = getattr(app.state, "sync_service", None)
        assert svc is not None, "sync_service not found on app.state"
        assert isinstance(svc, SyncService)

    def test_scheduler_on_app_state(self, client):
        """scheduler must be a SyncScheduler instance."""
        sched = getattr(app.state, "scheduler", None)
        assert sched is not None, "scheduler not found on app.state"
        assert isinstance(sched, SyncScheduler)

    def test_engine_on_app_state(self, client):
        """engine must exist on app.state (SyncEngine or similar)."""
        eng = getattr(app.state, "engine", None)
        assert eng is not None, "engine not found on app.state"

    def test_mock_adapters_are_enabled(self, client):
        """Mock adapters should be present when ADAPTER_ENABLED is True."""
        from local_api import config

        if config.ADAPTER_ENABLED:
            svc = app.state.sync_service
            # SyncService should have at least 1 adapter
            assert len(svc._adapters) >= 1, "Expected at least one adapter"


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Sync routes via real app
# ═══════════════════════════════════════════════════════════════════════════


class TestSyncPushRoute:
    """POST /api/sync/tasks/{task_id}/push"""

    def test_push_success(self, client):
        task_id = _create_task(client)
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["task_id"] == task_id
        assert data["target"] == "apple_calendar"
        assert data["direction"] == "push"
        assert data["status"] == "synced"
        assert data["error"] is None
        assert data["sync_id"] is not None

    def test_push_invalid_target(self, client):
        task_id = _create_task(client)
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "invalid_system"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["status"] == "failed_permanent"
        assert data["error"] is not None

    def test_push_without_auth_returns_401(self, client):
        task_id = _create_task(client)
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
        )
        assert resp.status_code == 401


class TestSyncPullRoute:
    """POST /api/sync/tasks/{task_id}/pull"""

    def test_pull_unsupported(self, client):
        task_id = _create_task(client)
        resp = client.post(
            f"/api/sync/tasks/{task_id}/pull",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["direction"] == "pull"
        assert data["status"] == "unsupported"

    def test_pull_without_auth_returns_401(self, client):
        resp = client.post("/api/sync/tasks/task_999/pull")
        assert resp.status_code == 401


class TestSyncStatusRoute:
    """GET /api/sync/tasks/{task_id}/status"""

    def test_status_not_synced_before_push(self, client):
        task_id = _create_task(client)
        resp = client.get(
            f"/api/sync/tasks/{task_id}/status",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_synced"

    def test_status_synced_after_push(self, client):
        task_id = _create_task(client)
        client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        resp = client.get(
            f"/api/sync/tasks/{task_id}/status",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "synced"
        assert data["target"] == "apple_calendar"


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Scheduler via app.state
# ═══════════════════════════════════════════════════════════════════════════


class TestSchedulerViaAppState:
    """Verify SyncScheduler can be invoked through app.state.scheduler."""

    def test_scheduler_sync_task(self, client):
        sched = app.state.scheduler
        assert isinstance(sched, SyncScheduler)

        _insert_task_direct("phase10_sched_001")

        result = sched.sync_task("phase10_sched_001", "apple_calendar")
        assert result["success"] is True
        assert result["status"] == "synced"
        assert result["target"] == "apple_calendar"
        assert result["sync_id"] is not None

    def test_scheduler_sync_task_invalid_target(self, client):
        sched = app.state.scheduler
        _insert_task_direct("phase10_sched_inv")

        result = sched.sync_task("phase10_sched_inv", "invalid_target")
        assert result["success"] is False
        assert result["status"] == "skipped"

    def test_scheduler_sync_task_all_targets(self, client):
        sched = app.state.scheduler
        _insert_task_direct("phase10_sched_all")

        result = sched.sync_task_all_targets("phase10_sched_all")
        assert result["task_id"] == "phase10_sched_all"
        assert result["total"] >= 1
        assert result["success_count"] >= 1

    def test_scheduler_sync_pending_empty(self, client):
        sched = app.state.scheduler
        result = sched.sync_pending(limit=5)
        assert result["processed"] == 0
        assert result["success_count"] == 0

    def test_scheduler_sync_pending_with_data(self, client):
        sched = app.state.scheduler
        _insert_task_direct("phase10_sched_pend_01")

        # Create a pending sync_state
        from local_api.services.sync_state_service import create_sync_state

        create_sync_state(task_id="phase10_sched_pend_01", sync_target="apple_calendar")

        result = sched.sync_pending(limit=10)
        assert result["processed"] >= 1
        assert result["success_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Health & baseline
# ═══════════════════════════════════════════════════════════════════════════


class TestHealthCheck:
    """/health is a public endpoint — no auth required."""

    def test_health_without_auth_returns_200(self, client):
        """Health check is a public path, bypasses auth middleware."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "4.4.0"

    def test_health_with_auth_also_works(self, client):
        """Health also works when auth token is provided."""
        resp = client.get("/health", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
