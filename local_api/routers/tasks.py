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
from ..services.sync_eligibility import eligible_for_calendar_sync
from ..services.sync_log_service import create_sync_log
from ..services.sync_state_service import (
    create_sync_state,
    get_sync_state_by_key,
    transition_sync_state,
)
from ..sync_client.payload import compute_task_payload_hash
from ..adapters.apple_adapter import AppleSyncAdapter

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
    """Convert a sqlite3.Row to a TaskResponse dict, then validate.

    Phase57: also extracts sync_enabled, sync_targets, last_sync_status.
    """
    d = dict(row)
    # Parse JSON fields
    if isinstance(d.get("reminder_channels"), str):
        try:
            d["reminder_channels"] = json.loads(d["reminder_channels"])
        except (json.JSONDecodeError, TypeError):
            d["reminder_channels"] = ["local_ui"]
    if isinstance(d.get("sync_targets"), str):
        try:
            d["sync_targets"] = json.loads(d["sync_targets"])
        except (json.JSONDecodeError, TypeError):
            d["sync_targets"] = ["apple_calendar"]
    d["need_weather_check"] = bool(d.get("need_weather_check", False))
    d["sync_enabled"] = bool(d.get("sync_enabled", False))
    # last_sync_status may be enriched later by _enrich_tasks_with_sync_info
    return TaskResponse(**d)


def _enrich_tasks_with_sync_info(tasks: list[TaskResponse]) -> list[TaskResponse]:
    """Batch-query sync_state for listed tasks and populate last_sync_status.

    Uses a single query instead of N+1 lookups. Only queries apple_calendar
    sync states since that is the primary sync target displayed in the UI.
    """
    task_ids = [t.task_id for t in tasks if t.sync_enabled]
    if not task_ids:
        return tasks

    conn = get_db()
    placeholders = ",".join("?" * len(task_ids))
    sync_rows = conn.execute(
        f"""SELECT ss.task_id, ss.sync_target, ss.sync_status, ss.last_synced_at
            FROM sync_state ss
            WHERE ss.task_id IN ({placeholders})
            ORDER BY ss.created_at DESC""",
        task_ids,
    ).fetchall()

    # Build map: task_id -> {sync_target -> sync_status}
    sync_map: dict[str, dict[str, str]] = {}
    for sr in sync_rows:
        tid = sr["task_id"]
        if tid not in sync_map:
            sync_map[tid] = {}
        target = sr["sync_target"]
        if target not in sync_map[tid]:
            # First (latest due to ORDER BY) entry per target wins
            sync_map[tid][target] = sr["sync_status"]

    enriched = []
    for t in tasks:
        t_dict = t.model_dump()
        if t.task_id in sync_map:
            cal_status = sync_map[t.task_id].get("apple_calendar")
            if cal_status is not None:
                t_dict["last_sync_status"] = cal_status
        enriched.append(TaskResponse(**t_dict))
    return enriched


def _task_to_eligibility_dict(row) -> dict:
    """Return a raw task dict suitable for Calendar sync eligibility checks."""
    return dict(row)


_CALENDAR_SYNC_TARGET = "apple_calendar"
_SYNC_RELEVANT_TASK_FIELDS = {
    "title",
    "description",
    "start_time",
    "due_time",
    "timezone",
    "location",
    "priority",
    "status",
}


def _sync_relevant_fields_changed(before: dict, after: dict) -> bool:
    """Return whether fields mirrored to Calendar changed."""
    return any(before.get(field) != after.get(field) for field in _SYNC_RELEVANT_TASK_FIELDS)


def _ensure_pending_calendar_sync_state_if_eligible(task: dict) -> None:
    """Create a pending apple_calendar sync_state only when eligible and missing."""
    result = eligible_for_calendar_sync(task, _CALENDAR_SYNC_TARGET)
    if not result.allowed:
        logger.info(
            "calendar_sync_state_not_enqueued task_id=%s reason=%s",
            task.get("task_id"),
            result.reason,
        )
        return

    existing = get_sync_state_by_key(task["task_id"], _CALENDAR_SYNC_TARGET)
    if existing is not None:
        return

    try:
        create_sync_state(
            task_id=task["task_id"],
            sync_target=_CALENDAR_SYNC_TARGET,
            sync_status="pending",
        )
    except ValueError as exc:
        logger.warning(
            "calendar_sync_state_enqueue_failed task_id=%s error=%s",
            task.get("task_id"),
            str(exc),
        )
        raise


