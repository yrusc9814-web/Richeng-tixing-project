"""Phase 4.4 — Sync logs router for managing sync_logs records."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..sync_models import (
    SyncLogCreateRequest,
    SyncLogResponse,
    SyncLogListResponse,
)
from ..services.sync_log_service import (
    create_sync_log,
    get_sync_log,
    list_sync_logs,
    delete_logs_by_sync_id,
)

router = APIRouter(prefix="/api/sync/logs", tags=["sync_logs"])
logger = logging.getLogger("local_api.sync")


def _row_to_response(row: dict) -> SyncLogResponse:
    """Convert a dict row to SyncLogResponse."""
    # Convert integer drift_detected to bool
    row = dict(row)
    row["drift_detected"] = bool(row.get("drift_detected", False))
    return SyncLogResponse(**row)


# ── POST /api/sync/logs ─────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=SyncLogResponse)
def create_sync_log_endpoint(request: Request, body: SyncLogCreateRequest):
    """Create a new sync log record."""
    try:
        record = create_sync_log(
            sync_id=body.sync_id,
            local_task_id=body.local_task_id,
            sync_target=body.sync_target,
            sync_attempt=body.sync_attempt,
            sync_result=body.sync_result,
            error_code=body.error_code,
            error_message=body.error_message,
            drift_detected=body.drift_detected,
            drift_fields=body.drift_fields,
            payload_hash_before=body.payload_hash_before,
            payload_hash_after=body.payload_hash_after,
            external_id_before=body.external_id_before,
            external_id_after=body.external_id_after,
            request_id=body.request_id,
            triggered_by=body.triggered_by,
        )
        logger.info(
            "sync_log_created log_id=%s sync_id=%s target=%s result=%s request_id=%s",
            record["log_id"], record["sync_id"], record["sync_target"],
            record["sync_result"], getattr(request.state, "request_id", "?"),
        )
        return _row_to_response(record)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── GET /api/sync/logs ──────────────────────────────────────────────────────


@router.get("", response_model=SyncLogListResponse)
def list_sync_logs_endpoint(
    request: Request,
    sync_id: Optional[str] = Query(default=None),
    sync_target: Optional[str] = Query(default=None),
    sync_result: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List sync log records with optional filters."""
    try:
        records, total = list_sync_logs(
            sync_id=sync_id,
            sync_target=sync_target,
            sync_result=sync_result,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return SyncLogListResponse(
        items=[_row_to_response(r) for r in records],
        total=total,
    )


# ── GET /api/sync/logs/{log_id} ─────────────────────────────────────────────


@router.get("/{log_id}", response_model=SyncLogResponse)
def get_sync_log_endpoint(request: Request, log_id: str):
    """Get a sync log record by log_id."""
    record = get_sync_log(log_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sync log not found: {log_id}")
    return _row_to_response(record)


# ── DELETE /api/sync/logs/{sync_id} ─────────────────────────────────────────


@router.delete("/by-sync/{sync_id}", status_code=200)
def delete_logs_by_sync_id_endpoint(request: Request, sync_id: str):
    """Delete all sync logs for a given sync_id."""
    count = delete_logs_by_sync_id(sync_id)
    logger.info(
        "sync_logs_deleted sync_id=%s count=%d request_id=%s",
        sync_id, count, getattr(request.state, "request_id", "?"),
    )
    return {"deleted": count}
