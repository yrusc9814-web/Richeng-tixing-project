"""Phase 39 — Alert / notification layer for background sync health."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Any

from .health import collect_background_health, to_json


@dataclass
class Alert:
    title: str
    severity: str  # "warn" | "critical"
    detail: str
    rule: str = ""


DEFAULT_PENDING_MAX = 50


def assess_alerts(
    health: dict[str, Any],
    *,
    pending_max: int = DEFAULT_PENDING_MAX,
) -> dict[str, Any]:
    alerts: list[Alert] = []

    # 1. LaunchAgent not loaded
    if not health.get("launchagent_loaded"):
        alerts.append(
            Alert(
                title="LaunchAgent not loaded",
                severity="critical",
                detail="com.vanta.lifesync.background-sync is not registered or not running.",
                rule="launchagent_loaded==False",
            )
        )

    # 2. Last exit code != 0
    last_exit = health.get("last_exit_code")
    if last_exit is not None and last_exit != 0:
        alerts.append(
            Alert(
                title=f"LaunchAgent exit code: {last_exit}",
                severity="critical",
                detail=f"Last exit code is {last_exit} instead of 0.",
                rule="last_exit_code!=0",
            )
        )

    # 3. Preflight not ready
    if not health.get("preflight_ready"):
        alerts.append(
            Alert(
                title="Preflight not ready",
                severity="critical",
                detail="Preflight checks failed; background sync may not work.",
                rule="preflight_ready==False",
            )
        )

    # 4. Helper auth not authorized / fullAccess
    helper = health.get("helper_auth", "")
    if not _is_authorized(helper):
        alerts.append(
            Alert(
                title=f"Helper TCC: {helper}",
                severity="critical",
                detail="LifeSyncCalendarHelper does not have Calendar access.",
                rule="helper_auth not in authorized/fullAccess",
            )
        )

    # 5. Pending count beyond threshold
    pending = health.get("pending_count", 0)
    if pending > pending_max:
        alerts.append(
            Alert(
                title=f"Pending count {pending} exceeds threshold {pending_max}",
                severity="warn",
                detail=f"{pending} sync_state records are pending or stale.",
                rule=f"pending_count>{pending_max}",
            )
        )

    # 6. Latest failure newer than latest success
    fail_at = health.get("latest_failure_at")
    success_at = health.get("latest_success_at")
    if fail_at and success_at and fail_at > success_at:
        alerts.append(
            Alert(
                title="Latest failure newer than latest success",
                severity="critical",
                detail=f"Success: {success_at}, Failure: {fail_at}.",
                rule="latest_failure_at>latest_success_at",
            )
        )

    # 7. Latest sync log result is failure/error
    latest_log = health.get("latest_sync_log")
    if isinstance(latest_log, dict):
        result = latest_log.get("sync_result", "")
        if result in ("failed", "error"):
            alerts.append(
                Alert(
                    title=f"Latest sync log result: {result}",
                    severity="critical",
                    detail=latest_log.get("error_message", ""),
                    rule=f"latest_sync_log.sync_result=={result}",
                )
            )

    # ── final status ──
    if any(a.severity == "critical" for a in alerts):
        status = "critical"
    elif alerts:
        status = "warn"
    else:
        status = "ok"

    return {
        "status": status,
        "alerts": [asdict(a) for a in alerts],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "health": health,
        "thresholds": {"pending_max": pending_max},
    }


def _is_authorized(auth_value: str) -> bool:
    if not auth_value:
        return False
    return auth_value.lower() in {"authorized", "fullaccess"}


def send_alert_notifications(alerts: list[Alert]) -> bool:
    return True  # stub — no actual dispatch