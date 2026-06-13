"""Phase 44 — Productized CLI for creating local tasks."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime

from local_api.database import get_db
from local_api.services.sync_state_service import create_sync_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create-task",
        description="Create a local task and queue it for background sync.",
    )
    parser.add_argument("--title", required=True, help="Task title")
    parser.add_argument("--start", required=True, help="Start time (ISO format, e.g., 2026-06-13T10:00:00)")
    parser.add_argument("--end", required=True, help="End time (ISO format, e.g., 2026-06-13T11:00:00)")
    parser.add_argument("--notes", default=None, help="Optional task description/notes")
    parser.add_argument("--target", default="apple_calendar", help="Sync target (default: apple_calendar)")
    parser.add_argument("--sync", action="store_true", help="Immediately sync the created task")
    return parser


def parse_iso(dt_str: str) -> datetime:
    # Minimal support for ISO from command line
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    title = args.title.strip()
    if not title:
        print("Error: --title cannot be empty.", file=sys.stderr)
        return 1
        
    target = args.target.strip()
    if target != "apple_calendar":
        print(f"Error: Unknown target '{target}'. Only 'apple_calendar' is supported.", file=sys.stderr)
        return 1

    try:
        start_dt = parse_iso(args.start)
        end_dt = parse_iso(args.end)
    except ValueError as e:
        print(f"Error: Invalid date format. Please use ISO format (e.g., 2026-06-13T10:00:00).", file=sys.stderr)
        return 1

    if end_dt <= start_dt:
        print("Error: --end must be strictly after --start.", file=sys.stderr)
        return 1

    task_id = str(uuid.uuid4()).upper()
    now_iso = datetime.utcnow().isoformat() + "Z"

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, title, description, priority, status,
                start_time, due_time, timezone, location,
                need_weather_check, reminder_channels, created_channel,
                sync_enabled, sync_targets,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                title,
                args.notes.strip() if args.notes else None,
                "P2",
                "pending",
                args.start,
                args.end,
                "Asia/Shanghai",  # Defaulting for CLI
                None,
                0,
                '["local_ui"]',
                "api_test",
                1,
                json.dumps([target]),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error creating task in database: {e}", file=sys.stderr)
        return 1

    # Create the sync state so it gets picked up by the background agent
    try:
        create_sync_state(
            task_id=task_id,
            sync_target=target,
            sync_status="pending",
        )
    except Exception as e:
        print(f"Error queuing sync state: {e}", file=sys.stderr)
        return 1

    print("\n✅ Task created successfully!")
    print(f"  task_id      : {task_id}")
    print(f"  title        : {title}")
    print(f"  start        : {args.start}")
    print(f"  end          : {args.end}")
    print(f"  target       : {target}")
    
    if not args.sync:
        print(f"  sync_status  : pending")
        print("\nNext step: Run `python -m local_api.scripts.sync_trigger sync-pending` to sync now, or wait for background agent.")
        return 0

    print(f"  initial_sync_status : pending")
    print("\nTriggering sync...")
    try:
        from local_api.scripts.sync_trigger import build_services
        service, scheduler = build_services(dry_run=False, explicit_target=target)
        result = scheduler.sync_task(task_id, target)
        
        # Read the external_id from db
        conn = get_db()
        row = conn.execute("SELECT sync_status, external_id FROM sync_state WHERE task_id=? AND sync_target=?", (task_id, target)).fetchone()
        
        final_status = row["sync_status"] if row else result.get("status", "unknown")
        ext_id = row["external_id"] if row else None
        
        print(f"  final_sync_status   : {final_status}")
        print(f"  external_id         : {ext_id}")
        
        if result.get("success"):
            return 0
        else:
            err_msg = result.get("error") or "Unknown error"
            print(f"  error               : {err_msg}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Error during sync: {e}", file=sys.stderr)
        return 1



if __name__ == "__main__":
    sys.exit(main())
