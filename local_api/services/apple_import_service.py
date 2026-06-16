"""Phase61 — safe local Apple Calendar snapshot import only."""

import json
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import get_db


def _iso_now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _task_id() -> str:
    return f"task_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def import_apple_snapshots(events: list[dict]) -> dict:
    conn = get_db()
    now = _iso_now()
    imported = updated_snapshots = conflicts = 0
    seen = set()
    for event in events:
        external_id = event.get("external_id") or event.get("id")
        if not external_id:
            continue
        seen.add(external_id)
        snapshot = json.dumps(event, ensure_ascii=False, sort_keys=True)
        existing = conn.execute("SELECT * FROM tasks WHERE apple_external_id = ?", (external_id,)).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO tasks (
                    task_id, title, description, priority, status, start_time, due_time,
                    timezone, location, need_weather_check, schedule_type, notify_policy,
                    weather_sensitive, reminder_channels, created_channel, sync_enabled,
                    sync_targets, source, apple_snapshot, apple_external_id,
                    conflict_state, conflict_metadata, created_at, updated_at
                ) VALUES (?, ?, ?, 'P2', 'pending', ?, ?, ?, ?, 0, 'plan', 'calendar_only',
                          0, '["local_ui"]', 'api_test', 0, '["apple_calendar"]',
                          'apple_calendar', ?, ?, NULL, NULL, ?, ?)""",
                (_task_id(), event.get("title") or "Untitled Apple Event", event.get("description"),
                 event.get("start_time"), event.get("due_time"), event.get("timezone") or "Asia/Shanghai",
                 event.get("location"), snapshot, external_id, now, now),
            )
            imported += 1
        else:
            metadata = {"reason": "apple_snapshot_changed", "external_id": external_id}
            conn.execute(
                """UPDATE tasks SET apple_snapshot = ?, conflict_state = ?, conflict_metadata = ?, updated_at = ?
                   WHERE task_id = ?""",
                (snapshot, "needs_review", json.dumps(metadata, ensure_ascii=False), now, existing["task_id"]),
            )
            updated_snapshots += 1
            conflicts += 1
    if seen:
        placeholders = ",".join("?" * len(seen))
        rows = conn.execute(
            f"SELECT task_id, apple_external_id FROM tasks WHERE source = 'apple_calendar' AND apple_external_id NOT IN ({placeholders})",
            list(seen),
        ).fetchall()
    else:
        rows = conn.execute("SELECT task_id, apple_external_id FROM tasks WHERE source = 'apple_calendar'").fetchall()
    missing = 0
    for row in rows:
        metadata = {"reason": "missing_in_apple", "external_id": row["apple_external_id"]}
        conn.execute(
            "UPDATE tasks SET conflict_state = ?, conflict_metadata = ?, updated_at = ? WHERE task_id = ?",
            ("missing_in_apple", json.dumps(metadata, ensure_ascii=False), now, row["task_id"]),
        )
        missing += 1
    conn.commit()
    return {"imported": imported, "updated_snapshots": updated_snapshots, "conflicts": conflicts, "missing_in_apple": missing}
