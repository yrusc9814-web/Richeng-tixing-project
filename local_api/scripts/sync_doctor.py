"""Phase 41 — One-click read-only diagnostic command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from local_api.alert import assess_alerts
from local_api.config import LOG_DIR
from local_api.health import (
    _launchctl_print,
    _read_launchagent_plist,
    collect_background_health,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent

MAIN_AGENT_LABEL = "com.vanta.lifesync.background-sync"
ALERT_AGENT_LABEL = "com.vanta.lifesync.alert-check"

MAIN_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{MAIN_AGENT_LABEL}.plist"
ALERT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{ALERT_AGENT_LABEL}.plist"
ALERT_LOG = LOG_DIR / "sync-alert-check.log"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync-doctor",
        description="Print a comprehensive read-only diagnostic report as JSON.",
    )
    return parser


def run_cmd(cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_git_status() -> dict[str, Any]:
    branch = run_cmd(["git", "branch", "--show-current"])
    head = run_cmd(["git", "rev-parse", "HEAD"])
    status_short = run_cmd(["git", "status", "--short", "--untracked-files=all"])
    return {
        "branch": branch,
        "head": head,
        "dirty": bool(status_short),
    }


def get_agent_status(label: str, plist_path: Path) -> dict[str, Any]:
    launchctl = _launchctl_print(label)
    plist = _read_launchagent_plist(plist_path)
    
    # Extract interval from plist manually since it's not exposed by _read_launchagent_plist
    interval = None
    if plist_path.exists():
        import plistlib
        try:
            with plist_path.open("rb") as f:
                data = plistlib.load(f)
                interval = data.get("StartInterval") or data.get("RunInterval")
        except Exception:
            pass

    return {
        "loaded": launchctl.get("loaded", False),
        "program": launchctl.get("program") or plist.get("program"),
        "interval": interval,
        "last_exit_code": launchctl.get("last_exit_code"),
    }


def get_alert_log_tail(lines: int = 5) -> list[str]:
    if not ALERT_LOG.exists():
        return []
    try:
        content = ALERT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return content[-lines:]
    except OSError:
        return []


def collect_doctor() -> dict[str, Any]:
    health = collect_background_health()
    alert_report = assess_alerts(health)

    return {
        "git": get_git_status(),
        "main_agent": get_agent_status(MAIN_AGENT_LABEL, MAIN_PLIST),
        "alert_agent": get_agent_status(ALERT_AGENT_LABEL, ALERT_PLIST),
        "preflight_ready": health.get("preflight_ready"),
        "helper_auth": health.get("helper_auth"),
        "pending_count": health.get("pending_count"),
        "latest_sync_log": health.get("latest_sync_log"),
        "latest_success_at": health.get("latest_success_at"),
        "latest_failure_at": health.get("latest_failure_at"),
        "alert_status": alert_report.get("status"),
        "recent_alert_logs": get_alert_log_tail(3),
    }


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = collect_doctor()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
