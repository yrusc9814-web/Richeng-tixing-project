"""Phase59 — local reminder policy engine (no external sends)."""

import hashlib
import json
import secrets
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ..database import get_db


def _iso_now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _log_id() -> str:
    return f"notif_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def _payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _insert_log(task_id: str, channel: str, trigger_type: str, scheduled_for: Optional[str],
                payload: dict, status: str = "pending", error_message: Optional[str] = None) -> dict:
    conn = get_db()
    now = _iso_now()
    digest = _payload_hash(payload)
    existing = conn.execute(
        """SELECT * FROM notification_log
           WHERE task_id = ? AND channel = ? AND trigger_type = ? AND payload_hash = ?""",
        (task_id, channel, trigger_type, digest),
    ).fetchone()
    if existing:
        return dict(existing)
    conn.execute(
        """INSERT INTO notification_log (
            id, task_id, channel, trigger_type, scheduled_for, sent_at, status,
            payload_hash, error_message, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_log_id(), task_id, channel, trigger_type, scheduled_for, None, status,
         digest, error_message, now, now),
    )
    conn.commit()
    row = conn.execute(
        """SELECT * FROM notification_log
           WHERE task_id = ? AND channel = ? AND trigger_type = ? AND payload_hash = ?""",
        (task_id, channel, trigger_type, digest),
    ).fetchone()
    return dict(row)


def generate_reminders_for_task(task: dict, *, trigger_type: str = "time") -> list[dict]:
    policy = task.get("notify_policy") or "calendar_only"
    if policy == "silent":
        return []
    scheduled_for = task.get("start_time") or task.get("due_time")
    actions = []
    if policy == "calendar_only":
        payload = {"task_id": task["task_id"], "channel": "apple_calendar", "trigger": trigger_type, "scheduled_for": scheduled_for}
        actions.append(_insert_log(task["task_id"], "apple_calendar", trigger_type, scheduled_for, payload))
    elif policy in {"wechat_normal", "wechat_important", "wechat_emergency"}:
        payload = {"task_id": task["task_id"], "channel": "wechat", "trigger": trigger_type, "policy": policy, "scheduled_for": scheduled_for}
        actions.append(_insert_log(task["task_id"], "wechat", trigger_type, scheduled_for, payload))
    return actions


def record_notification_failure(task_id: str, channel: str, trigger_type: str,
                                scheduled_for: Optional[str], payload: dict,
                                error_message: str) -> dict:
    return _insert_log(task_id, channel, trigger_type, scheduled_for, payload, "failed", error_message)


def reminder_summary() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT status, channel, COUNT(*) AS cnt FROM notification_log GROUP BY status, channel").fetchall()
    summary = {"today_pending": 0, "sent": 0, "failed": 0, "wechat": 0, "apple": 0}
    today = _iso_now()[:10]
    today_pending = conn.execute(
        "SELECT COUNT(*) AS cnt FROM notification_log WHERE status = 'pending' AND substr(COALESCE(scheduled_for, created_at), 1, 10) = ?",
        (today,),
    ).fetchone()["cnt"]
    summary["today_pending"] = today_pending
    for row in rows:
        if row["status"] == "sent":
            summary["sent"] += row["cnt"]
        if row["status"] == "failed":
            summary["failed"] += row["cnt"]
        if row["channel"] == "wechat":
            summary["wechat"] += row["cnt"]
        if row["channel"] == "apple_calendar":
            summary["apple"] += row["cnt"]
    return summary
