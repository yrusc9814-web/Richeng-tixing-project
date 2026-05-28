"""Phase 9 — Sync scheduler for triggering sync operations via SyncService.

Provides a minimal scheduling layer that orchestrates sync operations
through SyncService (not direct adapter/engine calls). Supports:
  - Single task sync to a target
  - Sync a task to all configured targets
  - Batch process pending sync_state records
"""

from __future__ import annotations

import logging
from typing import Optional

from ..adapters import SyncAdapter
from ..config import ALLOWED_SYNC_TARGETS
from ..services.sync_service import SyncService
from ..services.sync_state_service import get_sync_state, list_sync_states

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Minimal sync scheduler that triggers operations via SyncService.

    The scheduler is the orchestrator for automated sync operations.
    It does NOT replace SyncEngine — it provides a complementary trigger
    for programmatic or API-driven sync flows.
    """

    def __init__(self, sync_service: SyncService):
        self._svc = sync_service

    # ── Single task operations ───────────────────────────────────────

    def sync_task(self, task_id: str, sync_target: str) -> dict:
        """Sync a single task to a specific target via SyncService.

        Returns the SyncService result dict directly.
        """
        if sync_target not in ALLOWED_SYNC_TARGETS:
            return {
                "success": False,
                "task_id": task_id,
                "target": sync_target,
                "status": "skipped",
                "reason": f"Invalid sync_target: {sync_target}",
            }

        result = self._svc.run_task_sync(task_id, sync_target)
        logger.info(
            "scheduler_sync task=%s target=%s ok=%s status=%s",
            task_id, sync_target, result.get("success"), result.get("sync_status"),
        )
        return {
            "success": result.get("success", False),
            "task_id": task_id,
            "target": sync_target,
            "status": result.get("sync_status", "unknown"),
            "sync_id": result.get("sync_id"),
            "error": result.get("error_message"),
        }

    def sync_task_all_targets(self, task_id: str) -> dict:
        """Sync a task to all configured sync targets.

        Skips targets where the task already has a terminal sync_state
        (synced, failed_permanent) to avoid wasted work.

        Returns a summary dict with per-target results.
        """
        results = []
        for target in sorted(ALLOWED_SYNC_TARGETS):
            # Pre-check: skip if already at terminal state
            existing = self._get_terminal_state(task_id, target)
            if existing:
                logger.info(
                    "scheduler_skip task=%s target=%s reason=already_%s",
                    task_id, target, existing,
                )
                results.append({
                    "success": True,
                    "task_id": task_id,
                    "target": target,
                    "status": "skipped",
                    "reason": f"Already at terminal state: {existing}",
                })
                continue

            result = self.sync_task(task_id, target)
            results.append(result)

        return {
            "task_id": task_id,
            "total": len(results),
            "success_count": sum(1 for r in results if r.get("success")),
            "skip_count": sum(1 for r in results if r.get("status") == "skipped"),
            "results": results,
        }

    def sync_pending(self, limit: int = 10) -> dict:
        """Process pending sync_state records.

        Finds sync_state records with status 'pending' or 'failed'
        and triggers sync via SyncService.

        Returns summary with per-record results.
        """
        results = []
        for status_filter in ("pending", "failed"):
            records, total = list_sync_states(
                sync_status=status_filter, limit=limit,
            )
            for record in records:
                result = self.sync_task(
                    record["task_id"], record["sync_target"],
                )
                results.append(result)

        return {
            "processed": len(results),
            "success_count": sum(1 for r in results if r.get("success")),
            "results": results,
        }

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _get_terminal_state(task_id: str, sync_target: str) -> Optional[str]:
        """Check if a task+target already has a terminal sync state.

        Returns the terminal status ('synced', 'failed_permanent', etc.)
        or None if no terminal state exists.
        """
        from ..services.sync_state_service import get_sync_state_by_key

        try:
            record = get_sync_state_by_key(task_id, sync_target)
        except ValueError:
            return None

        if record is None:
            return None

        terminal_states = {"synced", "failed_permanent", "orphaned", "disabled", "deleted"}
        status = record.get("sync_status", "")
        return status if status in terminal_states else None
