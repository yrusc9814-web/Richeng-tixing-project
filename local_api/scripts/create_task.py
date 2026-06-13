"""Phase 43 — Minimal CLI for creating local tasks."""

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
    parser.add_argument("--end", required=True, help="End time (ISO format)")
    parser.add_argument("--notes", default=None, help="Optional task description/notes")
    parser.add_argument("--target", default="apple_calendar", help="Sync target (default: apple_calendar)")
    return parser


def parse_iso(dt_str: str) -> datetime:
    # Minimal support for ISO from command line
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.title.strip():
        print("Error: --title cannot be empty.", file=sys.stderr)
        return 1

    try:
        start_dt = parse_iso(args.start)
        end_dt = parse_iso(args.end)
    except ValueError as e:
        print(f"Error: Invalid date format. {e}", file=sys.stderr)
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
                args.title.strip(),
                args.notes.strip() if args.notes else None,
                "P2",
                "pending",
                args.start,
                args.end,
                "Asia/Shanghai",  # Defaulting for minimal CLI
                None,
                0,
                '["local_ui"]',
                "api_test",
                1,
                json.dumps([args.target]),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error creating task: {e}", file=sys.stderr)
        return 1

    # Create the sync state so it gets picked up by the background agent
    try:
        create_sync_state(
            task_id=task_id,
            sync_target=args.target,
            sync_status="pending",
        )
    except Exception as e:
        print(f"Error queuing sync state: {e}", file=sys.stderr)
        return 1

    print(f"Task created successfully! task_id={task_id}")
    print(f"Sync state queued for {args.target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
