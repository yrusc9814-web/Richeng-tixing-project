"""Phase58+ — local-safe schedule classification, reminders, weather, and Apple import tests."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["HERMES_API_TOKEN"] = "test-token-hermes-local-4.3"

from local_api.main import app
from local_api.database import get_db, init_db, reset_db

TEST_TOKEN = "test-token-hermes-local-4.3"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_TOKEN}"}
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    reset_db()
    init_db()
    yield
    reset_db()


def _create_task(**overrides):
    payload = {
        "title": "Phase58+ task",
        "start_time": "2026-06-18T09:00:00+08:00",
        "due_time": "2026-06-18T10:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "location": "厦门",
        "created_channel": "local_ui",
        "sync_enabled": True,
        "sync_targets": ["apple_calendar"],
    }
    payload.update(overrides)
    resp = client.post("/api/tasks", json=payload, headers=AUTH_HEADER)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestPhase58ScheduleFields:
    def test_create_defaults_include_phase58_fields(self):
        task = _create_task(title="Default fields")
        assert task["schedule_type"] == "plan"
        assert task["notify_policy"] == "calendar_only"
        assert task["weather_sensitive"] is False
        assert task["reminder_profile"] is None

    def test_create_and_update_phase58_fields(self):
        task = _create_task(
            title="Typed task",
            schedule_type="action",
            notify_policy="wechat_important",
            weather_sensitive=True,
            reminder_profile="户外运动",
        )
        assert task["schedule_type"] == "action"
        assert task["notify_policy"] == "wechat_important"
        assert task["weather_sensitive"] is True
        assert task["reminder_profile"] == "户外运动"

        resp = client.patch(
            f"/api/tasks/{task['task_id']}",
            json={
                "schedule_type": "critical",
                "notify_policy": "wechat_emergency",
                "weather_sensitive": False,
                "reminder_profile": "面试",
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["schedule_type"] == "critical"
        assert data["notify_policy"] == "wechat_emergency"
        assert data["weather_sensitive"] is False
        assert data["reminder_profile"] == "面试"

    def test_invalid_phase58_enums_rejected(self):
        resp = client.post(
            "/api/tasks",
            json={"title": "bad", "schedule_type": "invalid", "created_channel": "local_ui"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 422

    def test_frontend_contains_phase58_fields_and_cards(self):
        html = client.get("/frontend/").text
        app_js = client.get("/frontend/static/app.js").text
        # Phase 58 form fields still present in modal
        for text in ("日程类型", "提醒策略", "天气敏感"):
            assert text in html
        # JS API functions still exist (features preserved via inline card actions)
        for token in ("schedule_type", "notify_policy", "weather_sensitive"):
            assert token in app_js


class TestPhase59ReminderPolicy:
    def test_silent_policy_creates_no_notification(self):
        task = _create_task(notify_policy="silent")
        resp = client.post(f"/api/local/reminders/tasks/{task['task_id']}/generate", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["actions"] == []
        count = get_db().execute("SELECT COUNT(*) AS cnt FROM notification_log").fetchone()["cnt"]
        assert count == 0

    def test_calendar_only_creates_no_wechat_notification_and_deduplicates(self):
        task = _create_task(notify_policy="calendar_only")
        for _ in range(2):
            resp = client.post(f"/api/local/reminders/tasks/{task['task_id']}/generate", headers=AUTH_HEADER)
            assert resp.status_code == 200
        rows = get_db().execute("SELECT channel, status FROM notification_log").fetchall()
        assert [(r["channel"], r["status"]) for r in rows] == [("apple_calendar", "pending")]

    def test_wechat_important_creates_pending_wechat_notification(self):
        task = _create_task(notify_policy="wechat_important")
        resp = client.post(f"/api/local/reminders/tasks/{task['task_id']}/generate", headers=AUTH_HEADER)
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        assert len(actions) == 1
        assert actions[0]["channel"] == "wechat"
        assert actions[0]["status"] == "pending"


class TestPhase60WeatherRules:
    def test_weather_cache_write_and_sensitive_gate(self):
        insensitive = _create_task(title="Indoor", weather_sensitive=False, notify_policy="wechat_normal")
        sensitive = _create_task(title="Outdoor", weather_sensitive=True, notify_policy="wechat_normal")
        forecast = client.post(
            "/api/local/weather/forecasts",
            json={
                "location": "厦门",
                "forecast_time": "2026-06-18T09:00:00+08:00",
                "temperature": 20,
                "condition": "暴雨",
                "rain_probability": 0.9,
                "wind_level": "8",
                "aqi": 60,
                "raw_payload": {"source": "local_acceptance"},
            },
            headers=AUTH_HEADER,
        )
        assert forecast.status_code == 200, forecast.text

        no_trigger = client.post(f"/api/local/weather/tasks/{insensitive['task_id']}/evaluate", headers=AUTH_HEADER)
        assert no_trigger.status_code == 200
        assert no_trigger.json()["triggered"] is False
        assert no_trigger.json()["reason"] == "not_weather_sensitive"

        trigger = client.post(f"/api/local/weather/tasks/{sensitive['task_id']}/evaluate", headers=AUTH_HEADER)
        assert trigger.status_code == 200
        assert trigger.json()["triggered"] is True
        row = get_db().execute("SELECT channel, trigger_type FROM notification_log WHERE task_id = ?", (sensitive["task_id"],)).fetchone()
        assert (row["channel"], row["trigger_type"]) == ("wechat", "weather")

    def test_normal_weather_does_not_notify(self):
        task = _create_task(weather_sensitive=True, notify_policy="wechat_normal")
        client.post(
            "/api/local/weather/forecasts",
            json={"location": "厦门", "forecast_time": "2026-06-18T09:00:00+08:00", "condition": "多云", "rain_probability": 0.1, "aqi": 30},
            headers=AUTH_HEADER,
        )
        resp = client.post(f"/api/local/weather/tasks/{task['task_id']}/evaluate", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert resp.json()["triggered"] is False
        assert resp.json()["reason"] == "normal_weather"


class TestPhase61SafeAppleImport:
    def test_new_apple_snapshot_imports_local_task_without_sync_success(self):
        resp = client.post(
            "/api/local/apple/import-snapshots",
            json={
                "events": [{
                    "external_id": "apple-event-1",
                    "title": "Apple imported event",
                    "start_time": "2026-06-20T09:00:00+08:00",
                    "due_time": "2026-06-20T10:00:00+08:00",
                    "timezone": "Asia/Shanghai",
                    "location": "Apple Room",
                }]
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["imported"] == 1
        row = get_db().execute("SELECT title, source, apple_external_id, sync_enabled FROM tasks WHERE apple_external_id = 'apple-event-1'").fetchone()
        assert row["title"] == "Apple imported event"
        assert row["source"] == "apple_calendar"
        assert row["sync_enabled"] == 0

    def test_existing_apple_snapshot_updates_snapshot_only_and_marks_review(self):
        first = client.post(
            "/api/local/apple/import-snapshots",
            json={"events": [{"external_id": "apple-event-2", "title": "Original Apple", "location": "A"}]},
            headers=AUTH_HEADER,
        )
        assert first.status_code == 200
        second = client.post(
            "/api/local/apple/import-snapshots",
            json={"events": [{"external_id": "apple-event-2", "title": "Changed Apple", "location": "B"}]},
            headers=AUTH_HEADER,
        )
        assert second.status_code == 200
        row = get_db().execute("SELECT title, apple_snapshot, conflict_state FROM tasks WHERE apple_external_id = 'apple-event-2'").fetchone()
        assert row["title"] == "Original Apple"
        assert "Changed Apple" in row["apple_snapshot"]
        assert row["conflict_state"] == "needs_review"

    def test_missing_apple_event_marks_missing_not_delete(self):
        client.post(
            "/api/local/apple/import-snapshots",
            json={"events": [{"external_id": "apple-event-3", "title": "Will be missing"}]},
            headers=AUTH_HEADER,
        )
        resp = client.post("/api/local/apple/import-snapshots", json={"events": []}, headers=AUTH_HEADER)
        assert resp.status_code == 200
        row = get_db().execute("SELECT COUNT(*) AS cnt, conflict_state FROM tasks WHERE apple_external_id = 'apple-event-3'").fetchone()
        assert row["cnt"] == 1
        assert row["conflict_state"] == "missing_in_apple"
