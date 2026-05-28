"""Phase 11 — CLI sync trigger for SyncScheduler.

Minimal external entry point that allows cron, LaunchAgent, or shell scripts
to trigger sync operations through SyncScheduler without running the FastAPI
server. Never bypasses SyncService — always routes through the full service
layer.

Phase 13 — Safety layer:
  - Dry-run mode is clearly labeled with [DRY-RUN] prefix and "mode":"dry-run"
  - Live mode without explicit target emits a safety warning
  - Structured output always includes task_id, target, status, error
  - Sync skips (terminal state detection) are clearly shown
  - All failure paths return non-zero exit code

Usage:
    python -m local_api.scripts.sync_trigger sync-task <task-id> [--target TARGET] [--dry-run]
    python -m local_api.scripts.sync_trigger sync-pending [--limit N] [--dry-run]

Examples:
    # Dry-run: validate without external sync
    python -m local_api.scripts.sync_trigger sync-task task_abc123 --dry-run

    # Sync a task to a specific target
    python -m local_api.scripts.sync_trigger sync-task task_abc123 --target apple_calendar

    # Sync a task to all targets (safe default adapter)
    python -m local_api.scripts.sync_trigger sync-task task_abc123

    # Process up to 5 pending sync records
    python -m local_api.scripts.sync_trigger sync-pending --limit 5
"""

from __future__ import annotations

import argparse
import sys
import os
import json
from typing import Optional

# Ensure project root is on the path (same pattern as scripts/reset_test_db.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from local_api.adapters import AdapterResult, SyncAdapter
from local_api.config import ALLOWED_SYNC_TARGETS
from local_api.services.sync_service import SyncService
from local_api.scheduler.sync_scheduler import SyncScheduler


# ── Dry-run adapter ──────────────────────────────────────────────────────


class DryRunAdapter(SyncAdapter):
    """Adapter that records sync intent without external side effects.

    Used for --dry-run mode and as default adapter. Matches any sync target
    via prefix (apple_* → apple_calendar) so the routing layer is exercised.

    Phase 13: mode field in JSON output distinguishes dry-run from live mode.
    Stderr banner ([DRY-RUN] / [LIVE]) provides clear visual cue.
    """

    def __init__(self, target: str = "apple_calendar"):
        self._target = target

    @property
    def target_name(self) -> str:
        return self._target

    def validate_config(self) -> tuple[bool, Optional[str]]:
        return (True, None)

    def push(self, task_data: dict, sync_state: dict) -> AdapterResult:
        return AdapterResult(
            success=True,
            external_id="dry_run_noop",
            sync_result="skipped",
            sync_attempt=1,
        )

    def pull(self, external_id: str) -> Optional[dict]:
        return None


# ── Factory ──────────────────────────────────────────────────────────────


def build_services(dry_run: bool = False, explicit_target: Optional[str] = None) -> tuple[SyncService, SyncScheduler]:
    """Build SyncService and SyncScheduler for CLI use.

    Args:
        dry_run: If True, use a single DryRunAdapter.
                  If False, use one DryRunAdapter per ALLOWED_SYNC_TARGETS
                  (safe default — no real adapters are wired yet).
        explicit_target: If provided, only create adapter for this target
                         (currently still DryRunAdapter).

    Returns:
        (sync_service, sync_scheduler) tuple ready for use.
    """
    if dry_run:
        adapters = [DryRunAdapter()]
    elif explicit_target:
        adapters = [DryRunAdapter(target=explicit_target)]
    else:
        adapters = _default_adapters()
    service = SyncService(adapters=adapters)
    scheduler = SyncScheduler(sync_service=service)
    return service, scheduler


def _default_adapters():
    """Return configured adapters (safe default: all DryRunAdapter)."""
    return [DryRunAdapter(target=t) for t in ALLOWED_SYNC_TARGETS]


# ── Output helpers ───────────────────────────────────────────────────────


