"""Phase 8C — Sync task routes bridging API to SyncService.

Provides high-level sync operations scoped to a task:
  POST /api/sync/tasks/{task_id}/push   — push task to sync target
  POST /api/sync/tasks/{task_id}/pull   — pull from target (TBD)
  GET  /api/sync/tasks/{task_id}/status — get sync status for a task

All routes delegate to SyncService — no direct adapter or engine calls.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import ALLOWED_SYNC_TARGETS

logger = logging.getLogger("local_api.sync_routes")


# ── Request / Response models ─────────────────────────────────────────


class SyncPushRequest(BaseModel):
    """Request body for POST .../push."""
    sync_target: str = Field(..., description="Target to sync to")
    # Allow arbitrary extra fields (extensible for future payload)
    model_config = {"extra": "forbid"}


class SyncTaskResponse(BaseModel):
    """Standard response for sync task operations."""
    ok: bool
    task_id: str
    target: str
    direction: str
    status: str
    external_id: Optional[str] = None
    error: Optional[str] = None
    sync_id: Optional[str] = None


# ── Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/sync/tasks", tags=["sync_tasks"])


def _get_sync_service(request: Request):
    """Get SyncService from app state."""
    svc = getattr(request.app.state, "sync_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Sync service not available")
    return svc


@router.post("/{task_id}/push", response_model=SyncTaskResponse)
def push_task_endpoint(task_id: str, body: SyncPushRequest, request: Request):
    """Push a task to an external sync target.

    Delegates to SyncService.run_task_sync() which coordinates the full
    lifecycle: sync_state creation/find, adapter routing, push execution,
    state transition, and log recording.
    """
    svc = _get_sync_service(request)

    result = svc.run_task_sync(task_id, body.sync_target)

    logger.info(
        "sync_push task_id=%s target=%s ok=%s status=%s",
        task_id, body.sync_target, result["success"], result["sync_status"],
    )

    return SyncTaskResponse(
        ok=result["success"],
        task_id=task_id,
        target=body.sync_target,
        direction="push",
        status=result["sync_status"],
        external_id=result.get("external_id"),
        error=result.get("error_message"),
        sync_id=result.get("sync_id"),
    )


@router.post("/{task_id}/pull", response_model=SyncTaskResponse)
def pull_task_endpoint(task_id: str, request: Request):
    """Pull task data from external sync target.

    Currently not implemented. Phase 8C only covers push direction.
    Pull will be added in a future phase.
    """
    logger.info(
        "sync_pull_not_supported task_id=%s", task_id,
    )
    return SyncTaskResponse(
        ok=False,
        task_id=task_id,
        target="",
        direction="pull",
        status="unsupported",
        error="Pull direction is not yet supported",
    )


@router.get("/{task_id}/status", response_model=SyncTaskResponse)
def get_sync_task_status_endpoint(
    task_id: str,
    request: Request,
    sync_target: Optional[str] = Query(default=None),
):
    """Get the current sync status for a task.

    If sync_target is provided, returns status for that specific target.
    Otherwise returns status for the first available sync_state record.
    """
    from ..services.sync_state_service import list_sync_states

    records, _ = list_sync_states(task_id=task_id, sync_target=sync_target, limit=10)

    if not records:
        # No sync state found — task may not have been synced yet
        return SyncTaskResponse(
            ok=True,
            task_id=task_id,
            target=sync_target or "any",
            direction="status",
            status="not_synced",
            error=None,
            sync_id=None,
        )

    # Return the latest record
    latest = records[0]
    return SyncTaskResponse(
        ok=True,
        task_id=task_id,
        target=latest["sync_target"],
        direction="status",
        status=latest["sync_status"],
        external_id=latest.get("external_id"),
        error=None,
        sync_id=latest["sync_id"],
    )
