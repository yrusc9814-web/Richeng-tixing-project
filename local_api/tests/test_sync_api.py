"""Phase 4.4 — Tests for sync API (sync state + sync logs endpoints).

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_api.py -v

Uses FastAPI TestClient so no server needs to be running.
"""

import pytest
from fastapi.testclient import TestClient

# Force test database path before any import
import os
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "test_data" / "test_tasks.db"
os.environ["HERMES_API_TOKEN"] = "test-token-hermes-local-4.3"

# Now import app — it will pick up the test DB
from local_api.main import app
from local_api.database import reset_db, init_db

TEST_TOKEN = "test-token-hermes-local-4.3"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the database before each test."""
    reset_db()
    init_db()
    yield
    reset_db()


client = TestClient(app)


# ── Helper to create a task and return its ID ───────────────────────────────


def _create_task(title="Sync test task"):
    resp = client.post("/api/tasks", json={"title": title}, headers=AUTH_HEADER)
    assert resp.status_code == 201
    return resp.json()["task_id"]


# ── Sync State Tests ────────────────────────────────────────────────────────


class TestCreateSyncState:
    """POST /api/sync/state"""

    def test_create_minimal(self):
        task_id = _create_task()
        resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["sync_target"] == "apple_calendar"
        assert data["sync_status"] == "pending"
        assert data["sync_key"] == f"{task_id}:apple_calendar"
        assert data["sync_version"] == 1
        assert data["sync_id"].startswith("sync_")

    def test_create_full(self):
        task_id = _create_task()
        resp = client.post(
            "/api/sync/state",
            json={
                "task_id": task_id,
                "sync_target": "apple_reminder",
                "external_id": "ext_001",
                "sync_status": "synced",
                "payload_hash": "abc123",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["sync_target"] == "apple_reminder"
        assert data["external_id"] == "ext_001"
        assert data["sync_status"] == "synced"
        assert data["payload_hash"] == "abc123"

    def test_create_duplicate_key(self):
        task_id = _create_task()
        client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_invalid_target(self):
        task_id = _create_task()
        resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "google_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_invalid_status(self):
        task_id = _create_task()
        resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar", "sync_status": "invalid"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_nonexistent_task_returns_422(self):
        resp = client.post(
            "/api/sync/state",
            json={"task_id": "task_missing_001", "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        assert "Task not found" in resp.json()["detail"]


class TestGetSyncState:
    """GET /api/sync/state/{sync_id}"""

    def test_get_existing(self):
        task_id = _create_task()
        create_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = create_resp.json()["sync_id"]
        resp = client.get(f"/api/sync/state/{sync_id}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["sync_id"] == sync_id

    def test_get_nonexistent(self):
        resp = client.get("/api/sync/state/sync_nonexistent_999", headers=AUTH_HEADER)
        assert resp.status_code == 404


class TestGetSyncStateByKey:
    """GET /api/sync/state/by-key"""

    def test_get_by_key(self):
        task_id = _create_task()
        client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        resp = client.get(
            f"/api/sync/state/by-key?task_id={task_id}&sync_target=apple_calendar",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["sync_key"] == f"{task_id}:apple_calendar"

    def test_get_by_key_not_found(self):
        resp = client.get(
            "/api/sync/state/by-key?task_id=nonexistent&sync_target=apple_calendar",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404

    def test_get_by_key_invalid_target_returns_422(self):
        resp = client.get(
            "/api/sync/state/by-key?task_id=nonexistent&sync_target=google_calendar",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


class TestListSyncStates:
    """GET /api/sync/state"""

    def test_list_empty(self):
        resp = client.get("/api/sync/state", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_with_items(self):
        task_id = _create_task()
        client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_reminder"},
            headers=AUTH_HEADER,
        )
        resp = client.get("/api/sync/state", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_filter_by_target(self):
        task_id = _create_task()
        client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        resp = client.get(
            "/api/sync/state?symc_target=apple_calendar",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200

    def test_filter_by_status(self):
        task_id = _create_task()
        client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar", "sync_status": "synced"},
            headers=AUTH_HEADER,
        )
        resp = client.get(
            "/api/sync/state?sync_status=synced",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestUpdateSyncState:
    """PATCH /api/sync/state/{sync_id}"""

    def test_update_status(self):
        task_id = _create_task()
        create_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = create_resp.json()["sync_id"]
        resp = client.patch(
            f"/api/sync/state/{sync_id}",
            json={"sync_status": "synced", "external_id": "ext_002"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_status"] == "synced"
        assert data["external_id"] == "ext_002"

    def test_update_nonexistent(self):
        resp = client.patch(
            "/api/sync/state/sync_nonexistent_999",
            json={"sync_status": "synced"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404

    def test_update_invalid_status(self):
        task_id = _create_task()
        create_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = create_resp.json()["sync_id"]
        resp = client.patch(
            f"/api/sync/state/{sync_id}",
            json={"sync_status": "invalid_status"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


class TestDeleteSyncState:
    """DELETE /api/sync/state/{sync_id}"""

    def test_delete_existing(self):
        task_id = _create_task()
        create_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = create_resp.json()["sync_id"]
        resp = client.delete(f"/api/sync/state/{sync_id}", headers=AUTH_HEADER)
        assert resp.status_code == 204

        # Verify deleted
        get_resp = client.get(f"/api/sync/state/{sync_id}", headers=AUTH_HEADER)
        assert get_resp.status_code == 404

    def test_delete_nonexistent(self):
        resp = client.delete(
            "/api/sync/state/sync_nonexistent_999", headers=AUTH_HEADER
        )
        assert resp.status_code == 404


# ── Sync Log Tests ──────────────────────────────────────────────────────────


class TestCreateSyncLog:
    """POST /api/sync/logs"""

    def _create_sync_state(self):
        task_id = _create_task()
        resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        return resp.json()["sync_id"]

    def test_create_minimal(self):
        sync_id = self._create_sync_state()
        resp = client.post(
            "/api/sync/logs",
            json={
                "sync_id": sync_id,
                "sync_target": "apple_calendar",
                "sync_result": "success",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["sync_id"] == sync_id
        assert data["sync_result"] == "success"
        assert data["sync_attempt"] == 1
        assert data["log_id"].startswith("log_")

    def test_create_full(self):
        sync_id = self._create_sync_state()
        resp = client.post(
            "/api/sync/logs",
            json={
                "sync_id": sync_id,
                "local_task_id": "task_001",
                "sync_target": "apple_reminder",
                "sync_attempt": 3,
                "sync_result": "failed",
                "error_code": "ERR_001",
                "error_message": "Connection timeout",
                "drift_detected": True,
                "drift_fields": "title,priority",
                "payload_hash_before": "hash_before",
                "payload_hash_after": "hash_after",
                "external_id_before": "ext_before",
                "external_id_after": "ext_after",
                "request_id": "req_001",
                "triggered_by": "sync_worker",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["sync_result"] == "failed"
        assert data["sync_attempt"] == 3
        assert data["drift_detected"] is True
        assert data["error_code"] == "ERR_001"

    def test_create_invalid_result(self):
        sync_id = self._create_sync_state()
        resp = client.post(
            "/api/sync/logs",
            json={
                "sync_id": sync_id,
                "sync_target": "apple_calendar",
                "sync_result": "invalid_result",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_nonexistent_sync_returns_422(self):
        resp = client.post(
            "/api/sync/logs",
            json={
                "sync_id": "sync_missing_001",
                "sync_target": "apple_calendar",
                "sync_result": "failed",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        assert "Sync state not found" in resp.json()["detail"]

    def test_error_message_is_redacted(self):
        sync_id = self._create_sync_state()
        resp = client.post(
            "/api/sync/logs",
            json={
                "sync_id": sync_id,
                "sync_target": "apple_calendar",
                "sync_result": "failed",
                "error_message": "User typed private meeting title",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["error_message"] == "REDACTED"
        assert "private meeting title" not in str(data)


class TestGetSyncLog:
    """GET /api/sync/logs/{log_id}"""

    def test_get_existing(self):
        task_id = _create_task()
        sync_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = sync_resp.json()["sync_id"]
        create_resp = client.post(
            "/api/sync/logs",
            json={"sync_id": sync_id, "sync_target": "apple_calendar", "sync_result": "success"},
            headers=AUTH_HEADER,
        )
        log_id = create_resp.json()["log_id"]
        resp = client.get(f"/api/sync/logs/{log_id}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["log_id"] == log_id

    def test_get_nonexistent(self):
        resp = client.get("/api/sync/logs/log_nonexistent_999", headers=AUTH_HEADER)
        assert resp.status_code == 404


class TestListSyncLogs:
    """GET /api/sync/logs"""

    def test_list_empty(self):
        resp = client.get("/api/sync/logs", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_with_items(self):
        task_id = _create_task()
        sync_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = sync_resp.json()["sync_id"]
        client.post(
            "/api/sync/logs",
            json={"sync_id": sync_id, "sync_target": "apple_calendar", "sync_result": "success"},
            headers=AUTH_HEADER,
        )
        client.post(
            "/api/sync/logs",
            json={"sync_id": sync_id, "sync_target": "apple_calendar", "sync_result": "failed"},
            headers=AUTH_HEADER,
        )
        resp = client.get("/api/sync/logs", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_filter_by_result(self):
        task_id = _create_task()
        sync_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = sync_resp.json()["sync_id"]
        client.post(
            "/api/sync/logs",
            json={"sync_id": sync_id, "sync_target": "apple_calendar", "sync_result": "success"},
            headers=AUTH_HEADER,
        )
        resp = client.get(
            "/api/sync/logs?sync_result=success",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


class TestDeleteLogsBySyncId:
    """DELETE /api/sync/logs/by-sync/{sync_id}"""

    def test_delete_by_sync_id(self):
        task_id = _create_task()
        sync_resp = client.post(
            "/api/sync/state",
            json={"task_id": task_id, "sync_target": "apple_calendar"},
            headers=AUTH_HEADER,
        )
        sync_id = sync_resp.json()["sync_id"]
        client.post(
            "/api/sync/logs",
            json={"sync_id": sync_id, "sync_target": "apple_calendar", "sync_result": "success"},
            headers=AUTH_HEADER,
        )
        resp = client.delete(
            f"/api/sync/logs/by-sync/{sync_id}", headers=AUTH_HEADER
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] >= 1


# ── Auth Tests (Sync Endpoints) ─────────────────────────────────────────────


class TestSyncAuth:
    """Ensure sync endpoints require auth."""

    def test_create_sync_state_no_token(self):
        resp = client.post(
            "/api/sync/state",
            json={"task_id": "test", "sync_target": "apple_calendar"},
        )
        assert resp.status_code == 401

    def test_create_sync_log_no_token(self):
        resp = client.post(
            "/api/sync/logs",
            json={"sync_id": "test", "sync_target": "apple_calendar", "sync_result": "success"},
        )
        assert resp.status_code == 401
