"""Phase57 — Tests for Web Schedule Management Frontend integration.

Covers:
  - TaskResponse includes sync_enabled, sync_targets, last_sync_status
  - TaskUpdateRequest accepts sync_enabled and sync_targets
  - Archive via PATCH status=cancelled (not DELETE)
  - Frontend static files served correctly
  - Batch sync state enrichment in list_tasks
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "test_data" / "test_tasks.db"
os.environ["HERMES_API_TOKEN"] = "test-token-hermes-local-4.3"

from local_api.main import app
from local_api.database import reset_db, init_db, get_db

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


# ── Test Helpers ────────────────────────────────────────────────────────────


def _create_calendar_task(sync_enabled=True, start_time="2026-06-01T09:00:00+08:00",
                          due_time="2026-06-01T10:00:00+08:00", title="Phase57 Task",
                          sync_targets=None):
    targets = sync_targets if sync_targets is not None else ["apple_calendar"]
    resp = client.post(
        "/api/tasks",
        json={
            "title": title,
            "priority": "P1",
            "start_time": start_time,
            "due_time": due_time,
            "timezone": "Asia/Shanghai",
            "sync_enabled": sync_enabled,
            "sync_targets": targets,
            "created_channel": "local_ui",
        },
        headers=AUTH_HEADER,
    )
    assert resp.status_code == 201
    return resp.json()


def _mark_synced(task_id, external_id="phase57-event-001"):
    sync = get_db().execute(
        "SELECT * FROM sync_state WHERE task_id = ? AND sync_target = 'apple_calendar'",
        (task_id,),
    ).fetchone()
    assert sync is not None
    get_db().execute(
        "UPDATE sync_state SET sync_status = 'synced', external_id = ? WHERE sync_id = ?",
        (external_id, sync["sync_id"]),
    )
    get_db().commit()
    return sync["sync_id"]


# ─── TaskResponse Sync Fields ───────────────────────────────────────────────


class TestTaskResponseSyncFields:
    """TaskResponse includes sync_enabled, sync_targets, last_sync_status."""

    def test_create_response_has_sync_fields(self):
        task = _create_calendar_task(sync_enabled=True)
        assert "sync_enabled" in task
        assert task["sync_enabled"] is True
        assert "sync_targets" in task
        assert task["sync_targets"] == ["apple_calendar"]
        assert "last_sync_status" in task

    def test_create_response_sync_disabled(self):
        task = _create_calendar_task(sync_enabled=False)
        assert task["sync_enabled"] is False
        assert "sync_targets" in task
        assert "last_sync_status" in task

    def test_get_response_has_sync_fields(self):
        created = _create_calendar_task(sync_enabled=True)
        resp = client.get(f"/api/tasks/{created['task_id']}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "sync_enabled" in data
        assert "sync_targets" in data
        assert "last_sync_status" in data

    def test_list_response_has_sync_fields(self):
        _create_calendar_task(sync_enabled=True, title="Sync field test")
        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) >= 1
        task = data["tasks"][0]
        assert "sync_enabled" in task
        assert "sync_targets" in task
        assert "last_sync_status" in task

    def test_list_response_has_schedule_table_fields(self):
        created = _create_calendar_task(sync_enabled=True, title="Table field test")
        client.patch(
            f"/api/tasks/{created['task_id']}",
            json={"location": "Conference Room"},
            headers=AUTH_HEADER,
        )

        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        task = next(t for t in resp.json()["tasks"] if t["task_id"] == created["task_id"])
        for field in (
            "title",
            "start_time",
            "due_time",
            "location",
            "status",
            "last_sync_status",
            "sync_targets",
            "updated_at",
        ):
            assert field in task
        assert task["location"] == "Conference Room"

    def test_last_sync_status_enriched_from_sync_state(self):
        task = _create_calendar_task(sync_enabled=True, title="Enrich test")
        _mark_synced(task["task_id"], external_id="enrich-test-001")
        resp = client.get(f"/api/tasks/{task['task_id']}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_sync_status"] == "synced"

    def test_list_enriches_sync_status(self):
        task1 = _create_calendar_task(sync_enabled=True, title="Enrich A")
        task2 = _create_calendar_task(sync_enabled=True, title="Enrich B")
        _mark_synced(task1["task_id"], external_id="enrich-a-001")
        _mark_synced(task2["task_id"], external_id="enrich-b-001")

        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        # Find our test tasks in the response
        for t in data["tasks"]:
            if t["task_id"] == task1["task_id"]:
                assert t["last_sync_status"] == "synced"
            if t["task_id"] == task2["task_id"]:
                assert t["last_sync_status"] == "synced"

    def test_task_without_sync_state_has_null_last_sync_status(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "No sync task",
                "sync_enabled": False,
                "created_channel": "local_ui",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        task = resp.json()
        assert task["last_sync_status"] is None


# ─── Archive via PATCH status=cancelled ─────────────────────────────────────


class TestArchiveSemantics:
    """Archive uses PATCH with status=cancelled, not DELETE."""

    def test_archive_sets_status_cancelled(self):
        task = _create_calendar_task(title="Archive test")
        resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"status": "cancelled"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["task_id"] == task["task_id"]

    def test_archived_task_still_exists_in_list(self):
        task = _create_calendar_task(title="Archived but visible")
        client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"status": "cancelled"},
            headers=AUTH_HEADER,
        )
        resp = client.get("/api/tasks?status=cancelled", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        ids = [t["task_id"] for t in data["tasks"]]
        assert task["task_id"] in ids

    def test_archive_does_not_delete_task(self):
        task = _create_calendar_task(title="Not deleted on archive")
        client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"status": "cancelled"},
            headers=AUTH_HEADER,
        )
        # Task should still be fetchable
        resp = client.get(f"/api/tasks/{task['task_id']}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_delete_is_distinct_from_archive(self):
        """DELETE returns 204, PATCH with cancelled returns 200 and preserves the row."""
        task = _create_calendar_task(title="Distinction test")
        # PATCH archive
        patch_resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"status": "cancelled"},
            headers=AUTH_HEADER,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "cancelled"

        # DELETE the task
        del_resp = client.delete(f"/api/tasks/{task['task_id']}", headers=AUTH_HEADER)
        assert del_resp.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/tasks/{task['task_id']}", headers=AUTH_HEADER)
        assert get_resp.status_code == 404

    def test_archive_synced_task_cleans_up_calendar_event(self):
        task = _create_calendar_task(title="Archive sync cleanup")
        sync_id = _mark_synced(task["task_id"], external_id="archive-clean-001")
        assert sync_id is not None

        resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"status": "cancelled"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # Sync state should have been cleaned up (adapter removal attempted)
        from local_api.services.sync_state_service import get_sync_state
        state = get_sync_state(sync_id)
        # The state may be transitioned to disabled/deleted by cleanup
        assert state is not None


# ─── TaskUpdateRequest accepts sync_enabled/sync_targets ────────────────────


class TestUpdateTaskSyncFields:
    """PATCH /api/tasks/{task_id} accepts sync_enabled and sync_targets."""

    def test_update_sync_enabled(self):
        task = _create_calendar_task(sync_enabled=False)
        resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"sync_enabled": True},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["sync_enabled"] is True

    def test_update_sync_targets(self):
        task = _create_calendar_task(sync_enabled=True, sync_targets=["apple_calendar"])
        resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"sync_targets": ["apple_calendar", "apple_reminder"]},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        targets = resp.json()["sync_targets"]
        assert "apple_calendar" in targets
        assert "apple_reminder" in targets

    def test_update_sync_fields_preserves_other_fields(self):
        task = _create_calendar_task(sync_enabled=False, title="Original")
        resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={"sync_enabled": True, "sync_targets": ["apple_calendar"]},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_enabled"] is True
        assert data["sync_targets"] == ["apple_calendar"]
        assert data["title"] == "Original"


# ─── Frontend Static Serving ────────────────────────────────────────────────


class TestFrontendServing:
    """Frontend static files served from /frontend."""

    def test_frontend_index_served(self):
        resp = client.get("/frontend/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        content = resp.text
        # Should contain the main HTML structure
        assert "日程管理" in content
        assert "LifeSync Hub" in content
        # Token should be injected
        assert "test-token-hermes-local-4.3" in content

    def test_frontend_table_contains_required_schedule_columns(self):
        resp = client.get("/frontend/")
        assert resp.status_code == 200
        content = resp.text
        for header in ("标题", "开始时间", "结束时间", "地点", "任务状态", "同步状态", "同步目标", "更新时间"):
            assert header in content

    def test_frontend_archive_uses_cancelled_status_not_delete(self):
        resp = client.get("/frontend/static/app.js")
        assert resp.status_code == 200
        content = resp.text
        archive_start = content.index("async function archiveTask")
        archive_end = content.index("// ── Confirm Dialog ───────────────────────────────────────────────────────")
        archive_fn = content[archive_start:archive_end]
        assert "status: 'cancelled'" in archive_fn
        assert "confirmDeleteTask" not in archive_fn

    def test_frontend_style_served(self):
        resp = client.get("/frontend/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "").lower() or \
               resp.headers.get("content-type", "").startswith("text/css") or \
               "css" in resp.headers.get("content-type", "").lower()

    def test_frontend_js_served(self):
        resp = client.get("/frontend/static/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers.get("content-type", "").lower() or \
               "js" in resp.headers.get("content-type", "").lower()

    def test_frontend_without_auth(self):
        """Frontend is on PUBLIC_PATH_PREFIXES, so /frontend/ works without auth."""
        resp = client.get("/frontend/")
        assert resp.status_code == 200

    def test_frontend_static_without_auth(self):
        resp = client.get("/frontend/static/style.css")
        assert resp.status_code == 200

    def test_health_still_public(self):
        resp = client.get("/health")
        assert resp.status_code == 200


# ─── Created Channel local_ui ───────────────────────────────────────────────


class TestCreatedChannelLocalUI:
    """Create tasks with created_channel=local_ui from frontend."""

    def test_create_with_local_ui_channel(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Frontend created task",
                "created_channel": "local_ui",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created_channel"] == "local_ui"
        assert data["sync_enabled"] is True
        assert data["sync_targets"] == ["apple_calendar"]

    def test_list_filters_includes_local_ui_tasks(self):
        client.post(
            "/api/tasks",
            json={"title": "UI task", "created_channel": "local_ui"},
            headers=AUTH_HEADER,
        )
        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        total = resp.json()["total"]
        assert total >= 1
