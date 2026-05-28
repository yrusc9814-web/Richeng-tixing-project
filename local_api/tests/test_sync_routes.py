"""Phase 8C — Tests for sync task routes (push, pull, status).

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_routes.py -v

Uses FastAPI TestClient so no server needs to be running.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi.testclient import TestClient

# Force test database path before any import
import os
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "test_data" / "test_tasks.db"
os.environ["HERMES_API_TOKEN"] = "test-token-hermes-local-4.3"

# Now import app — it will pick up the test DB
# Now import app — it will pick up the test DB
from local_api.main import app
from local_api.database import reset_db, init_db
from local_api.services.sync_service import SyncService

TEST_TOKEN = "test-token-hermes-local-4.3"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the database before each test."""
    reset_db()
    init_db()
    yield
    reset_db()


@pytest.fixture(autouse=True)
def init_sync_service():
    """Set up SyncService on app.state for routes that depend on it."""
    from local_api import config
    from local_api.adapters.apple_adapter import MockAppleAdapter

    adapters = [MockAppleAdapter()] if config.ADAPTER_ENABLED else []
    app.state.sync_service = SyncService(adapters=adapters)
    yield
    app.state.sync_service = None


client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────


def _create_task(title: str = "Sync route test task") -> str:
    """Create a task via the API and return its ID."""
    resp = client.post("/api/tasks", json={"title": title}, headers=AUTH_HEADER)
    assert resp.status_code == 201, f"Task creation failed: {resp.text}"
    return resp.json()["task_id"]


# ── Tests: POST .../push ──────────────────────────────────────────────


class TestPushEndpoint:
    """POST /api/sync/tasks/{task_id}/push"""

    def test_push_success(self):
        task_id = _create_task()
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
        assert data["external_id"] is not None
        assert data["sync_id"] is not None
        assert data["error"] is None

    def test_push_with_explicit_apple_reminder(self):
        task_id = _create_task()
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_reminder"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["target"] == "apple_reminder"
        assert data["status"] == "synced"

    def test_push_invalid_target(self):
        task_id = _create_task()
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "invalid_system"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200  # Route succeeds, service returns error
        data = resp.json()
        assert data["ok"] is False
        assert data["status"] == "failed_permanent"
        assert "Invalid sync_target" in (data["error"] or "")

    def test_push_task_not_found(self):
        """Push for non-existent task returns service error."""
        resp = client.post(
            "/api/sync/tasks/nonexistent_task/push",
            json={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        # SyncService creates sync_state first, which validates task exists
        assert data["status"] == "failed_permanent"
        assert data["error"] is not None

    def test_push_without_auth_returns_401(self):
        task_id = _create_task()
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
        )
        assert resp.status_code == 401

    def test_push_with_extra_fields_rejected(self):
        """model_config extra='forbid' rejects unknown fields."""
        task_id = _create_task()
        resp = client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar", "payload": "extra_data"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


# ── Tests: POST .../pull ──────────────────────────────────────────────


class TestPullEndpoint:
    """POST /api/sync/tasks/{task_id}/pull"""

    def test_pull_returns_unsupported(self):
        task_id = _create_task()
        resp = client.post(
            f"/api/sync/tasks/{task_id}/pull",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["direction"] == "pull"
        assert data["status"] == "unsupported"
        assert "not yet supported" in (data["error"] or "")

    def test_pull_without_auth_returns_401(self):
        resp = client.post("/api/sync/tasks/task_1/pull")
        assert resp.status_code == 401


# ── Tests: GET .../status ─────────────────────────────────────────────


class TestStatusEndpoint:
    """GET /api/sync/tasks/{task_id}/status"""

    def test_status_returns_not_synced_before_push(self):
        task_id = _create_task()
        resp = client.get(
            f"/api/sync/tasks/{task_id}/status",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "not_synced"
        assert data["error"] is None

    def test_status_returns_synced_after_push(self):
        task_id = _create_task()
        # Push first
        client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        # Then check status
        resp = client.get(
            f"/api/sync/tasks/{task_id}/status",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "synced"
        assert data["target"] == "apple_calendar"
        assert data["external_id"] is not None
        assert data["sync_id"] is not None

    def test_status_with_specific_target(self):
        task_id = _create_task()
        # Push to apple_calendar
        client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        # Status for apple_calendar
        resp = client.get(
            f"/api/sync/tasks/{task_id}/status",
            params={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "synced"

    def test_status_with_target_not_synced(self):
        task_id = _create_task()
        # Push to apple_calendar
        client.post(
            f"/api/sync/tasks/{task_id}/push",
            json={"sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        # Status for apple_reminder (not synced yet)
        resp = client.get(
            f"/api/sync/tasks/{task_id}/status",
            params={"sync_target": "apple_reminder"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_synced"

    def test_status_without_auth_returns_401(self):
        resp = client.get("/api/sync/tasks/task_1/status")
        assert resp.status_code == 401


# ── Tests: Registration in main.py ────────────────────────────────────


class TestRouterRegistration:
    """Verify routes are accessible through the app."""

    def test_push_route_registered(self):
        """The push endpoint exists (even if no auth — returns 401, not 404)."""
        resp = client.post("/api/sync/tasks/task_1/push", json={"sync_target": "apple_calendar"})
        assert resp.status_code == 401  # Auth required, not 404

    def test_pull_route_registered(self):
        resp = client.post("/api/sync/tasks/task_1/pull")
        assert resp.status_code == 401  # Not 404

    def test_status_route_registered(self):
        resp = client.get("/api/sync/tasks/task_1/status")
        assert resp.status_code == 401  # Not 404