def _sync_calendar_state_after_task_update(before: dict, after: dict) -> None:
    """Maintain apple_calendar sync_state after task updates without writing Calendar.

    Phase 21: uses payload_hash comparison for drift detection when available.
    Falls back to field-diff for legacy rows with no stored hash.
    """
    existing = get_sync_state_by_key(after["task_id"], _CALENDAR_SYNC_TARGET)

    # Only process synced Calendar records with an external_id
    if (
        existing is not None
        and existing.get("sync_status") == "synced"
        and existing.get("external_id")
    ):
        stored_hash = existing.get("payload_hash")

        if stored_hash is not None:
            # Phase 21: hash-based drift detection
            after_hash = compute_task_payload_hash(after)
            if stored_hash == after_hash:
                # No change in sync-relevant fields — stay synced
                return

            # Drift detected — mark stale and write drift log
            transition_sync_state(existing["sync_id"], "stale", trigger="trigger")

            # Collect which sync-relevant fields actually changed
            changed_fields = sorted(
                f for f in _SYNC_RELEVANT_TASK_FIELDS
                if before.get(f) != after.get(f)
            )
            drift_fields = ",".join(changed_fields) if changed_fields else None

            try:
                create_sync_log(
                    sync_id=existing["sync_id"],
                    local_task_id=after["task_id"],
                    sync_target=_CALENDAR_SYNC_TARGET,
                    sync_result="drift_detected",
                    drift_detected=True,
                    drift_fields=drift_fields,
                    payload_hash_before=stored_hash,
                    payload_hash_after=after_hash,
                    external_id_before=existing["external_id"],
                    external_id_after=existing["external_id"],
                    triggered_by="system",
                )
            except Exception:
                logger.warning(
                    "drift_log_write_failed task_id=%s sync_id=%s",
                    after["task_id"], existing["sync_id"],
                )
            return

        # Legacy fallback: no stored payload_hash — use field-diff
        if _sync_relevant_fields_changed(before, after):
            transition_sync_state(existing["sync_id"], "stale", trigger="trigger")

            # Phase 23: write traceable drift log for legacy no-hash rows
            changed_fields = sorted(
                f for f in _SYNC_RELEVANT_TASK_FIELDS
                if before.get(f) != after.get(f)
            )
            drift_fields = ",".join(changed_fields) if changed_fields else None
            try:
                create_sync_log(
                    sync_id=existing["sync_id"],
                    local_task_id=after["task_id"],
                    sync_target=_CALENDAR_SYNC_TARGET,
                    sync_result="drift_detected",
                    drift_detected=True,
                    drift_fields=drift_fields,
                    payload_hash_before=None,  # legacy: no stored hash
                    payload_hash_after=compute_task_payload_hash(after),
                    external_id_before=existing["external_id"],
                    external_id_after=existing["external_id"],
                    triggered_by="system",
                )
            except Exception:
                logger.warning(
                    "drift_log_write_failed task_id=%s sync_id=%s (legacy no-hash)",
                    after["task_id"], existing["sync_id"],
                )
        return

    if existing is None or existing.get("sync_status") != "synced":
        _ensure_pending_calendar_sync_state_if_eligible(after)


_TERMINAL_CLEANUP_SKIP_STATUSES = frozenset({"deleted", "disabled", "orphaned"})


