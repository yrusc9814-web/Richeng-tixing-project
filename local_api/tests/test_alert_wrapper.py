"""Phase 40 — Tests for alert check wrapper script and plist."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WRAPPER_SCRIPT = SCRIPTS_DIR / "life-sync-alert-check.sh"
PLIST_FILE = SCRIPTS_DIR / "com.vanta.lifesync.alert-check.plist"


def test_wrapper_script_exists_and_is_executable():
    assert WRAPPER_SCRIPT.exists()
    assert (WRAPPER_SCRIPT.stat().st_mode & 0o111) != 0


def test_wrapper_script_uses_correct_python():
    content = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert ".venv/bin/python" in content
    assert "local_api.scripts.sync_alert_check" in content


def test_plist_has_correct_structure():
    assert PLIST_FILE.exists()
    with PLIST_FILE.open("rb") as f:
        data = plistlib.load(f)
    
    assert data["Label"] == "com.vanta.lifesync.alert-check"
    assert data["ProgramArguments"][0].endswith("scripts/life-sync-alert-check.sh")
    
    # Verify it doesn't conflict with main sync agent (300)
    interval = data.get("StartInterval")
    assert interval is not None
    assert int(interval) >= 600  # at least 10 minutes
