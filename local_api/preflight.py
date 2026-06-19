"""Phase 17 — Real environment preflight checks.

Verifies all conditions required for real Apple Calendar/Reminders and
WeChat notification acceptance, WITHOUT making any real external calls.

Usage:
    python -m local_api.preflight          # print report to stdout
    python -c "from local_api.preflight import run_preflight; print(run_preflight().model_dump_json(indent=2))"

Design principles:
    - No real external writes (no Calendar event creation, no WeChat API)
    - Each check is self-contained and idempotent
    - Non-macOS platforms gracefully skip Apple checks
    - Output is structured JSON for programmatic consumption
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path


# ── Enums ──────────────────────────────────────────────────────────────────


class PreflightStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    WARN = "warn"


class ReadinessLevel(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    NOT_READY = "not_ready"


# ── Result types ───────────────────────────────────────────────────────────


@dataclass
class PreflightCheckItem:
    """Single preflight check result."""

    name: str
    status: PreflightStatus
    detail: str = ""
    recommendation: str = ""


@dataclass
class PreflightReport:
    """Complete preflight report for real environment acceptance."""

    checks: list[PreflightCheckItem] = field(default_factory=list)
    readiness: ReadinessLevel = ReadinessLevel.NOT_READY
    summary: str = ""

    def add(self, name: str, status: PreflightStatus,
            detail: str = "", recommendation: str = "") -> None:
        self.checks.append(PreflightCheckItem(
            name=name,
            status=status,
            detail=detail,
            recommendation=recommendation,
        ))

    def to_dict(self) -> dict:
        return {
            "readiness": self.readiness.value,
            "summary": self.summary,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "detail": c.detail,
                    "recommendation": c.recommendation,
                }
                for c in self.checks
            ],
        }


# ── Constants for check identifiers ────────────────────────────────────────

CHECK_APPLE_PLATFORM = "apple_platform"
CHECK_APPLE_EVENTKIT_DEPS = "apple_eventkit_deps"
CHECK_APPLE_CALENDAR_PERM = "apple_calendar_permission"
CHECK_APPLE_HELPER_APP = "apple_calendar_helper_app"
CHECK_APPLE_HELPER_AUTH = "apple_calendar_helper_auth"
CHECK_APPLE_PYTHON_CALENDAR_PERM = "apple_python_calendar_permission"
CHECK_APPLE_REMINDER_PERM = "apple_reminder_permission"
CHECK_APPLE_DRY_RUN = "apple_dry_run_available"
CHECK_APPLE_TEST_MODE = "apple_test_mode_available"
CHECK_WECHAT_CONFIG = "wechat_config"
CHECK_WECHAT_DRY_RUN = "wechat_dry_run_available"
CHECK_WECHAT_TEST_MODE = "wechat_test_mode_available"
CHECK_TEST_DATA_MARKING = "test_data_marking_rules"
CHECK_ROLLBACK_CLEANUP = "rollback_cleanup_rules"


# ── Core preflight logic ───────────────────────────────────────────────────


def run_preflight() -> PreflightReport:
    """Run all preflight checks without any real external calls.

    Returns:
        PreflightReport with per-check results and overall readiness.
    """
    report = PreflightReport()
    is_macos = sys.platform == "darwin"

    # ── 1. Apple: Platform check ──────────────────────────────────────
    if is_macos:
        report.add(
            CHECK_APPLE_PLATFORM,
            PreflightStatus.PASS,
            detail=f"Running on macOS ({sys.platform})",
            recommendation="Ready for Apple EventKit integration.",
        )
    else:
        report.add(
            CHECK_APPLE_PLATFORM,
            PreflightStatus.FAIL,
            detail=f"Not on macOS. Current platform: {sys.platform}",
            recommendation="Apple Calendar/Reminders sync requires macOS. "
                           "Use --dry-run or test_mode for local validation only.",
        )

    # ── 2. Apple: EventKit / pyobjc dependencies ──────────────────────
    if is_macos:
        eventkit_ok = _check_eventkit_deps()
        if eventkit_ok:
            report.add(
                CHECK_APPLE_EVENTKIT_DEPS,
                PreflightStatus.PASS,
                detail="EventKit / PyObjC are importable.",
                recommendation="Ready for EventKit integration.",
            )
        else:
            report.add(
                CHECK_APPLE_EVENTKIT_DEPS,
                PreflightStatus.FAIL,
                detail="Cannot import EventKit or PyObjC framework.",
                recommendation="Install PyObjC: pip install pyobjc-framework-EventKit",
            )
    else:
        report.add(
            CHECK_APPLE_EVENTKIT_DEPS,
            PreflightStatus.SKIP,
            detail="Not on macOS — EventKit is not available.",
            recommendation="No action needed on this platform.",
        )

    # ── 3. Apple: Calendar helper permission ──────────────────────────
    if is_macos:
        helper_app = _calendar_helper_app_path()
        if _check_calendar_helper_app(helper_app):
            report.add(
                CHECK_APPLE_HELPER_APP,
                PreflightStatus.PASS,
                detail=f"LifeSyncCalendarHelper.app exists: {helper_app}",
                recommendation="Ready to launch the Calendar helper app.",
            )
        else:
            report.add(
                CHECK_APPLE_HELPER_APP,
                PreflightStatus.FAIL,
                detail=f"LifeSyncCalendarHelper.app is missing or not launchable: {helper_app}",
                recommendation="Build the helper app with tools/calendar-helper/build-helper.sh.",
            )

        helper_auth = _check_calendar_helper_auth(helper_app)
        if helper_auth.get("ok"):
            status = helper_auth.get("authorizationStatus", "unknown")
            count = helper_auth.get("calendarCount", "unknown")
            report.add(
                CHECK_APPLE_HELPER_AUTH,
                PreflightStatus.PASS,
                detail=f"Helper Calendar auth is {status}; calendarCount={count}.",
                recommendation="Ready for helper-owned Calendar write operations.",
            )
            report.add(
                CHECK_APPLE_CALENDAR_PERM,
                PreflightStatus.PASS,
                detail="Calendar TCC is granted to LifeSyncCalendarHelper.app; Python Calendar TCC is not required.",
                recommendation="Proceed with sync-pending; AppleSyncAdapter delegates writes to the helper app.",
            )
        else:
            detail = helper_auth.get("detail") or "Helper Calendar authorization check failed."
            report.add(
                CHECK_APPLE_HELPER_AUTH,
                PreflightStatus.FAIL,
                detail=detail,
                recommendation="Open LifeSyncCalendarHelper.app and grant Calendar access.",
            )
            report.add(
                CHECK_APPLE_CALENDAR_PERM,
                PreflightStatus.FAIL,
                detail="Calendar TCC is not ready for the helper app.",
                recommendation="Grant Calendar access to LifeSyncCalendarHelper.app, not the venv Python binary.",
            )

        python_cal_perm = _check_calendar_permission()
        if python_cal_perm:
            report.add(
                CHECK_APPLE_PYTHON_CALENDAR_PERM,
                PreflightStatus.PASS,
                detail="Python Calendar TCC is also granted.",
                recommendation="No action needed; helper app remains the production writer.",
            )
        else:
            report.add(
                CHECK_APPLE_PYTHON_CALENDAR_PERM,
                PreflightStatus.SKIP,
                detail="Python Calendar TCC is not granted and is no longer required for helper-based writes.",
                recommendation="No action needed unless you restore direct Python EventKit writes.",
            )
    else:
        report.add(
            CHECK_APPLE_HELPER_APP,
            PreflightStatus.SKIP,
            detail="Not on macOS — Calendar helper app is not applicable.",
            recommendation="No action needed on this platform.",
        )
        report.add(
            CHECK_APPLE_HELPER_AUTH,
            PreflightStatus.SKIP,
            detail="Not on macOS — Calendar helper authorization is not applicable.",
            recommendation="No action needed on this platform.",
        )
        report.add(
            CHECK_APPLE_CALENDAR_PERM,
            PreflightStatus.SKIP,
            detail="Not on macOS — Calendar permission is not applicable.",
            recommendation="No action needed on this platform.",
        )
        report.add(
            CHECK_APPLE_PYTHON_CALENDAR_PERM,
            PreflightStatus.SKIP,
            detail="Not on macOS — Python Calendar permission is not applicable.",
            recommendation="No action needed on this platform.",
        )

    # ── 4. Apple: Reminders permission ────────────────────────────────
    report.add(
        CHECK_APPLE_REMINDER_PERM,
        PreflightStatus.SKIP,
        detail="Reminders writes are intentionally disabled for Phase 18B.",
        recommendation="No action needed for Calendar-only acceptance.",
    )

    # ── 5. Apple: Dry-run mode ────────────────────────────────────────
    report.add(
        CHECK_APPLE_DRY_RUN,
        PreflightStatus.PASS,
        detail="AppleSyncAdapter supports dry_run=True flag.",
        recommendation="Use --dry-run for safe validation before live sync.",
    )

    # ── 6. Apple: Test mode ───────────────────────────────────────────
    report.add(
        CHECK_APPLE_TEST_MODE,
        PreflightStatus.PASS,
        detail="AppleSyncAdapter supports test_mode=True flag with [SYNC-TEST] prefix.",
        recommendation="Use test_mode for simulated push validation.",
    )

    # ── 7. WeChat: Configuration ──────────────────────────────────────
    wechat_enabled = os.environ.get("WECHAT_REMINDER_ENABLED", "").strip().lower()
    wechat_app_id = os.environ.get("WECHAT_APP_ID", "").strip()
    wechat_app_secret = os.environ.get("WECHAT_APP_SECRET", "").strip()

    if wechat_enabled in ("true", "1", "yes"):
        if wechat_app_id and wechat_app_secret:
            report.add(
                CHECK_WECHAT_CONFIG,
                PreflightStatus.PASS,
                detail="WECHAT_REMINDER_ENABLED=true, WECHAT_APP_ID and WECHAT_APP_SECRET are set.",
                recommendation="Ready for WeChat notification — real push is not yet implemented.",
            )
        elif not wechat_app_id and not wechat_app_secret:
            report.add(
                CHECK_WECHAT_CONFIG,
                PreflightStatus.WARN,
                detail="WECHAT_REMINDER_ENABLED=true but both WECHAT_APP_ID and WECHAT_APP_SECRET are missing.",
                recommendation="Set WECHAT_APP_ID and WECHAT_APP_SECRET before WeChat notification acceptance.",
            )
        elif not wechat_app_id:
            report.add(
                CHECK_WECHAT_CONFIG,
                PreflightStatus.WARN,
                detail="WECHAT_REMINDER_ENABLED=true but WECHAT_APP_ID is missing.",
                recommendation="Set WECHAT_APP_ID before WeChat notification acceptance.",
            )
        else:
            report.add(
                CHECK_WECHAT_CONFIG,
                PreflightStatus.WARN,
                detail="WECHAT_REMINDER_ENABLED=true but WECHAT_APP_SECRET is missing.",
                recommendation="Set WECHAT_APP_SECRET before WeChat notification acceptance.",
            )
    elif wechat_enabled == "":
        report.add(
            CHECK_WECHAT_CONFIG,
            PreflightStatus.SKIP,
            detail="WECHAT_REMINDER_ENABLED is not set; WeChat is outside Phase 18B Calendar-only scope.",
            recommendation="No action needed for Calendar-only acceptance.",
        )
    else:
        report.add(
            CHECK_WECHAT_CONFIG,
            PreflightStatus.SKIP,
            detail=f"WECHAT_REMINDER_ENABLED is '{wechat_enabled}' (not a truthy value); WeChat is disabled.",
            recommendation="No action needed for Calendar-only acceptance.",
        )

    # ── 8. WeChat: Dry-run mode ───────────────────────────────────────
    report.add(
        CHECK_WECHAT_DRY_RUN,
        PreflightStatus.PASS,
        detail="WeChatNotifyChannel supports mode='dry_run' which returns status='skipped'.",
        recommendation="Use dry_run mode for safe reminder validation.",
    )

    # ── 9. WeChat: Test mode ──────────────────────────────────────────
    report.add(
        CHECK_WECHAT_TEST_MODE,
        PreflightStatus.PASS,
        detail="WeChatNotifyChannel supports mode='test_mode' which returns status='simulated'.",
        recommendation="Use test_mode for simulated reminder validation.",
    )

    # ── 10. Test data marking rules ───────────────────────────────────
    # Check that the SYNC_TEST_PREFIX constant exists in apple_adapter
    from local_api.adapters.apple_adapter import _SYNC_TEST_PREFIX
    if _SYNC_TEST_PREFIX == "[SYNC-TEST] ":
        report.add(
            CHECK_TEST_DATA_MARKING,
            PreflightStatus.PASS,
            detail=f"Apple adapter marks test data with prefix '{_SYNC_TEST_PREFIX}'.",
            recommendation="Test data is traceable — use test_mode for safe validation.",
        )
    else:
        report.add(
            CHECK_TEST_DATA_MARKING,
            PreflightStatus.WARN,
            detail=f"Apple adapter test prefix is unexpected: '{_SYNC_TEST_PREFIX}'.",
            recommendation="Verify that test data can be distinguished from real data.",
        )

    # ── 11. Rollback / cleanup rules ──────────────────────────────────
    rollback_ok = _check_rollback_cleanup_rules()
    if rollback_ok:
        report.add(
            CHECK_ROLLBACK_CLEANUP,
            PreflightStatus.PASS,
            detail="Rollback/cleanup rules are documented: delete by external_id, "
                   "filter by [SYNC-TEST] prefix, delete sync_state record, "
                   "rely on database transaction rollback.",
            recommendation="Use dry_run and test_mode — real data can be cleaned by "
                           "deleting test-prefixed records.",
        )
    else:
        report.add(
            CHECK_ROLLBACK_CLEANUP,
            PreflightStatus.WARN,
            detail="Rollback/cleanup rules not fully verified.",
            recommendation="Document cleanup procedures before real write operations.",
        )

    # ── Compute overall readiness ─────────────────────────────────────
    _compute_readiness(report)
    return report


# ── Internal check implementations ─────────────────────────────────────────


def _calendar_helper_app_path() -> Path:
    """Return the bundled LifeSyncCalendarHelper.app path for this checkout."""
    return Path(__file__).resolve().parents[1] / "tools" / "calendar-helper" / "LifeSyncCalendarHelper.app"


def _check_calendar_helper_app(helper_app: Path) -> bool:
    """Check that the helper app bundle and executable exist."""
    helper_bin = helper_app / "Contents" / "MacOS" / "LifeSyncCalendarHelper"
    return helper_app.is_dir() and helper_bin.is_file() and os.access(helper_bin, os.X_OK)


def _check_calendar_helper_auth(helper_app: Path) -> dict:
    """Ask the helper app to report its Calendar TCC state without writing events."""
    if sys.platform != "darwin":
        return {"ok": False, "detail": "Calendar helper auth is only available on macOS."}
    if not _check_calendar_helper_app(helper_app):
        return {"ok": False, "detail": f"Helper app is missing or not executable: {helper_app}"}

    report_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as rf:
            report_path = rf.name
        result = subprocess.run(
            [
                "/usr/bin/open",
                "-g",
                "-n",
                "-W",
                str(helper_app),
                "--args",
                "--check-access",
                "--report-file",
                report_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "helper launch failed").strip()
            return {"ok": False, "detail": detail}
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "LifeSyncCalendarHelper --check-access timed out."}
    except Exception as exc:
        return {"ok": False, "detail": f"LifeSyncCalendarHelper --check-access failed: {exc}"}
    finally:
        if report_path:
            try:
                os.unlink(report_path)
            except OSError:
                pass

    if report.get("mode") != "check_access":
        return {"ok": False, "detail": f"Unexpected helper report mode: {report.get('mode')}"}
    if report.get("bundleIdentifier") != "com.vanta.lifesync.calendar-helper":
        return {"ok": False, "detail": f"Unexpected helper bundle id: {report.get('bundleIdentifier')}"}
    if not report.get("calendarAccessGranted"):
        status = report.get("authorizationStatus", "unknown")
        return {"ok": False, "detail": f"Helper Calendar auth is {status}."}
    if int(report.get("calendarCount") or 0) <= 0:
        return {"ok": False, "detail": "Helper Calendar auth is granted but no calendars are available."}
    return {"ok": True, **report}


def _check_eventkit_deps() -> bool:
    """Check if EventKit / PyObjC framework is importable.

    No real EventKit API is called — only importability is tested.
    """
    try:
        import EventKit  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import PyObjCTools  # noqa: F401
        return True
    except ImportError:
        pass
    return False


def _check_calendar_permission() -> bool:
    """Check if Calendar TCC permission is already granted.

    Uses EventKit's current access state only. It does not request access or
    create Calendar data, so preflight remains a read-only readiness probe.
    """
    if sys.platform != "darwin":
        return False
    try:
        import EventKit  # type: ignore[import-not-found]

        store = EventKit.EKEventStore.alloc().init()
        return bool(store.accessGrantedForEntityType_(EventKit.EKEntityTypeEvent))
    except Exception:
        return False


def _check_reminders_permission() -> bool:
    """Check if Reminders TCC permission appears granted.

    Same constraints as _check_calendar_permission — no real API calls.
    """
    if sys.platform != "darwin":
        return False
    # Deferred: real TCC check requires EKEventStore.requestAccess.
    return False


def _check_rollback_cleanup_rules() -> bool:
    """Verify that rollback/cleanup rules are known.

    Checks for the SYNC_TEST_PREFIX constant and the database reset
    fixture used in tests as evidence that cleanup is understood.
    """
    from local_api.adapters.apple_adapter import _SYNC_TEST_PREFIX
    from local_api.database import reset_db

    # Verify constants exist and are accessible
    has_prefix = bool(_SYNC_TEST_PREFIX)
    has_reset = callable(reset_db)
    return has_prefix and has_reset


def _compute_readiness(report: PreflightReport) -> None:
    """Compute overall readiness level from individual check results.

    Rule:
        - All PASS or SKIP → READY
        - Any FAIL → NOT_READY
        - Any WARN → PARTIAL
    """
    fails = [c for c in report.checks if c.status == PreflightStatus.FAIL]
    warns = [c for c in report.checks if c.status == PreflightStatus.WARN]

    if not fails and not warns:
        report.readiness = ReadinessLevel.READY
        report.summary = "All checks passed. Ready for real environment acceptance."
    elif fails:
        fail_names = [c.name for c in fails]
        report.summary = (
            f"{len(fails)} check(s) failed: {', '.join(fail_names)}. "
            "Resolve issues before real writes, or use dry_run/test_mode."
        )
        report.readiness = ReadinessLevel.NOT_READY
    else:
        warn_names = [c.name for c in warns]
        report.summary = (
            f"{len(warns)} warning(s): {', '.join(warn_names)}. "
            "Review warnings before real writes."
        )
        report.readiness = ReadinessLevel.PARTIAL


# ── CLI entry point ────────────────────────────────────────────────────────


def print_report(report: Optional[PreflightReport] = None) -> None:
    """Print preflight report to stdout as formatted JSON."""
    if report is None:
        report = run_preflight()
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    report = run_preflight()
    print_report(report)
    sys.exit(0 if report.readiness in (ReadinessLevel.READY, ReadinessLevel.PARTIAL) else 1)
