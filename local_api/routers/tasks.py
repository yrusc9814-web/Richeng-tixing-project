"""Phase 4.3 — Task CRUD router."""

import json
import time
import secrets
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from ..database import get_db
from ..models import TaskCreateRequest, TaskUpdateRequest, TaskResponse, TaskListResponse
from ..config import MAX_LIMIT, DEFAULT_LIMIT, ALLOWED_PRIORITIES, ALLOWED_STATUSES

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
logger = logging.getLogger("local_api.tasks")


def _generate_task_id() -> str:
    """Generate a unique task_id: task_<ms-timestamp>_<6-char-hex>"""
    ms = int(time.time() * 1000)
    rand = secrets.token_hex(3)  # 6 hex chars
    return f"task_{ms}_{rand}"


def _iso_now() -> str:
    """Return current time as ISO-8601 string with Asia/Shanghai timezone (+08:00)."""
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _row_to_response(row) -> TaskResponse:
    """Convert a sqlite3.Row to a TaskResponse dict, then validate."""
    d = dict(row)
    # Parse JSON fields
    if isinstance(d.get("reminder_channels"), str):
        try:
            d["reminder_channels"] = json.loads(d["reminder_channels"])
        except (json.JSONDecodeError, TypeError):
            d["reminder_channels"] = ["local_ui"]
    d["need_weather_check"] = bool(d.get("need_weather_check", False))
    return TaskResponse(**d)


# ── POST /api/tasks ────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=TaskResponse)
def create_task(request: Request, body: TaskCreateRequest):
    """Create a new task. task_id is server-generated."""
    conn = get_db()
    now = _iso_now()
    task_id = _generate_task_id()

    reminder_json = json.dumps(body.reminder_channels, ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO tasks (
            task_id, title, description, priority, status,
            start_time, due_time, timezone, location,
            need_weather_check, reminder_channels, created_channel,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            body.title.strip(),
            body.description.strip() if body.description else None,
            body.priority,
            body.status,
            body.start_time,
            body.due_time,
            body.timezone,
            body.location.strip() if body.location else None,
            1 if body.need_weather_check else 0,
            reminder_json,
            body.created_channel,
            now,
            now,
        ),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    logger.info("task_created task_id=%s title=REDACTED request_id=%s", task_id, getattr(request.state, "request_id", "?"))
    return _row_to_response(row)


# ── GET /api/tasks ─────────────────────────────────────────────────────────


@router.get("", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    status: Optional[str] = Query(default=None, description="Filter by status (pending/completed/cancelled)"),
    priority: Optional[str] = Query(default=None, description="Filter by priority (P0/P1/P2/P3)"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max tasks per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
):
    """List tasks with optional filters and pagination."""
    conn = get_db()

    where_clauses = []
    params = []

    if status is not None:
        status_lower = status.strip().lower()
        if status_lower not in ALLOWED_STATUSES:
            raise HTTPException(status_code=422, detail=f"Invalid status filter: {status}")
        where_clauses.append("status = ?")
        params.append(status_lower)

    if priority is not None:
        priority_upper = priority.strip().upper()
        if priority_upper not in ALLOWED_PRIORITIES:
            raise HTTPException(status_code=422, detail=f"Invalid priority filter: {priority}")
        where_clauses.append("priority = ?")
        params.append(priority_upper)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Count
    count_row = conn.execute(f"SELECT COUNT(*) as cnt FROM tasks WHERE {where_sql}", params).fetchone()
    total = count_row["cnt"]

    # Fetch page
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    tasks = [_row_to_response(r) for r in rows]
    return TaskListResponse(tasks=tasks, total=total, limit=limit, offset=offset)


# ── GET /api/tasks/{task_id} ───────────────────────────────────────────────


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(request: Request, task_id: str):
    """Get a single task by ID. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _row_to_response(row)


# ── PATCH /api/tasks/{task_id} ─────────────────────────────────────────────


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(request: Request, task_id: str, body: TaskUpdateRequest):
    """Update an existing task. Only supplied fields are changed. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # Build SET clause from non-None fields
    updates = []
    params = []
    now = _iso_now()

    field_map = {
        "title": lambda v: v.strip() if v else v,
        "description": lambda v: v.strip() if v else v,
        "priority": lambda v: v,
        "status": lambda v: v,
        "start_time": lambda v: v,
        "due_time": lambda v: v,
        "timezone": lambda v: v,
        "location": lambda v: v.strip() if v else v,
        "need_weather_check": lambda v: 1 if v else 0,
        "reminder_channels": lambda v: json.dumps(v, ensure_ascii=False),
        "created_channel": lambda v: v,
    }

    update_data = body.model_dump(exclude_unset=True)

    for field, transform in field_map.items():
        if field in update_data and update_data[field] is not None:
            updates.append(f"{field} = ?")
            params.append(transform(update_data[field]))

    if not updates:
        # Nothing to update — return current state
        return _row_to_response(row)

    updates.append("updated_at = ?")
    params.append(now)
    params.append(task_id)

    conn.execute(
        f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?",
        params,
    )
    conn.commit()

    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    logger.info("task_updated task_id=%s request_id=%s", task_id, getattr(request.state, "request_id", "?"))
    return _row_to_response(row)


# ── POST /api/tasks/{task_id}/complete ─────────────────────────────────────


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(request: Request, task_id: str):
    """Mark a task as completed. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    now = _iso_now()
    conn.execute(
        "UPDATE tasks SET status = 'completed', updated_at = ? WHERE task_id = ?",
        (now, task_id),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    logger.info("task_completed task_id=%s request_id=%s", task_id, getattr(request.state, "request_id", "?"))
    return _row_to_response(row)
