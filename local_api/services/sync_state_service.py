"""Phase 4.4 — Sync state service for CRUD operations on sync_state."""

import time
import secrets
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from ..database import get_db
from ..config import ALLOWED_SYNC_TARGETS, ALLOWED_SYNC_STATUSES
from .state_transition_validator import validate_transition_or_raise

logger = logging.getLogger("local_api.sync_state_service")


def _generate_sync_id() -> str:
    """Generate a unique sync_id: sync_<ms-timestamp>_<6-char-hex>"""
    ms = int(time.time() * 1000)
    rand = secrets.token_hex(3)
    return f"sync_{ms}_{rand}"


def _generate_log_id() -> str:
    """Generate a unique log_id: log_<ms-timestamp>_<6-char-hex>"""
    ms = int(time.time() * 1000)
    rand = secrets.token_hex(3)
    return f"log_{ms}_{rand}"


def _iso_now() -> str:
    """Return current time as ISO-8601 string with Asia/Shanghai timezone (+08:00)."""
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _build_sync_key(task_id: str, sync_target: str) -> str:
    """Build a sync_key from task_id and sync_target.
    
    Formula: "{task_id}:{sync_target}"
    """
    return f"{task_id}:{sync_target}"


def create_sync_state(
    task_id: str,
    sync_target: str,
    external_id: Optional[str] = None,
    sync_status: str = "pending",
    payload_hash: Optional[str] = None,
) -> dict:
    """Create a new sync_state record.
    
    Returns the created record as a dict.
    Raises ValueError if sync_target or sync_status are invalid,
    or if a record with the same sync_key already exists.
    """
    if sync_target not in ALLOWED_SYNC_TARGETS:
        raise ValueError(f"Invalid sync_target: {sync_target}")
    if sync_status not in ALLOWED_SYNC_STATUSES:
        raise ValueError(f"Invalid sync_status: {sync_status}")

    conn = get_db()
    now = _iso_now()
    sync_id = _generate_sync_id()
    sync_key = _build_sync_key(task_id, sync_target)

    task = conn.execute(
        "SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if task is None:
        raise ValueError(f"Task not found: {task_id}")

    # Check for existing record
    existing = conn.execute(
        "SELECT sync_id FROM sync_state WHERE sync_key = ?", (sync_key,)
    ).fetchone()
    if existing:
        raise ValueError(f"Sync state already exists for key: {sync_key}")

    conn.execute(
        """INSERT INTO sync_state (
            sync_id, task_id, sync_target, sync_key, payload_hash,
            sync_status, sync_version, external_id,
            last_synced_at, last_sync_trigger,
            started_at, locked_at,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            sync_id, task_id, sync_target, sync_key, payload_hash,
            sync_status, 1, external_id,
            None, None,
            None, None,
            now, now,
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    return dict(row)


def get_sync_state(sync_id: str) -> Optional[dict]:
    """Get a sync_state record by sync_id."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    return dict(row) if row else None


def get_sync_state_by_key(task_id: str, sync_target: str) -> Optional[dict]:
    """Get a sync_state record by task_id and sync_target (via sync_key)."""
    if sync_target not in ALLOWED_SYNC_TARGETS:
        raise ValueError(f"Invalid sync_target: {sync_target}")

    conn = get_db()
    sync_key = _build_sync_key(task_id, sync_target)
    row = conn.execute(
        "SELECT * FROM sync_state WHERE sync_key = ?", (sync_key,)
    ).fetchone()
    return dict(row) if row else None


def list_sync_states(
    task_id: Optional[str] = None,
    sync_target: Optional[str] = None,
    sync_status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List sync_state records with optional filters."""
    conn = get_db()
    where_clauses = []
    params = []

    if task_id is not None:
        where_clauses.append("task_id = ?")
        params.append(task_id)
    if sync_target is not None:
        if sync_target not in ALLOWED_SYNC_TARGETS:
            raise ValueError(f"Invalid sync_target: {sync_target}")
        where_clauses.append("sync_target = ?")
        params.append(sync_target)
    if sync_status is not None:
        if sync_status not in ALLOWED_SYNC_STATUSES:
            raise ValueError(f"Invalid sync_status: {sync_status}")
        where_clauses.append("sync_status = ?")
        params.append(sync_status)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    count_row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM sync_state WHERE {where_sql}", params
    ).fetchone()
    total = count_row["cnt"]

    rows = conn.execute(
        f"SELECT * FROM sync_state WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return [dict(r) for r in rows], total


def update_sync_state(
    sync_id: str,
    sync_status: Optional[str] = None,
    sync_version: Optional[int] = None,
    external_id: Optional[str] = None,
    payload_hash: Optional[str] = None,
    last_synced_at: Optional[str] = None,
    last_sync_trigger: Optional[str] = None,
) -> Optional[dict]:
    """Update a sync_state record. Returns the updated record or None if not found."""
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    if existing is None:
        return None

    updates = []
    params = []
    now = _iso_now()

    if sync_status is not None:
        if sync_status not in ALLOWED_SYNC_STATUSES:
            raise ValueError(f"Invalid sync_status: {sync_status}")
        updates.append("sync_status = ?")
        params.append(sync_status)

    if sync_version is not None:
        updates.append("sync_version = ?")
        params.append(sync_version)

    if external_id is not None:
        updates.append("external_id = ?")
        params.append(external_id)

    if payload_hash is not None:
        updates.append("payload_hash = ?")
        params.append(payload_hash)

    if last_synced_at is not None:
        updates.append("last_synced_at = ?")
        params.append(last_synced_at)

    if last_sync_trigger is not None:
        updates.append("last_sync_trigger = ?")
        params.append(last_sync_trigger)

    if not updates:
        return dict(existing)

    updates.append("updated_at = ?")
    params.append(now)
    params.append(sync_id)

    conn.execute(
        f"UPDATE sync_state SET {', '.join(updates)} WHERE sync_id = ?",
        params,
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    return dict(row)


def delete_sync_state(sync_id: str) -> bool:
    """Delete a sync_state record by sync_id. Returns True if deleted."""
    conn = get_db()
    cursor = conn.execute("DELETE FROM sync_state WHERE sync_id = ?", (sync_id,))
    conn.commit()
    return cursor.rowcount > 0


def transition_sync_state(
    sync_id: str,
    to_sync_status: str,
    *,
    trigger: str = "engine",
) -> Optional[dict]:
    """Execute state transition after M2 v4 validation.

    Does not write sync_log and does not manage attempt numbers.
    When transitioning to in_progress:
      - started_at is set only on the very first entry (when NULL in DB)
      - locked_at is set on every entry to in_progress
    """
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    if existing is None:
        return None

    if to_sync_status not in ALLOWED_SYNC_STATUSES:
        raise ValueError(f"Invalid sync_status: {to_sync_status}")

    current_status = existing["sync_status"]
    validate_transition_or_raise(
        current_status,
        to_sync_status,
        trigger=trigger,
        has_external_id=existing["external_id"] is not None,
    )

    if current_status == to_sync_status:
        return dict(existing)

    now = _iso_now()
    updates = ["sync_status = ?", "updated_at = ?"]
    params = [to_sync_status, now]

    if to_sync_status == "in_progress":
        # started_at: only set on the very first entry to in_progress
        if existing["started_at"] is None:
            updates.append("started_at = ?")
            params.append(now)
        # locked_at: update every time we enter in_progress
        updates.append("locked_at = ?")
        params.append(now)

    params.append(sync_id)
    conn.execute(
        f"UPDATE sync_state SET {', '.join(updates)} WHERE sync_id = ?",
        params,
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    return dict(row)
