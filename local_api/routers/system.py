"""Phase 4.3 — System status router."""

import logging
from fastapi import APIRouter, Request

from ..database import get_db
from ..models import SystemStatusResponse, SyncEngineStatusResponse, SyncEngineStatsResponse

router = APIRouter(prefix="/api/system", tags=["system"])
logger = logging.getLogger("local_api.system")


def _get_engine():
    """Lazy import to avoid circular dependency with main.py."""
    from ..main import engine
    return engine


@router.get("/status", response_model=SystemStatusResponse)
def get_status(request: Request):
    """Return API version, DB connectivity, and task count."""
    conn = get_db()
    try:
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM tasks")
        count = cursor.fetchone()["cnt"]
        db_connected = True
    except Exception:
        count = 0
        db_connected = False
        logger.exception("DB connectivity check failed")

    return SystemStatusResponse(
        status="ok",
        api_version="4.3.0",
        db_connected=db_connected,
        task_count=count,
    )


# ── Sync Engine Management Endpoints ───────────────────────────────────────


@router.post("/sync-engine/start")
def sync_engine_start():
    """Start the sync engine background loop."""
    engine = _get_engine()
    was_running = engine.is_running
    engine.start()
    if was_running:
        return {"status": "already_running"}
    return {"status": "started"}


@router.post("/sync-engine/stop")
def sync_engine_stop():
    """Stop the sync engine background loop."""
    engine = _get_engine()
    was_running = engine.is_running
    engine.stop()
    if was_running:
        return {"status": "stopped"}
    return {"status": "not_running"}


@router.get("/sync-engine/status", response_model=SyncEngineStatusResponse)
def sync_engine_status():
    """Return the current sync engine status."""
    engine = _get_engine()
    running = engine.is_running
    # Count pending sync_state entries
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_state WHERE sync_status = 'pending'"
        ).fetchone()
        queued_pending = row["cnt"]
    except Exception:
        queued_pending = 0
        logger.exception("Failed to query pending sync count")

    return SyncEngineStatusResponse(
        running=running,
        status="running" if running else "idle",
        scan_count=engine.scan_count,
        last_scan_at=engine.last_scan_at,
        queued_pending=queued_pending,
    )


@router.get("/sync-engine/stats", response_model=SyncEngineStatsResponse)
def sync_engine_stats():
    """Return aggregate sync engine statistics from sync_logs."""
    conn = get_db()
    try:
        total_scans_row = conn.execute("SELECT COUNT(*) as cnt FROM sync_logs").fetchone()
        total_scans = total_scans_row["cnt"]

        total_processed_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_logs WHERE sync_result != ''"
        ).fetchone()
        total_processed = total_processed_row["cnt"]

        success_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_logs WHERE sync_result = 'success'"
        ).fetchone()
        success_count = success_row["cnt"]

        failed_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_logs WHERE sync_result = 'failed'"
        ).fetchone()
        failed_count = failed_row["cnt"]
    except Exception:
        logger.exception("Failed to query sync_logs stats")
        total_scans = 0
        total_processed = 0
        success_count = 0
        failed_count = 0

    # Prevent division by zero
    if total_scans > 0:
        success_rate = round((success_count / total_scans) * 100, 2)
    else:
        success_rate = 0.0

    # Failure distribution by error_code
    try:
        dist_rows = conn.execute(
            "SELECT error_code, COUNT(*) as cnt FROM sync_logs WHERE sync_result = 'failed' GROUP BY error_code"
        ).fetchall()
        failure_distribution = {row["error_code"]: row["cnt"] for row in dist_rows}
    except Exception:
        logger.exception("Failed to query failure distribution")
        failure_distribution = {}

    return SyncEngineStatsResponse(
        total_scans=total_scans,
        total_processed=total_processed,
        success_count=success_count,
        failed_count=failed_count,
        success_rate=success_rate,
        failure_distribution=failure_distribution,
    )