def _cleanup_calendar_on_terminal(task_id: str) -> None:
    """Remove apple_calendar event for a task entering a terminal status.

    Called after complete_task() or update_task() when status becomes
    completed or cancelled.  Idempotent — safe to call multiple times.

    Never blocks the caller (logs and returns on any failure).
    """
    sync_state = get_sync_state_by_key(task_id, _CALENDAR_SYNC_TARGET)
    if sync_state is None:
        return
    external_id = sync_state.get("external_id")
    if not external_id:
        logger.info(
            "calendar_terminal_cleanup_skip task_id=%s reason=no_external_id",
            task_id,
        )
        return
    status = sync_state.get("sync_status", "")
    if status in _TERMINAL_CLEANUP_SKIP_STATUSES:
        logger.info(
            "calendar_terminal_cleanup_skip task_id=%s reason=already_%s",
            task_id, status,
        )
        return

    try:
        adapter = AppleSyncAdapter(target=_CALENDAR_SYNC_TARGET)
        removed = adapter.remove_event(external_id)
    except Exception as exc:
        logger.warning(
            "calendar_terminal_cleanup_exception task_id=%s error=%s",
            task_id, exc,
        )
        return

    if removed:
        try:
            transition_sync_state(sync_state["sync_id"], "disabled", trigger="manual")
            logger.info(
                "calendar_terminal_cleanup_ok task_id=%s sync_id=%s",
                task_id, sync_state["sync_id"],
            )
        except Exception as exc:
            logger.warning(
                "calendar_terminal_cleanup_transition_failed task_id=%s error=%s",
                task_id, exc,
            )
    else:
        logger.warning(
            "calendar_terminal_cleanup_failed task_id=%s external_id=%s",
            task_id, external_id,
        )


# ── POST /api/tasks ────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=TaskResponse)
def create_task(request: Request, body: TaskCreateRequest):
    """Create a new task. task_id is server-generated."""
    conn = get_db()
    now = _iso_now()
    task_id = _generate_task_id()

    reminder_json = json.dumps(body.reminder_channels, ensure_ascii=False)

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
                1 if body.sync_enabled else 0,
                json.dumps(body.sync_targets, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    _ensure_pending_calendar_sync_state_if_eligible(_task_to_eligibility_dict(row))
    logger.info("task_created task_id=%s title=REDACTED request_id=%s", task_id, getattr(request.state, "request_id", "?"))
    result = _row_to_response(row)
    enriched = _enrich_tasks_with_sync_info([result])
    return enriched[0] if enriched else result


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
    tasks = _enrich_tasks_with_sync_info(tasks)
    return TaskListResponse(tasks=tasks, total=total, limit=limit, offset=offset)


# ── GET /api/tasks/{task_id} ───────────────────────────────────────────────


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(request: Request, task_id: str):
    """Get a single task by ID. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = _row_to_response(row)
    # Enrich single task with sync info
    enriched = _enrich_tasks_with_sync_info([result])
    return enriched[0] if enriched else result


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
        "sync_enabled": lambda v: 1 if v else 0,
        "sync_targets": lambda v: json.dumps(v, ensure_ascii=False),
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

    before_update = _task_to_eligibility_dict(row)
    try:
        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?",
            params,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    _sync_calendar_state_after_task_update(before_update, _task_to_eligibility_dict(row))
    after_status = row["status"] if row else None
    if after_status == "cancelled":
        _cleanup_calendar_on_terminal(task_id)
    logger.info("task_updated task_id=%s request_id=%s", task_id, getattr(request.state, "request_id", "?"))
    result = _row_to_response(row)
    enriched = _enrich_tasks_with_sync_info([result])
    return enriched[0] if enriched else result


# ── POST /api/tasks/{task_id}/complete ─────────────────────────────────────


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(request: Request, task_id: str):
    """Mark a task as completed. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    now = _iso_now()
    try:
        conn.execute(
            "UPDATE tasks SET status = 'completed', updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    _cleanup_calendar_on_terminal(task_id)
    logger.info("task_completed task_id=%s request_id=%s", task_id, getattr(request.state, "request_id", "?"))
    result = _row_to_response(row)
    enriched = _enrich_tasks_with_sync_info([result])
    return enriched[0] if enriched else result


# ── DELETE /api/tasks/{task_id} ──────────────────────────────────────────────


@router.delete("/{task_id}", status_code=204)
def delete_task(request: Request, task_id: str):
    """Delete a task. Cleans up Calendar event before deletion if one exists."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    _cleanup_calendar_on_terminal(task_id)

    try:
        conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    logger.info("task_deleted task_id=%s request_id=%s", task_id, getattr(request.state, "request_id", "?"))
