"""Phase 4.3 — Unit tests for the local API.

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_api.py -v

Uses FastAPI TestClient so no server needs to be running.
"""

import json
import pytest
from fastapi.testclient import TestClient

# Force test database path before any import
import os
from pathlib import Path

TEST_DB_PATH = Path(__file__).resolve().parent.parent / "test_data" / "test_tasks.db"
os.environ["HERMES_API_TOKEN"] = "test-token-hermes-local-4.3"

# Now import app — it will pick up the test DB
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


# ── System Status Tests ─────────────────────────────────────────────────────


class TestSystemStatus:
    """GET /api/system/status"""

    def test_status_with_token(self):
        resp = client.get("/api/system/status", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["api_version"] == "4.3.0"
        assert data["db_connected"] is True
        assert data["task_count"] == 0

    def test_status_without_token(self):
        resp = client.get("/api/system/status")
        assert resp.status_code == 401

    def test_status_with_bad_token(self):
        resp = client.get(
            "/api/system/status",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401


# ── Task CRUD Tests ────────────────────────────────────────────────────────


class TestCreateTask:
    """POST /api/tasks"""

    def test_create_minimal(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "Minimal task"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Minimal task"
        assert data["task_id"].startswith("task_")
        assert data["priority"] == "P2"  # default
        assert data["status"] == "pending"
        assert data["created_channel"] == "api_test"

    def test_create_full(self):
        payload = {
            "title": "Full task",
            "description": "A detailed description",
            "priority": "P0",
            "start_time": "2026-06-01T09:00:00+08:00",
            "due_time": "2026-06-01T10:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "location": "Office",
            "need_weather_check": True,
            "reminder_channels": ["local_ui", "wechat"],
            "created_channel": "api_test",
        }
        resp = client.post("/api/tasks", json=payload, headers=AUTH_HEADER)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Full task"
        assert data["priority"] == "P0"
        assert data["need_weather_check"] is True
        assert data["reminder_channels"] == ["local_ui", "wechat"]
        assert data["location"] == "Office"

    def test_create_duplicate_task_ids(self):
        """Two tasks created in sequence should have different IDs."""
        resp1 = client.post(
            "/api/tasks", json={"title": "Task A"}, headers=AUTH_HEADER
        )
        resp2 = client.post(
            "/api/tasks", json={"title": "Task B"}, headers=AUTH_HEADER
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["task_id"] != resp2.json()["task_id"]

    def test_create_invalid_priority(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "Bad priority", "priority": "P5"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_invalid_channel(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "Bad channel", "created_channel": "unknown_channel"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_empty_title(self):
        resp = client.post(
            "/api/tasks",
            json={"title": ""},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_create_eligible_task_enqueues_pending_calendar_sync_state(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Calendar task",
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        row = get_db().execute(
            "SELECT * FROM sync_state WHERE task_id = ? AND sync_target = 'apple_calendar'",
            (task_id,),
        ).fetchone()
        assert row is not None
        assert row["sync_status"] == "pending"
        assert row["sync_key"] == f"{task_id}:apple_calendar"
        assert row["external_id"] is None

    def test_create_task_without_time_does_not_enqueue_sync_state(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "No time",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        count = get_db().execute(
            "SELECT COUNT(*) AS cnt FROM sync_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()["cnt"]
        assert count == 0

    def test_create_task_with_sync_enabled_false_does_not_enqueue_sync_state(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Disabled sync",
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "sync_enabled": False,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        count = get_db().execute(
            "SELECT COUNT(*) AS cnt FROM sync_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()["cnt"]
        assert count == 0

    def test_create_task_with_non_calendar_target_does_not_enqueue_sync_state(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Reminder target",
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "sync_enabled": True,
                "sync_targets": ["apple_reminder"],
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        count = get_db().execute(
            "SELECT COUNT(*) AS cnt FROM sync_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()["cnt"]
        assert count == 0

    def test_create_task_with_due_time_not_after_start_time_does_not_enqueue_sync_state(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Invalid time window",
                "start_time": "2026-06-01T10:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        count = get_db().execute(
            "SELECT COUNT(*) AS cnt FROM sync_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()["cnt"]
        assert count == 0

    def test_create_task_does_not_write_calendar_or_run_sync(self):
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Do not sync immediately",
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        sync_row = get_db().execute(
            "SELECT * FROM sync_state WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        assert sync_row is not None
        assert sync_row["sync_status"] == "pending"
        assert sync_row["external_id"] is None
        log_count = get_db().execute(
            "SELECT COUNT(*) AS cnt FROM sync_logs WHERE local_task_id = ?",
            (task_id,),
        ).fetchone()["cnt"]
        assert log_count == 0


class TestListTasks:
    """GET /api/tasks"""

    def test_list_empty(self):
        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["total"] == 0

    def test_list_with_tasks(self):
        # Create a few tasks first
        for i in range(3):
            client.post(
                "/api/tasks",
                json={"title": f"Task {i}", "priority": "P1"},
                headers=AUTH_HEADER,
            )
        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 3
        assert data["total"] == 3

    def test_filter_by_status(self):
        client.post("/api/tasks", json={"title": "Pending"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?status=pending", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_filter_by_priority(self):
        client.post(
            "/api/tasks",
            json={"title": "P0 task", "priority": "P0"},
            headers=AUTH_HEADER,
        )
        client.post(
            "/api/tasks",
            json={"title": "P1 task", "priority": "P1"},
            headers=AUTH_HEADER,
        )
        resp = client.get("/api/tasks?priority=P0", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["priority"] == "P0"

    def test_pagination(self):
        for i in range(5):
            client.post(
                "/api/tasks", json={"title": f"T{i}"}, headers=AUTH_HEADER
            )
        resp = client.get("/api/tasks?limit=2&offset=1", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 1

    def test_invalid_status_filter(self):
        resp = client.get("/api/tasks?status=invalid_status", headers=AUTH_HEADER)
        assert resp.status_code == 422

    def test_invalid_priority_filter(self):
        resp = client.get("/api/tasks?priority=P5", headers=AUTH_HEADER)
        assert resp.status_code == 422


class TestSearchTasks:
    """GET /api/tasks?search=..."""

    def test_search_by_title(self):
        client.post("/api/tasks", json={"title": "Find me please", "description": "secret"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "Other task"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?search=Find", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert all("Find" in t["title"] for t in data["tasks"])

    def test_search_by_description(self):
        client.post("/api/tasks", json={"title": "Desc search", "description": "unique-description-text"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "Something else"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?search=unique-description", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_search_by_location(self):
        client.post("/api/tasks", json={"title": "Loc task", "location": "Tokyo Tower"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "Other"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?search=Tokyo", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_search_no_match(self):
        resp = client.get("/api/tasks?search=zzzthisdoesnotexistzzz", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_search_empty_query_returns_all(self):
        client.post("/api/tasks", json={"title": "Task A"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "Task B"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?search=", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2

    def test_search_composes_with_status_filter(self):
        client.post("/api/tasks", json={"title": "Meeting prep", "status": "pending"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "Meeting done", "status": "completed"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?search=Meeting&status=pending", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for t in data["tasks"]:
            assert t["status"] == "pending"


class TestDateFilterTasks:
    """GET /api/tasks?start_date=...&end_date=..."""

    def test_filter_by_start_date(self):
        client.post("/api/tasks", json={
            "title": "June task", "start_time": "2026-06-15T09:00:00+08:00", "due_time": "2026-06-15T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        client.post("/api/tasks", json={
            "title": "July task", "start_time": "2026-07-01T09:00:00+08:00", "due_time": "2026-07-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?start_date=2026-07-01", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for t in data["tasks"]:
            assert t["start_time"] is not None and t["start_time"] >= "2026-07-01"

    def test_filter_by_end_date(self):
        client.post("/api/tasks", json={
            "title": "Early", "start_time": "2026-06-01T09:00:00+08:00", "due_time": "2026-06-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        client.post("/api/tasks", json={
            "title": "Late", "start_time": "2026-07-10T09:00:00+08:00", "due_time": "2026-07-10T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?end_date=2026-06-30", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for t in data["tasks"]:
            assert t["due_time"] is not None and t["due_time"] <= "2026-06-30"

    def test_filter_by_date_range(self):
        client.post("/api/tasks", json={
            "title": "Mid", "start_time": "2026-06-15T09:00:00+08:00", "due_time": "2026-06-15T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?start_date=2026-06-01&end_date=2026-07-31", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1


class TestSortTasks:
    """GET /api/tasks?sort_by=...&sort_order=..."""

    def test_sort_by_start_time_asc(self):
        client.post("/api/tasks", json={
            "title": "Later", "start_time": "2026-07-01T09:00:00+08:00", "due_time": "2026-07-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        client.post("/api/tasks", json={
            "title": "Earlier", "start_time": "2026-06-01T09:00:00+08:00", "due_time": "2026-06-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?sort_by=start_time&sort_order=asc", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if len(data["tasks"]) >= 2:
            assert data["tasks"][0]["start_time"] <= data["tasks"][-1]["start_time"]

    def test_sort_by_due_time_desc(self):
        client.post("/api/tasks", json={
            "title": "Old", "start_time": "2026-06-01T09:00:00+08:00", "due_time": "2026-06-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        client.post("/api/tasks", json={
            "title": "New", "start_time": "2026-07-01T09:00:00+08:00", "due_time": "2026-07-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?sort_by=due_time&sort_order=desc", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if len(data["tasks"]) >= 2:
            assert data["tasks"][0]["due_time"] >= data["tasks"][-1]["due_time"]

    def test_sort_by_title(self):
        client.post("/api/tasks", json={"title": "B task"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "A task"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?sort_by=title&sort_order=asc", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if len(data["tasks"]) >= 2:
            assert data["tasks"][0]["title"] <= data["tasks"][1]["title"]

    def test_invalid_sort_column_falls_back_to_created_at(self):
        resp = client.get("/api/tasks?sort_by=nonexistent_column&sort_order=asc", headers=AUTH_HEADER)
        assert resp.status_code == 200  # Should not error, falls back to default

    def test_default_sort_is_created_at_desc(self):
        client.post("/api/tasks", json={"title": "First"}, headers=AUTH_HEADER)
        client.post("/api/tasks", json={"title": "Second"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if len(data["tasks"]) >= 2:
            assert data["tasks"][0]["created_at"] >= data["tasks"][-1]["created_at"]


class TestArchivedExclusion:
    """Archived/cancelled tasks should not pollute active lists."""

    def test_cancelled_excluded_from_list(self):
        task = client.post("/api/tasks", json={"title": "Will cancel"}, headers=AUTH_HEADER).json()
        client.patch(f"/api/tasks/{task['task_id']}", json={"status": "cancelled"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?status=pending", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] != "cancelled" for t in data["tasks"])

    def test_archived_filter_returns_cancelled(self):
        task = client.post("/api/tasks", json={"title": "Archived"}, headers=AUTH_HEADER).json()
        client.patch(f"/api/tasks/{task['task_id']}", json={"status": "cancelled"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?status=cancelled", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert all(t["status"] == "cancelled" for t in data["tasks"])


class TestSyncStatusFilterAPI:
    """GET /api/tasks?sync_status=..."""

    def _create_task_with_sync_state(self, title, sync_status_val):
        resp = client.post("/api/tasks", json={
            "title": title,
            "start_time": "2026-06-01T09:00:00+08:00",
            "due_time": "2026-06-01T10:00:00+08:00",
            "sync_enabled": True,
            "sync_targets": ["apple_calendar"],
        }, headers=AUTH_HEADER)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        # Update the sync_state directly
        conn = get_db()
        sync = conn.execute(
            "SELECT * FROM sync_state WHERE task_id = ? AND sync_target = 'apple_calendar'",
            (task_id,),
        ).fetchone()
        if sync:
            conn.execute(
                "UPDATE sync_state SET sync_status = ? WHERE sync_id = ?",
                (sync_status_val, sync["sync_id"]),
            )
            conn.commit()
        return task_id

    def test_filter_by_sync_status_synced(self):
        self._create_task_with_sync_state("Synced task", "synced")
        resp = client.get("/api/tasks?sync_status=synced", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if data["total"] > 0:
            for t in data["tasks"]:
                assert t["last_sync_status"] == "synced"

    def test_filter_by_sync_status_failed(self):
        self._create_task_with_sync_state("Failed task", "failed")
        resp = client.get("/api/tasks?sync_status=failed", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if data["total"] > 0:
            for t in data["tasks"]:
                assert t["last_sync_status"] == "failed"

    def test_filter_by_sync_status_pending(self):
        self._create_task_with_sync_state("Pending task", "pending")
        resp = client.get("/api/tasks?sync_status=pending", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if data["total"] > 0:
            for t in data["tasks"]:
                assert t["last_sync_status"] == "pending"

    def test_filter_by_sync_status_stale(self):
        self._create_task_with_sync_state("Stale task", "stale")
        resp = client.get("/api/tasks?sync_status=stale", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if data["total"] > 0:
            for t in data["tasks"]:
                assert t["last_sync_status"] == "stale"

    def test_invalid_sync_status_filter(self):
        resp = client.get("/api/tasks?sync_status=invalid_sync", headers=AUTH_HEADER)
        assert resp.status_code == 422

    def test_sync_status_not_synced(self):
        # Tasks without sync_state should be "not_synced"
        client.post("/api/tasks", json={"title": "No sync"}, headers=AUTH_HEADER)
        resp = client.get("/api/tasks?sync_status=not_synced", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        if data["total"] > 0:
            for t in data["tasks"]:
                assert t["last_sync_status"] is None or t["last_sync_status"] == "not_synced"


class TestAPIResponseStructure:
    """Verify API response model includes all expected fields."""

    def test_task_response_has_sync_fields(self):
        resp = client.post("/api/tasks", json={
            "title": "Response check",
            "start_time": "2026-06-01T09:00:00+08:00",
            "due_time": "2026-06-01T10:00:00+08:00",
        }, headers=AUTH_HEADER)
        assert resp.status_code == 201
        data = resp.json()
        expected = {
            "task_id", "title", "description", "priority", "status",
            "start_time", "due_time", "timezone", "location",
            "need_weather_check", "schedule_type", "notify_policy",
            "weather_sensitive", "reminder_profile", "reminder_channels",
            "created_channel", "sync_enabled", "sync_targets",
            "last_sync_status", "last_synced_at", "created_at", "updated_at",
        }
        assert expected.issubset(data.keys())

    def test_list_response_has_pagination(self):
        resp = client.get("/api/tasks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    def test_list_response_works_with_all_new_params(self):
        """All new query params together should not error."""
        resp = client.get(
            "/api/tasks?search=test&start_date=2026-01-01&end_date=2026-12-31&sort_by=start_time&sort_order=asc",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert "tasks" in resp.json()

    def test_list_response_supports_date_and_sort_aliases(self):
        client.post(
            "/api/tasks",
            json={
                "title": "Alias date task",
                "description": "alias search marker",
                "start_time": "2026-07-03T09:00:00+08:00",
                "due_time": "2026-07-03T10:00:00+08:00",
            },
            headers=AUTH_HEADER,
        )
        resp = client.get(
            "/api/tasks?search=alias&date=2026-07-03&sort=start_time&sort_order=asc",
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert all((task["start_time"] or task["due_time"] or "").startswith("2026-07-03") for task in data["tasks"])
        assert any(task["title"] == "Alias date task" for task in data["tasks"])

    def test_invalid_date_filter_returns_422(self):
        resp = client.get("/api/tasks?date=not-a-date", headers=AUTH_HEADER)
        assert resp.status_code == 422


class TestFrontendCalendarMarkers:
    """Verify frontend app.js contains calendar date interaction markers."""

    FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend" / "app.js"

    def test_select_calendar_date_function_exists(self):
        source = self.FRONTEND_PATH.read_text(encoding="utf-8")
        assert "function selectCalendarDate" in source

    def test_calendar_mini_renders_date_dots(self):
        source = self.FRONTEND_PATH.read_text(encoding="utf-8")
        assert "dateSet" in source or "hasTask" in source

    def test_selected_date_state_variable_exists(self):
        source = self.FRONTEND_PATH.read_text(encoding="utf-8")
        assert "selectedDate" in source

    def test_calendar_click_handler_in_table_cell(self):
        source = self.FRONTEND_PATH.read_text(encoding="utf-8")
        assert "selectCalendarDate" in source

    def test_empty_state_hint_for_selected_date(self):
        source = self.FRONTEND_PATH.read_text(encoding="utf-8")
        assert "暂无日程" in source

    def test_prd_ui_contract_markers_exist(self):
        app_source = self.FRONTEND_PATH.read_text(encoding="utf-8")
        html_source = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")
        assert "class=\"task-select\"" in app_source
        assert "id=\"select-all-tasks\"" in html_source
        assert "#edit-sync-enabled').checked = true" in app_source
        assert "calendar-mini-month" in html_source
        assert "最后同步时间" in app_source
        assert "info-status-line" in app_source


class TestGetTask:
    """GET /api/tasks/{task_id}"""

    def test_get_existing(self):
        create = client.post(
            "/api/tasks", json={"title": "Get me"}, headers=AUTH_HEADER
        )
        task_id = create.json()["task_id"]
        resp = client.get(f"/api/tasks/{task_id}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id

    def test_get_nonexistent(self):
        resp = client.get("/api/tasks/task_nonexistent_12345", headers=AUTH_HEADER)
        assert resp.status_code == 404


class TestUpdateTask:
    """PATCH /api/tasks/{task_id}"""

    def _create_calendar_task(self, title="Calendar task"):
        resp = client.post(
            "/api/tasks",
            json={
                "title": title,
                "description": "Original description",
                "priority": "P1",
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "timezone": "Asia/Shanghai",
                "location": "Office",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        return resp.json()["task_id"]

    def _mark_calendar_synced(self, task_id, external_id="calendar-event-001"):
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

    def _calendar_sync_rows(self, task_id):
        return get_db().execute(
            "SELECT * FROM sync_state WHERE task_id = ? AND sync_target = 'apple_calendar' ORDER BY sync_id",
            (task_id,),
        ).fetchall()

    def test_update_title(self):
        create = client.post(
            "/api/tasks", json={"title": "Old title"}, headers=AUTH_HEADER
        )
        task_id = create.json()["task_id"]
        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "New title"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"
        assert resp.json()["priority"] == "P2"  # unchanged

    def test_update_priority(self):
        create = client.post(
            "/api/tasks", json={"title": "X"}, headers=AUTH_HEADER
        )
        task_id = create.json()["task_id"]
        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"priority": "P0"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["priority"] == "P0"

    def test_update_nonexistent(self):
        resp = client.patch(
            "/api/tasks/task_nonexistent_999",
            json={"title": "X"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 404

    def test_update_invalid_status(self):
        create = client.post(
            "/api/tasks", json={"title": "X"}, headers=AUTH_HEADER
        )
        task_id = create.json()["task_id"]
        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"status": "invalid"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_synced_calendar_task_title_change_marks_sync_state_stale(self):
        task_id = self._create_calendar_task()
        sync_id = self._mark_calendar_synced(task_id, external_id="calendar-event-title")

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "Updated calendar title"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_id"] == sync_id
        assert rows[0]["sync_status"] == "stale"
        assert rows[0]["external_id"] == "calendar-event-title"

    def test_synced_calendar_task_time_change_marks_sync_state_stale(self):
        task_id = self._create_calendar_task()
        self._mark_calendar_synced(task_id, external_id="calendar-event-time")

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"due_time": "2026-06-01T11:00:00+08:00"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "stale"
        assert rows[0]["external_id"] == "calendar-event-time"

    def test_synced_calendar_task_non_sync_field_change_does_not_mark_stale(self):
        task_id = self._create_calendar_task()
        self._mark_calendar_synced(task_id, external_id="calendar-event-weather")

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"need_weather_check": True},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "synced"
        assert rows[0]["external_id"] == "calendar-event-weather"

    def test_update_synced_calendar_task_does_not_create_second_sync_state(self):
        task_id = self._create_calendar_task()
        self._mark_calendar_synced(task_id)

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"location": "Updated office"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        assert len(self._calendar_sync_rows(task_id)) == 1

    def test_update_calendar_task_does_not_write_calendar_or_run_sync(self):
        task_id = self._create_calendar_task()
        self._mark_calendar_synced(task_id, external_id="calendar-event-no-write")

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "No direct Calendar write"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        row = self._calendar_sync_rows(task_id)[0]
        assert row["sync_status"] == "stale"
        assert row["external_id"] == "calendar-event-no-write"
        # Phase 23: legacy no-hash drift writes a drift_detected log.
        # No Calendar write or sync run occurs — only a trace log.
        log_count = get_db().execute(
            "SELECT COUNT(*) AS cnt FROM sync_logs WHERE local_task_id = ?",
            (task_id,),
        ).fetchone()["cnt"]
        assert log_count == 1
        # Verify it's a drift_detected log, not a success/failed sync log
        from local_api.services.sync_log_service import list_sync_logs
        logs, _ = list_sync_logs(sync_id=row["sync_id"])
        assert len(logs) == 1
        assert logs[0]["sync_result"] == "drift_detected"

    def test_update_unsynced_eligible_task_keeps_pending_sync_state(self):
        task_id = self._create_calendar_task()

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "Still pending"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "pending"
        assert rows[0]["external_id"] is None

    def test_update_unsynced_eligible_task_without_sync_state_creates_pending(self):
        task_id = client.post(
            "/api/tasks",
            json={"title": "Initially no time", "sync_enabled": True, "sync_targets": ["apple_calendar"]},
            headers=AUTH_HEADER,
        ).json()["task_id"]

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
            },
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "pending"

    def test_update_ineligible_task_is_blocked_from_sync_state(self):
        task_id = client.post(
            "/api/tasks",
            json={
                "title": "Calendar task that becomes invalid",
                "sync_enabled": True,
                "sync_targets": ["apple_calendar"],
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
            },
            headers=AUTH_HEADER,
        ).json()["task_id"]

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"due_time": "2026-06-01T09:00:00+08:00"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "pending"

    # ── Phase 21 — Payload hash / drift wiring ──────────────────────────

    def test_synced_task_patch_sync_field_detects_drift(self):
        """Patch a sync-relevant field on a synced Calendar task → stale + drift log."""
        task_id = self._create_calendar_task(title="Drift test")
        sync_id = self._mark_calendar_synced(task_id, external_id="calendar-drift-001")

        # Seed a stored payload_hash to simulate a past successful sync
        from local_api.sync_client.payload import compute_task_payload_hash
        conn = get_db()
        task_row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        stored_hash = compute_task_payload_hash(dict(task_row))
        conn.execute(
            "UPDATE sync_state SET payload_hash = ? WHERE sync_id = ?",
            (stored_hash, sync_id),
        )
        conn.commit()

        # Now patch a sync-relevant field
        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "Drifted title"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "stale"
        # payload_hash must NOT be updated — keep last synced hash
        assert rows[0]["payload_hash"] == stored_hash

        # Drift log must exist
        from local_api.services.sync_log_service import list_sync_logs
        logs, _ = list_sync_logs(sync_id=sync_id)
        drift_logs = [l for l in logs if l["sync_result"] == "drift_detected"]
        assert len(drift_logs) == 1
        drift = drift_logs[0]
        assert drift["drift_detected"] == 1
        assert drift["payload_hash_before"] == stored_hash
        assert drift["payload_hash_after"] is not None
        assert drift["payload_hash_after"] != stored_hash
        # drift_fields should include the changed field(s)
        assert "title" in (drift["drift_fields"] or "")
        # external_id unchanged across drift
        assert drift["external_id_before"] == "calendar-drift-001"
        assert drift["external_id_after"] == "calendar-drift-001"

    def test_synced_task_patch_status_detects_drift(self):
        """Patch only status on a synced Calendar task → stale + drift log with status in drift_fields."""
        task_id = self._create_calendar_task(title="Status drift test")
        sync_id = self._mark_calendar_synced(task_id, external_id="calendar-drift-status")

        from local_api.sync_client.payload import compute_task_payload_hash
        conn = get_db()
        task_row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        stored_hash = compute_task_payload_hash(dict(task_row))
        conn.execute(
            "UPDATE sync_state SET payload_hash = ? WHERE sync_id = ?",
            (stored_hash, sync_id),
        )
        conn.commit()

        # Patch only status — must be detected as drift with drift_fields including "status"
        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"status": "completed"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "stale"
        assert rows[0]["payload_hash"] == stored_hash

        from local_api.services.sync_log_service import list_sync_logs
        logs, _ = list_sync_logs(sync_id=sync_id)
        drift_logs = [l for l in logs if l["sync_result"] == "drift_detected"]
        assert len(drift_logs) == 1
        drift = drift_logs[0]
        assert drift["drift_detected"] == 1
        assert drift["payload_hash_before"] == stored_hash
        assert drift["payload_hash_after"] is not None
        assert drift["payload_hash_after"] != stored_hash
        # drift_fields must include "status"
        assert "status" in (drift["drift_fields"] or "")

    def test_synced_task_patch_non_sync_field_no_drift(self):
        """Patch need_weather_check on a synced task → stays synced, no drift log."""
        task_id = self._create_calendar_task(title="No drift test")
        sync_id = self._mark_calendar_synced(task_id, external_id="calendar-nodrift-001")

        from local_api.sync_client.payload import compute_task_payload_hash
        conn = get_db()
        task_row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        stored_hash = compute_task_payload_hash(dict(task_row))
        conn.execute(
            "UPDATE sync_state SET payload_hash = ? WHERE sync_id = ?",
            (stored_hash, sync_id),
        )
        conn.commit()

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"need_weather_check": True},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "synced"

        from local_api.services.sync_log_service import list_sync_logs
        logs, _ = list_sync_logs(sync_id=sync_id)
        drift_logs = [l for l in logs if l["sync_result"] == "drift_detected"]
        assert len(drift_logs) == 0

    def test_apple_reminder_task_patch_does_not_create_calendar_state_or_log(self):
        """An apple_reminder-only task update does not create Calendar sync_state or drift log."""
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Reminder only",
                "start_time": "2026-06-01T09:00:00+08:00",
                "due_time": "2026-06-01T10:00:00+08:00",
                "sync_enabled": True,
                "sync_targets": ["apple_reminder"],
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "Updated reminder"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200

        # No Calendar sync_state
        from local_api.services.sync_state_service import get_sync_state_by_key
        cal_state = get_sync_state_by_key(task_id, "apple_calendar")
        assert cal_state is None

    def test_synced_task_legacy_no_stored_hash_still_becomes_stale(self):
        """When stored payload_hash is missing (legacy), sync-relevant change still → stale
        AND Phase 23 writes a traceable drift log."""
        task_id = self._create_calendar_task(title="Legacy no hash")
        sync_id = self._mark_calendar_synced(task_id, external_id="calendar-legacy-001")
        # Deliberately leave payload_hash as NULL (legacy row)

        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "Legacy title changed"},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "stale"

        # Phase 23: legacy no-hash drift now writes a traceable drift_detected log
        from local_api.services.sync_log_service import list_sync_logs
        logs, _ = list_sync_logs(sync_id=sync_id)
        drift_logs = [l for l in logs if l["sync_result"] == "drift_detected"]
        assert len(drift_logs) == 1, (
            "Phase 23: legacy no-hash drift must write a traceable drift_detected log"
        )
        drift = drift_logs[0]
        assert drift["drift_detected"] == 1
        assert drift["payload_hash_before"] is None  # legacy: no stored hash
        assert drift["payload_hash_after"] is not None
        assert "title" in (drift["drift_fields"] or "")
        assert drift["external_id_before"] == "calendar-legacy-001"
        assert drift["external_id_after"] == "calendar-legacy-001"

    def test_synced_task_legacy_no_hash_no_sync_field_change_stays_synced(self):
        """Legacy row with no stored hash: non-sync field change → stays synced, no drift log."""
        task_id = self._create_calendar_task(title="Legacy no hash unchanged")
        sync_id = self._mark_calendar_synced(task_id, external_id="calendar-legacy-nodrift")
        # Deliberately leave payload_hash as NULL (legacy row)

        # Patch a non-sync-relevant field (need_weather_check is excluded from payload)
        resp = client.patch(
            f"/api/tasks/{task_id}",
            json={"need_weather_check": True},
            headers=AUTH_HEADER,
        )

        assert resp.status_code == 200
        rows = self._calendar_sync_rows(task_id)
        assert len(rows) == 1
        assert rows[0]["sync_status"] == "synced"

        # No drift log
        from local_api.services.sync_log_service import list_sync_logs
        logs, _ = list_sync_logs(sync_id=sync_id)
        drift_logs = [l for l in logs if l["sync_result"] == "drift_detected"]
        assert len(drift_logs) == 0


class TestCompleteTask:
    """POST /api/tasks/{task_id}/complete"""

    def test_complete_task(self):
        create = client.post(
            "/api/tasks", json={"title": "Complete me"}, headers=AUTH_HEADER
        )
        task_id = create.json()["task_id"]
        resp = client.post(
            f"/api/tasks/{task_id}/complete", headers=AUTH_HEADER
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_complete_nonexistent(self):
        resp = client.post(
            "/api/tasks/task_nonexistent_999/complete", headers=AUTH_HEADER
        )
        assert resp.status_code == 404

    def test_complete_already_completed(self):
        create = client.post(
            "/api/tasks", json={"title": "X"}, headers=AUTH_HEADER
        )
        task_id = create.json()["task_id"]
        client.post(f"/api/tasks/{task_id}/complete", headers=AUTH_HEADER)
        # Completing again should still succeed (idempotent)
        resp = client.post(
            f"/api/tasks/{task_id}/complete", headers=AUTH_HEADER
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"


# ── Validation Tests ───────────────────────────────────────────────────────


class TestForbiddenFields:
    """Middleware rejects forbidden field names."""

    def test_file_path_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "file_path": "/etc/passwd"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422
        assert "Forbidden field" in resp.json()["detail"]

    def test_sql_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "sql": "DROP TABLE"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_command_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "command": "rm -rf /"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_script_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "script": "something"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_shell_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "shell": "/bin/bash"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_exec_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "exec": "calc.exe"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_cmd_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "cmd": "dir"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_path_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "path": "/etc/shadow"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_filename_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "filename": "secret.key"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_directory_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "directory": "/root"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_template_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "template": "{{config}}"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_raw_query_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "raw_query": "SELECT 1"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


class TestSQLInjection:
    """Middleware rejects SQL injection patterns in string values."""

    def test_select_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "SELECT * FROM users"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_drop_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "DROP TABLE tasks"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_insert_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "INSERT INTO tasks VALUES"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_union_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "1 UNION SELECT"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_sql_comment_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "admin'--"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_sql_in_description(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "OK", "description": "SELECT * FROM passwords"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_clean_title_accepted(self):
        """Normal titles without SQL should work fine."""
        resp = client.post(
            "/api/tasks",
            json={"title": "Buy milk and eggs"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 201


class TestShellInjection:
    """Middleware rejects shell injection patterns."""

    def test_semicolon_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "hello; rm -rf /"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_pipe_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "cat /etc/passwd | mail"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422


# ── Sync Engine Management Tests ───────────────────────────────────────────


class TestSyncEngineStart:
    """POST /api/system/sync-engine/start"""

    def test_start_engine(self):
        resp = client.post("/api/system/sync-engine/start", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("started", "already_running")

    def test_start_engine_idempotent(self):
        resp1 = client.post("/api/system/sync-engine/start", headers=AUTH_HEADER)
        resp2 = client.post("/api/system/sync-engine/start", headers=AUTH_HEADER)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Second call should be idempotent and not error
        assert resp2.json()["status"] in ("started", "already_running")

    def test_start_without_token(self):
        resp = client.post("/api/system/sync-engine/start")
        assert resp.status_code == 401


class TestSyncEngineStop:
    """POST /api/system/sync-engine/stop"""

    def test_stop_engine(self):
        # Start first
        client.post("/api/system/sync-engine/start", headers=AUTH_HEADER)
        resp = client.post("/api/system/sync-engine/stop", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("stopped", "not_running")

    def test_stop_when_not_running(self):
        resp = client.post("/api/system/sync-engine/stop", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_running"

    def test_stop_idempotent(self):
        resp1 = client.post("/api/system/sync-engine/stop", headers=AUTH_HEADER)
        resp2 = client.post("/api/system/sync-engine/stop", headers=AUTH_HEADER)
        assert resp1.status_code == 200
        assert resp2.status_code == 200


class TestSyncEngineStatus:
    """GET /api/system/sync-engine/status"""

    def test_status_default(self):
        resp = client.get("/api/system/sync-engine/status", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "status" in data
        assert "scan_count" in data
        assert data["scan_count"] >= 0
        assert "last_scan_at" in data
        assert "queued_pending" in data
        assert data["queued_pending"] >= 0

    def test_status_after_start(self):
        client.post("/api/system/sync-engine/start", headers=AUTH_HEADER)
        resp = client.get("/api/system/sync-engine/status", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        # Engine should be running
        assert data["running"] is True
        assert data["status"] == "running"

    def test_status_after_start_stop(self):
        client.post("/api/system/sync-engine/start", headers=AUTH_HEADER)
        client.post("/api/system/sync-engine/stop", headers=AUTH_HEADER)
        resp = client.get("/api/system/sync-engine/status", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        # Engine should have stopped
        assert data["running"] is False
        assert data["status"] == "idle"

    def test_status_without_token(self):
        resp = client.get("/api/system/sync-engine/status")
        assert resp.status_code == 401


class TestSyncEngineStats:
    """GET /api/system/sync-engine/stats"""

    def test_stats_default(self):
        resp = client.get("/api/system/sync-engine/stats", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_scans" in data
        assert "total_processed" in data
        assert "success_count" in data
        assert "failed_count" in data
        assert "success_rate" in data
        assert "failure_distribution" in data
        assert isinstance(data["failure_distribution"], dict)
        # All zero initially
        assert data["total_scans"] == 0
        assert data["success_rate"] == 0.0

    def test_stats_with_logs(self):
        """Insert some sync_logs and verify stats reflect them."""
        conn = get_db()
        from datetime import datetime
        now = datetime.now().isoformat()

        # Insert tasks first (required by FK constraint)
        conn.execute(
            "INSERT INTO tasks (task_id, title, priority, status, created_channel, created_at, updated_at) "
            "VALUES ('t1', 'Task 1', 'P2', 'pending', 'api_test', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO tasks (task_id, title, priority, status, created_channel, created_at, updated_at) "
            "VALUES ('t2', 'Task 2', 'P2', 'pending', 'api_test', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO tasks (task_id, title, priority, status, created_channel, created_at, updated_at) "
            "VALUES ('t3', 'Task 3', 'P2', 'pending', 'api_test', ?, ?)",
            (now, now),
        )
        conn.commit()

        # Insert sync_state entries
        conn.execute(
            "INSERT INTO sync_state (sync_id, task_id, sync_target, sync_key, sync_status, created_at, updated_at) "
            "VALUES ('s1', 't1', 'apple_calendar', 'k1', 'synced', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO sync_state (sync_id, task_id, sync_target, sync_key, sync_status, created_at, updated_at) "
            "VALUES ('s2', 't2', 'apple_reminder', 'k2', 'synced', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO sync_state (sync_id, task_id, sync_target, sync_key, sync_status, created_at, updated_at) "
            "VALUES ('s3', 't3', 'apple_calendar', 'k3', 'failed', ?, ?)",
            (now, now),
        )
        conn.commit()

        # Insert sync_logs
        conn.execute(
            "INSERT INTO sync_logs (log_id, sync_id, local_task_id, sync_target, sync_attempt, sync_result, error_code, created_at) "
            "VALUES ('l1', 's1', 't1', 'apple_calendar', 1, 'success', NULL, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO sync_logs (log_id, sync_id, local_task_id, sync_target, sync_attempt, sync_result, error_code, created_at) "
            "VALUES ('l2', 's2', 't2', 'apple_reminder', 1, 'success', NULL, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO sync_logs (log_id, sync_id, local_task_id, sync_target, sync_attempt, sync_result, error_code, created_at) "
            "VALUES ('l3', 's3', 't3', 'apple_calendar', 1, 'failed', 'timeout', ?)",
            (now,),
        )
        conn.commit()

        resp = client.get("/api/system/sync-engine/stats", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_scans"] == 3
        assert data["total_processed"] == 3  # all have non-empty sync_result
        assert data["success_count"] == 2
        assert data["failed_count"] == 1
        assert data["success_rate"] == round((2 / 3) * 100, 2)
        assert data["failure_distribution"] == {"timeout": 1}

    def test_stats_without_token(self):
        resp = client.get("/api/system/sync-engine/stats")
        assert resp.status_code == 401
