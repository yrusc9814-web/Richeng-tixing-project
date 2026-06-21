"""Thin LifeSync closure aliases for product-level API flows."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..database import get_db
from ..notify.wechat_channel import WeChatNotifyChannel
from ..services.reminder_policy import _insert_log
from ..services.weather_rules import evaluate_weather_for_task, fetch_and_store_real_forecast

router = APIRouter(tags=["lifesync"])


class WeatherEvaluateRequest(BaseModel):
    task_id: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    forecast_time: Optional[str] = Field(default=None)


class WeChatSendRequest(BaseModel):
    task_id: str = Field(..., min_length=1)
    trigger_type: str = Field(default="manual")


class CalendarSyncRequest(BaseModel):
    task_id: str = Field(..., min_length=1)
    sync_target: str = Field(default="apple_calendar")


def _iso_now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _task_or_404(task_id: str) -> dict[str, Any]:
    row = get_db().execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    task = dict(row)
    task["need_weather_check"] = bool(task.get("need_weather_check"))
    task["weather_sensitive"] = bool(task.get("weather_sensitive"))
    task["sync_enabled"] = bool(task.get("sync_enabled"))
    return task


def _upsert_sync_blocked(task_id: str, sync_target: str, error_message: str) -> dict[str, Any]:
    conn = get_db()
    now = _iso_now()
    sync_key = f"{task_id}:{sync_target}"
    sync_id = f"sync_blocked_{hashlib.sha256(sync_key.encode()).hexdigest()[:16]}"
    try:
        conn.execute(
            """
            INSERT INTO sync_state (
                sync_id, task_id, sync_target, sync_key, payload_hash, sync_status,
                sync_version, external_id, last_synced_at, last_sync_trigger,
                started_at, locked_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'failed_permanent', 1, NULL, NULL, 'manual', NULL, NULL, ?, ?)
            ON CONFLICT(sync_key) DO UPDATE SET
                sync_status = 'failed_permanent',
                last_sync_trigger = 'manual',
                updated_at = excluded.updated_at
            """,
            (sync_id, task_id, sync_target, sync_key, None, now, now),
        )
        log_id = f"log_{int(datetime.now().timestamp() * 1000)}_{hashlib.sha256((sync_key + now).encode()).hexdigest()[:8]}"
        conn.execute(
            """
            INSERT OR IGNORE INTO sync_logs (
                log_id, sync_id, local_task_id, sync_target, sync_attempt, sync_result,
                error_code, error_message, drift_detected, drift_fields,
                payload_hash_before, payload_hash_after, external_id_before,
                external_id_after, request_id, triggered_by, created_at
            ) VALUES (?, ?, ?, ?, 1, 'failed', 'runtime_unavailable', ?, 0, NULL, NULL, NULL, NULL, NULL, NULL, 'api', ?)
            """,
            (log_id, sync_id, task_id, sync_target, error_message, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    row = conn.execute("SELECT * FROM sync_state WHERE sync_key = ?", (sync_key,)).fetchone()
    return dict(row)


@router.post("/api/weather/evaluate")
def evaluate_weather_alias(body: WeatherEvaluateRequest):
    """Fetch real weather when possible, persist weather_cache, then evaluate a task."""
    task = _task_or_404(body.task_id) if body.task_id else None
    location = body.location or (task or {}).get("location")
    forecast_time = body.forecast_time or (task or {}).get("start_time") or (task or {}).get("due_time")
    if not location:
        return {"ok": False, "status": "blocked", "reason": "missing_location"}

    try:
        forecast = fetch_and_store_real_forecast(location, forecast_time)
    except Exception as exc:
        return {"ok": False, "status": "blocked", "reason": "weather_provider_error", "error": str(exc)}

    if task is None:
        return {"ok": True, "status": "fetched", "forecast": forecast}

    task_for_eval = dict(task)
    task_for_eval["weather_sensitive"] = True
    evaluation = evaluate_weather_for_task(task_for_eval, fetch_real=False)
    return {"ok": True, "status": "evaluated", "forecast": forecast, "evaluation": evaluation}


@router.post("/api/wechat/send")
def send_wechat_alias(body: WeChatSendRequest):
    """Send through the real configured WeChat webhook channel or persist a transparent failure."""
    task = _task_or_404(body.task_id)
    channel = WeChatNotifyChannel(mode=WeChatNotifyChannel.MODE_REAL)
    result = channel.send_reminder(body.task_id, task)
    payload = {
        "task_id": body.task_id,
        "channel": "wechat",
        "trigger": body.trigger_type,
        "status": result.status,
        "message_id": result.message_id,
        "request_id": result.request_id,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "provider_response": result.provider_response,
    }
    log = _insert_log(
        body.task_id,
        "wechat",
        body.trigger_type,
        task.get("start_time") or task.get("due_time"),
        payload,
    )
    if result.success:
        conn = get_db()
        conn.execute(
            "UPDATE notification_log SET status = 'sent', sent_at = ?, error_message = NULL, updated_at = ? WHERE id = ?",
            (_iso_now(), _iso_now(), log["id"]),
        )
        conn.commit()
        log = dict(conn.execute("SELECT * FROM notification_log WHERE id = ?", (log["id"],)).fetchone())
    else:
        conn = get_db()
        conn.execute(
            "UPDATE notification_log SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
            (result.error_message or result.error_code or "wechat_runtime_unavailable", _iso_now(), log["id"]),
        )
        conn.commit()
        log = dict(conn.execute("SELECT * FROM notification_log WHERE id = ?", (log["id"],)).fetchone())
    return {
        "ok": bool(result.success),
        "status": "sent" if result.success else "blocked",
        "message_id": result.message_id if result.success else None,
        "notification": log,
        "provider": result.provider_response,
        "error": None if result.success else (result.error_message or result.error_code),
    }


@router.post("/api/calendar/sync")
def sync_calendar_alias(request: Request, body: CalendarSyncRequest):
    """Push a task to Calendar through the configured SyncService."""
    _task_or_404(body.task_id)
    if body.sync_target != "apple_calendar":
        state = _upsert_sync_blocked(body.task_id, body.sync_target, "Only apple_calendar is supported by this endpoint")
        return {"ok": False, "status": "blocked", "sync_state": state, "error": "unsupported_target"}

    svc = getattr(request.app.state, "sync_service", None)
    if svc is None:
        state = _upsert_sync_blocked(body.task_id, body.sync_target, "Sync service not available")
        return {"ok": False, "status": "blocked", "sync_state": state, "error": "sync_service_unavailable"}

    result = svc.run_task_sync(body.task_id, body.sync_target)
    return {
        "ok": bool(result.get("success")),
        "status": result.get("sync_status"),
        "sync_id": result.get("sync_id"),
        "external_id": result.get("external_id"),
        "error": result.get("error_message") or result.get("error_code"),
        "result": result,
    }
