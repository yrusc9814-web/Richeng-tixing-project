"""Phase 39 — CLI entry for background sync alert checks."""

from __future__ import annotations

import argparse
import sys

from local_api.alert import assess_alerts, send_alert_notifications
from local_api.health import collect_background_health


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync-alert-check",
        description="Run a read-only background sync health alert check and print the result as JSON.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        default=False,
        help="Send alert notifications (stub — no dispatch implemented).",
    )
    parser.add_argument(
        "--pending-max",
        type=int,
        default=50,
        help="Pending count threshold (default: 50).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    health = collect_background_health()
    report = assess_alerts(health, pending_max=args.pending_max)

    import json

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if args.notify and report["alerts"]:
        from local_api.alert import Alert

        alerts = [Alert(**item) for item in report["alerts"]]
        send_alert_notifications(alerts)

    return 0


if __name__ == "__main__":
    sys.exit(main())