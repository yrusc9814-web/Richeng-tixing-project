"""Phase 38 — Read-only background sync health checks."""

from __future__ import annotations

import json
import plistlib
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .config import DB_PATH, LOG_DIR
from .preflight import CHECK_APPLE_HELPER_AUTH, PreflightStatus, ReadinessLevel, run_preflight

LAUNCHAGENT_LABEL = "com.vanta.lifesync.background-sync"
DEFAULT_LAUNCHAGENT_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHAGENT_LABEL}.plist"
)
DEFAULT_BACKGROUND_LOG = LOG_DIR / "background-sync.log"


def collect_background_health(
    *,
    label: str = LAUNCHAGENT_LABEL,
    plist_path: Path | str = DEFAULT_LAUNCHAGENT_PLIST,
    db_path: Path | str = DB_PATH,
    log_path: Path | str = DEFAULT_BACKGROUND_LOG,
) -> dict[str, Any]:
    """Collect a read-only health snapshot for the background sync path."""
    launchagent = _read_launchagent_status(label, Path(plist_path))
    preflight = _read_preflight_status()
    db_health = _read_sync_db_health(Path(db_path))

    return {
        "launchagent_loaded": launchagent["loaded"],
        "launchagent_program": launchagent["program"],
        "last_exit_code": launchagent["last_exit_code"],
        "preflight_ready": preflight["preflight_ready"],
        "helper_auth": preflight["helper_auth"],
        "pending_count": db_health["pending_count"],
        "latest_sync_log": db_health["latest_sync_log"] or _read_latest_background_log(Path(log_path)),
        "latest_success_at": db_health["latest_success_at"],
        "latest_failure_at": db_health["latest_failure_at"],
    }


def _read_launchagent_status(label: str, plist_path: Path) -> dict[str, Any]:
    plist = _read_launchagent_plist(plist_path)
    launchctl = _launchctl_print(label)
    return {
        "loaded": launchctl.get("loaded", False),
        "program": launchctl.get("program") or plist.get("program"),
        "last_exit_code": launchctl.get("last_exit_code"),
    }


def _read_launchagent_plist(plist_path: Path) -> dict[str, Any]:
    if not plist_path.exists():
        return {"program": None}
    try:
        with plist_path.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception:
        return {"program": None}

    args = data.get("ProgramArguments") or []
    program = args[0] if args else data.get("Program")
    return {"program": program}


def _launchctl_print(label: str) -> dict[str, Any]:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{_uid()}/{label}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return {"loaded": False, "program": None, "last_exit_code": None}

    output = result.stdout
    program = _match_value(output, r"\bprogram = (.+)")
    last_exit = _match_int(output, r"\blast exit code = (-?\d+)")
    return {"loaded": True, "program": program, "last_exit_code": last_exit}


def _uid() -> str:
    result = subprocess.run(["id", "-u"], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _read_preflight_status() -> dict[str, Any]:
    report = run_preflight()
    helper = next((item for item in report.checks if item.name == CHECK_APPLE_HELPER_AUTH), None)
    helper_auth = None
    if helper is not None:
        if helper.status == PreflightStatus.PASS:
            helper_auth = _extract_helper_auth_status(helper.detail) or "authorized"
        else:
            helper_auth = helper.status.value

    return {
        "preflight_ready": report.readiness == ReadinessLevel.READY,
        "helper_auth": helper_auth,
    }


def _read_sync_db_health(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "pending_count": 0,
            "latest_sync_log": None,
            "latest_success_at": None,
            "latest_failure_at": None,
        }

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM sync_state WHERE sync_status IN ('pending', 'stale')"
        ).fetchone()[0]
        latest_log = conn.execute(
            """SELECT log_id, sync_id, local_task_id, sync_target, sync_attempt,
                      sync_result, error_code, error_message, triggered_by, created_at
                 FROM sync_logs
                ORDER BY created_at DESC
                LIMIT 1"""
        ).fetchone()
        latest_success_at = conn.execute(
            "SELECT MAX(created_at) FROM sync_logs WHERE sync_result = 'success'"
        ).fetchone()[0]
        latest_failure_at = conn.execute(
            "SELECT MAX(created_at) FROM sync_logs WHERE sync_result = 'failed'"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "pending_count": int(pending_count or 0),
        "latest_sync_log": dict(latest_log) if latest_log else None,
        "latest_success_at": latest_success_at,
        "latest_failure_at": latest_failure_at,
    }


def _read_latest_background_log(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.strip():
            return line
    return None


def _match_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(1).strip()


def _match_int(text: str, pattern: str) -> int | None:
    value = _match_value(text, pattern)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _extract_helper_auth_status(detail: str) -> str | None:
    match = re.search(r"Helper Calendar auth is ([^;\.]+)", detail)
    if not match:
        return None
    return match.group(1).strip()


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
