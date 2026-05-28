"""Phase 4.4 — FastAPI local API entry point.

Start with:
    cd local_api
    uvicorn main:app --host 127.0.0.1 --port 8100
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .config import API_HOST, API_PORT, ACCESS_LOG_PATH
from .database import init_db, close_db
from .middleware import AuthAndValidationMiddleware
from .routers import (
    tasks_router,
    system_router,
    sync_router,
    sync_logs_router,
    sync_routes_router,
)
from .services.sync_service import SyncService
from .scheduler.sync_scheduler import SyncScheduler
from .sync_engine import SyncEngine

from .adapters.apple_adapter import MockAppleAdapter

# ── Logging ────────────────────────────────────────────────────────────────

logger = logging.getLogger("local_api")
logger.setLevel(logging.INFO)

# File handler for access log
fh = logging.FileHandler(str(ACCESS_LOG_PATH), encoding="utf-8")
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(fh)

# Also log to console for visibility during dev
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(ch)


# ── Engine instance (module-level) ─────────────────────────────────────────

_adapters = [MockAppleAdapter()] if config.ADAPTER_ENABLED else []
engine = SyncEngine(adapters=_adapters)
sync_service = SyncService(adapters=_adapters)
scheduler = SyncScheduler(sync_service=sync_service)


# ── Lifespan ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("local_api starting — db=%s", str(init_db))
    init_db()

    app.state.engine = engine
    app.state.sync_service = sync_service
    app.state.scheduler = scheduler

    if config.SYNC_ENGINE_AUTO_START:
        logger.info("Auto-starting sync engine")
        engine.start()
    else:
        logger.info("Sync engine auto-start disabled (SYNC_ENGINE_AUTO_START=False)")

    yield

    logger.info("Shutting down sync engine")
    engine.stop()
    logger.info("local_api shutting down")
    close_db()


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Hermes Agent Local API",
    version="4.4.0",
    description="Phase 4.4 — Local task management API with SQLite sync backend",
    lifespan=lifespan,
)

# Attach the auth + validation middleware
app.add_middleware(AuthAndValidationMiddleware)

# Register routers
app.include_router(tasks_router)
app.include_router(system_router)
app.include_router(sync_router)
app.include_router(sync_logs_router)
app.include_router(sync_routes_router)


# ── Health check without auth (for convenience during dev) ─────────────────


@app.get("/health")
def health_check():
    """Quick liveness probe without auth."""
    return {"status": "ok", "version": "4.4.0"}
