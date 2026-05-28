"""Phase 4.4 — Sync state router for managing sync_state records."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..sync_models import (
    SyncStateCreateRequest,
    SyncStateUpdateRequest,
    SyncStateResponse,
    SyncStateListResponse,
)
from ..services.sync_state_service import (
    create_sync_state,
    get_sync_state,
    get_sync_state_by_key,
    list_sync_states,
    update_sync_state,
    delete_sync_state,
)

router = APIRouter(prefix="/api/sync/state", tags=["sync_state"])
logger = logging.getLogger("local_api.sync")


def _row_to_response(row: dict) -> SyncStateResponse:
    """Convert a dict row to SyncStateResponse."""
    return SyncStateResponse(**row)


# ── POST /api/sync/state ────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=SyncStateResponse)
def create_sync_state_endpoint(request: Request, body: SyncStateCreateRequest):
    """Create a new sync state record."""
    try:
        record = create_sync_state(
            task_id=body.task_id,
            sync_target=body.sync_target,
            external_id=body.external_id,
            sync_status=body.sync_status,
            payload_hash=body.payload_hash,
        )
        logger.info(
            "sync_state_created sync_id=%s task_id=%s target=%s request_id=%s",
            record["sync_id"], record["task_id"], record["sync_target"],
            getattr(request.state, "request_id", "?"),
        )
        return _row_to_response(record)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── GET /api/sync/state ─────────────────────────────────────────────────────


@router.get("", response_model=SyncStateListResponse)
def list_sync_states_endpoint(
    request: Request,
    task_id: Optional[str] = Query(default=None),
    sync_target: Optional[str] = Query(default=None),
    sync_status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List sync state records with optional filters."""
    try:
        records, total = list_sync_states(
            task_id=task_id,
            sync_target=sync_target,
            sync_status=sync_status,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return SyncStateListResponse(
        items=[_row_to_response(r) for r in records],
        total=total,
    )


# ── GET /api/sync/state/by-key ──────────────────────────────────────────────


@router.get("/by-key", response_model=SyncStateResponse)
def get_sync_state_by_key_endpoint(
    request: Request,
    task_id: str = Query(...),
    sync_target: str = Query(...),
):
    """Get a sync state record by task_id and sync_target."""
    try:
        record = get_sync_state_by_key(task_id, sync_target)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sync state not found for task_id={task_id}, target={sync_target}",
        )
    return _row_to_response(record)


# ── GET /api/sync/state/{sync_id} ───────────────────────────────────────────


@router.get("/{sync_id}", response_model=SyncStateResponse)
def get_sync_state_endpoint(request: Request, sync_id: str):
    """Get a sync state record by sync_id."""
    record = get_sync_state(sync_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Sync state not found: {sync_id}")
    return _row_to_response(record)


# ── PATCH /api/sync/state/{sync_id} ─────────────────────────────────────────


@router.patch("/{sync_id}", response_model=SyncStateResponse)
def update_sync_state_endpoint(
    request: Request, sync_id: str, body: SyncStateUpdateRequest
):
    """Update a sync state record."""
    update_kwargs = body.model_dump(exclude_unset=True)
    try:
        record = update_sync_state(sync_id, **update_kwargs)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if record is None:
        raise HTTPException(status_code=404, detail=f"Sync state not found: {sync_id}")

    logger.info(
        "sync_state_updated sync_id=%s request_id=%s",
        sync_id, getattr(request.state, "request_id", "?"),
    )
    return _row_to_response(record)


# ── DELETE /api/sync/state/{sync_id} ────────────────────────────────────────


@router.delete("/{sync_id}", status_code=204)
def delete_sync_state_endpoint(request: Request, sync_id: str):
    """Delete a sync state record."""
    deleted = delete_sync_state(sync_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sync state not found: {sync_id}")
    logger.info(
        "sync_state_deleted sync_id=%s request_id=%s",
        sync_id, getattr(request.state, "request_id", "?"),
    )
