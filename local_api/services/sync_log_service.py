"""Phase 4.4 — Sync log service for CRUD operations on sync_logs."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from ..database import get_db
from ..config import ALLOWED_SYNC_TARGETS, ALLOWED_SYNC_RESULTS

from .sync_state_service import _generate_log_id, _iso_now

logger = logging.getLogger("local_api.sync_log_service")

REDACTED_ERROR_MESSAGE = "REDACTED"


def create_sync_log(
    sync_id: str,
    sync_target: str,
    sync_result: str,
    local_task_id: Optional[str] = None,
    sync_attempt: int = 1,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    drift_detected: bool = False,
    drift_fields: Optional[str] = None,
    payload_hash_before: Optional[str] = None,
    payload_hash_after: Optional[str] = None,
    external_id_before: Optional[str] = None,
    external_id_after: Optional[str] = None,
    request_id: Optional[str] = None,
    triggered_by: str = "api",
) -> dict:
    """Create a new sync_log record.
    
    Returns the created record as a dict.
    """
    if sync_target not in ALLOWED_SYNC_TARGETS:
        raise ValueError(f"Invalid sync_target: {sync_target}")
    if sync_result not in ALLOWED_SYNC_RESULTS:
        raise ValueError(f"Invalid sync_result: {sync_result}")

    conn = get_db()
    now = _iso_now()
    log_id = _generate_log_id()
    redacted_error_message = REDACTED_ERROR_MESSAGE if error_message is not None else None

    sync = conn.execute(
        "SELECT sync_id FROM sync_state WHERE sync_id = ?", (sync_id,)
    ).fetchone()
    if sync is None:
        raise ValueError(f"Sync state not found: {sync_id}")

    conn.execute(
        """INSERT INTO sync_logs (
            log_id, sync_id, local_task_id, sync_target, sync_attempt,
            sync_result, error_code, error_message, drift_detected, drift_fields,
            payload_hash_before, payload_hash_after,
            external_id_before, external_id_after,
            request_id, triggered_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            log_id, sync_id, local_task_id, sync_target, sync_attempt,
            sync_result, error_code, redacted_error_message,
            1 if drift_detected else 0, drift_fields,
            payload_hash_before, payload_hash_after,
            external_id_before, external_id_after,
            request_id, triggered_by, now,
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM sync_logs WHERE log_id = ?", (log_id,)
    ).fetchone()
    return dict(row)


def get_sync_log(log_id: str) -> Optional[dict]:
    """Get a sync_log record by log_id."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sync_logs WHERE log_id = ?", (log_id,)
    ).fetchone()
    return dict(row) if row else None


def list_sync_logs(
    sync_id: Optional[str] = None,
    sync_target: Optional[str] = None,
    sync_result: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List sync_log records with optional filters."""
    conn = get_db()
    where_clauses = []
    params = []

    if sync_id is not None:
        where_clauses.append("sync_id = ?")
        params.append(sync_id)
    if sync_target is not None:
        if sync_target not in ALLOWED_SYNC_TARGETS:
            raise ValueError(f"Invalid sync_target: {sync_target}")
        where_clauses.append("sync_target = ?")
        params.append(sync_target)
    if sync_result is not None:
        if sync_result not in ALLOWED_SYNC_RESULTS:
            raise ValueError(f"Invalid sync_result: {sync_result}")
        where_clauses.append("sync_result = ?")
        params.append(sync_result)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    count_row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM sync_logs WHERE {where_sql}", params
    ).fetchone()
    total = count_row["cnt"]

    rows = conn.execute(
        f"SELECT * FROM sync_logs WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return [dict(r) for r in rows], total


def delete_logs_by_sync_id(sync_id: str) -> int:
    """Delete all sync_log records for a given sync_id. Returns count deleted."""
    conn = get_db()
    cursor = conn.execute("DELETE FROM sync_logs WHERE sync_id = ?", (sync_id,))
    conn.commit()
    return cursor.rowcount
