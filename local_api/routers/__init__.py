"""Phase 4.4 — Routers package."""
from .tasks import router as tasks_router
from .system import router as system_router
from .sync import router as sync_router
from .sync_logs import router as sync_logs_router
from .sync_routes import router as sync_routes_router

__all__ = [
    "tasks_router",
    "system_router",
    "sync_router",
    "sync_logs_router",
    "sync_routes_router",
]