def _emit_banner(mode: str, task_id: str, target: Optional[str] = None) -> None:
    """Print a visible safety banner to stdout before JSON output.

    Phase 13: banner clearly labels dry-run vs live mode and surfaces
    safety warnings for live mode without explicit target.
    """
    if mode == "dry-run":
        print(f"[DRY-RUN] Dry-run mode — no external sync will be performed.", file=sys.stderr)
    elif target:
        print(f"[LIVE] Syncing task {task_id} to target: {target}", file=sys.stderr)
    else:
        print(
            f"[LIVE] Syncing task {task_id} to ALL targets (dry-run adapter — safe default). "
            f"Use --target to specify a single target.",
            file=sys.stderr,
        )


def _ensure_fields(result: dict) -> dict:
    """Ensure every result dict has the required safety fields.

    Phase 13: all output rows must include task_id, target, status, error.
    """
    safe = dict(result)
    safe.setdefault("task_id", None)
    safe.setdefault("target", None)
    safe.setdefault("status", "unknown")
    safe.setdefault("error", None)
    return safe


def _ensure_fields_in_results(data: dict) -> dict:
    """Recursively ensure safety fields in nested results arrays."""
    safe = dict(data)
    if "results" in safe and isinstance(safe["results"], list):
        safe["results"] = [_ensure_fields(r) for r in safe["results"]]
    return safe


def _print_result(command: str, result: dict, mode: str = "live") -> None:
    """Print result as formatted JSON to stdout.

    Phase 13: includes mode field for clear dry-run vs live distinction.
    """
    safe_result = _ensure_fields_in_results(result)
    output = {
        "command": command,
        "mode": mode,
        "result": safe_result,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


# ── Command handlers ──────────────────────────────────────────────────────


def handle_sync_task(args: argparse.Namespace) -> int:
    """Execute sync-task command."""
    mode = "dry-run" if args.dry_run else "live"
    service, scheduler = build_services(
        dry_run=args.dry_run,
        explicit_target=args.target,
    )

    task_id = args.task_id
    target = args.target

    # Safety banner
    _emit_banner(mode, task_id, target)

    # Initialize DB before accessing it
    from local_api.database import init_db, get_db

    init_db()

    if target:
        result = scheduler.sync_task(task_id, target)
        _print_result("sync-task", result, mode)
        return 0 if result.get("success") else 1
    else:
        result = scheduler.sync_task_all_targets(task_id)
        _print_result("sync-task", result, mode)
        # All-targets: success if at least one target produced a result
        success_count = result.get("success_count", 0)
        return 0 if success_count > 0 else 1


def handle_sync_pending(args: argparse.Namespace) -> int:
    """Execute sync-pending command."""
    mode = "dry-run" if args.dry_run else "live"
    service, scheduler = build_services(
        dry_run=args.dry_run,
        explicit_target=None,
    )

    # Safety banner
    print(f"[{mode.upper()}] Processing pending sync records.", file=sys.stderr)

    # Initialize DB
    from local_api.database import init_db

    init_db()

    result = scheduler.sync_pending(limit=args.limit)
    _print_result("sync-pending", result, mode)
    processed = result.get("processed", 0)
    success_count = result.get("success_count", 0)
    return 0 if processed == 0 or success_count > 0 else 1


# ── CLI entry point ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync trigger — call SyncScheduler from CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # sync-task
    task_parser = subparsers.add_parser(
        "sync-task",
        help="Sync a single task to one or all targets",
        description="Sync a task to one or all configured sync targets.",
    )
    task_parser.add_argument("task_id", help="ID of the task to sync")
    task_parser.add_argument(
        "--target",
        default=None,
        help="Specific sync target (default: all targets)",
    )
    task_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate pipeline without external sync",
    )

    # sync-pending
    pending_parser = subparsers.add_parser(
        "sync-pending",
        help="Process pending sync_state records",
        description="Find and process pending or failed sync_state records.",
    )
    pending_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max pending records to process (default: 10)",
    )
    pending_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate pipeline without external sync",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "sync-task": handle_sync_task,
        "sync-pending": handle_sync_pending,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
