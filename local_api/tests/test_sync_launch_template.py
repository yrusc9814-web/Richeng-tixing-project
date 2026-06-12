"""Phase 12 — Tests for sync trigger launch/cron templates.

Verifies that template files:
  - Exist and are well-formed
  - Contain required keys (ProgramArguments, WorkingDirectory, StartInterval, etc.)
  - Use correct command invocation (python -m local_api.scripts.sync_trigger)
  - Include dry-run examples
  - Do NOT contain execution commands (no launchctl load, no crontab install)

Run with:
    cd D:/hermes-agent
    python -m pytest local_api/tests/test_sync_launch_template.py -v
"""

from __future__ import annotations

import os
import plistlib
import re
import xml.parsers.expat

import pytest


# ── Paths ──────────────────────────────────────────────────────────────────

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "local_api", "scripts")

PLIST_PATH = os.path.join(SCRIPTS_DIR, "sync_trigger.plist.template")
BACKGROUND_PLIST_PATH = os.path.join(SCRIPTS_DIR, "life_sync_background.plist.template")
BACKGROUND_WRAPPER_PATH = os.path.join(REPO_ROOT, "scripts", "life-sync-background.sh")
CRONTAB_PATH = os.path.join(SCRIPTS_DIR, "sync_trigger.crontab.template")
SCHTASK_PATH = os.path.join(SCRIPTS_DIR, "sync_trigger_schtask.bat.template")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1 — Template files exist
# ═══════════════════════════════════════════════════════════════════════════════

class TestTemplateFilesExist:
    """All three template files must exist on disk."""

    def test_plist_template_exists(self):
        assert os.path.isfile(PLIST_PATH), f"Missing: {PLIST_PATH}"

    def test_crontab_template_exists(self):
        assert os.path.isfile(CRONTAB_PATH), f"Missing: {CRONTAB_PATH}"

    def test_schtask_template_exists(self):
        assert os.path.isfile(SCHTASK_PATH), f"Missing: {SCHTASK_PATH}"

    def test_background_plist_template_exists(self):
        assert os.path.isfile(BACKGROUND_PLIST_PATH), f"Missing: {BACKGROUND_PLIST_PATH}"

    def test_background_wrapper_exists(self):
        assert os.path.isfile(BACKGROUND_WRAPPER_PATH), f"Missing: {BACKGROUND_WRAPPER_PATH}"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2 — LaunchAgent plist validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlistTemplate:
    """LaunchAgent plist template must be valid XML and contain required keys."""

    @pytest.fixture(scope="class")
    def plist_content(self) -> str:
        return _read(PLIST_PATH)

    @pytest.fixture(scope="class")
    def plist_data(self, plist_content: str) -> dict:
        cleaned = re.sub(r"<!--.*?-->", "", plist_content, flags=re.DOTALL)
        return plistlib.loads(cleaned.encode("utf-8"))

    def test_valid_xml(self, plist_content: str):
        """Plist parses without error."""
        cleaned = re.sub(r"<!--.*?-->", "", plist_content, flags=re.DOTALL)
        try:
            plistlib.loads(cleaned.encode("utf-8"))
        except Exception as e:
            pytest.fail(f"Invalid plist XML: {e}")

    def test_label_matches(self, plist_data: dict):
        assert plist_data["Label"] == "com.localapi.sync-trigger"

    def test_program_arguments(self, plist_data: dict):
        args = plist_data["ProgramArguments"]
        assert args[0] == "{{PYTHON}}"
        assert args[1] == "-m"
        assert args[2] == "local_api.scripts.sync_trigger"
        assert args[3] == "sync-pending"
        assert args[4] == "--limit"
        assert args[5] == "10"

    def test_working_directory_placeholder(self, plist_data: dict):
        assert plist_data["WorkingDirectory"] == "{{PROJECT_ROOT}}"

    def test_standard_out_path(self, plist_data: dict):
        assert "StandardOutPath" in plist_data
        assert "sync_trigger_out.log" in plist_data["StandardOutPath"]

    def test_standard_err_path(self, plist_data: dict):
        assert "StandardErrorPath" in plist_data
        assert "sync_trigger_err.log" in plist_data["StandardErrorPath"]

    def test_start_interval(self, plist_data: dict):
        assert "StartInterval" in plist_data
        assert plist_data["StartInterval"] == 300

    def test_start_calendar_interval_commented(self, plist_content: str):
        """StartCalendarInterval is documented in comments but not active."""
        assert "StartCalendarInterval" in plist_content

    def test_keep_alive_false(self, plist_data: dict):
        assert plist_data.get("KeepAlive") is False

    def test_exit_timeout(self, plist_data: dict):
        assert plist_data["ExitTimeOut"] == 120

    def test_run_at_load_false(self, plist_data: dict):
        assert plist_data.get("RunAtLoad") is False

    def test_process_type_background(self, plist_data: dict):
        assert plist_data.get("ProcessType") == "Background"

    def test_no_launchctl_load(self, plist_content: str):
        """Template must NOT contain launchctl load as active XML."""
        # Strip ALL comments (single and multi-line)
        no_comments = re.sub(r"<!--.*?-->", "", plist_content, flags=re.DOTALL)
        # Check XML content only
        assert "launchctl load" not in no_comments.lower(), \
            "launchctl load found outside of comments"

    def test_dry_run_commented(self, plist_content: str):
        """Dry-run is mentioned at least in comments."""
        assert "dry-run" in plist_content.lower() or "DryRun" in plist_content


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2B — Phase 37 background LaunchAgent validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackgroundLaunchAgentTemplate:
    """Background LaunchAgent must call the non-interactive wrapper."""

    @pytest.fixture(scope="class")
    def plist_content(self) -> str:
        return _read(BACKGROUND_PLIST_PATH)

    @pytest.fixture(scope="class")
    def plist_data(self, plist_content: str) -> dict:
        cleaned = re.sub(r"<!--.*?-->", "", plist_content, flags=re.DOTALL)
        return plistlib.loads(cleaned.encode("utf-8"))

    def test_valid_xml(self, plist_content: str):
        cleaned = re.sub(r"<!--.*?-->", "", plist_content, flags=re.DOTALL)
        plistlib.loads(cleaned.encode("utf-8"))

    def test_label_matches(self, plist_data: dict):
        assert plist_data["Label"] == "com.vanta.lifesync.background-sync"

    def test_program_arguments_use_background_wrapper(self, plist_data: dict):
        args = plist_data["ProgramArguments"]
        assert args == ["{{PROJECT_ROOT}}/scripts/life-sync-background.sh"]

    def test_no_direct_python_sync_trigger(self, plist_content: str):
        no_comments = re.sub(r"<!--.*?-->", "", plist_content, flags=re.DOTALL)
        assert "local_api.scripts.sync_trigger" not in no_comments
        assert "{{PYTHON}}" not in no_comments

    def test_background_log_paths(self, plist_data: dict):
        assert "background-launchagent.out.log" in plist_data["StandardOutPath"]
        assert "background-launchagent.err.log" in plist_data["StandardErrorPath"]


class TestBackgroundWrapper:
    """Background wrapper must use fixed project Python and helper preflight."""

    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _read(BACKGROUND_WRAPPER_PATH)

    def test_uses_fixed_project_venv_python(self, content: str):
        assert 'PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"' in content
        assert '"$PYTHON_BIN" -m local_api.preflight' in content
        assert '"$PYTHON_BIN" -m local_api.scripts.sync_trigger sync-pending --limit 10' in content

    def test_non_interactive_no_read_prompt(self, content: str):
        assert "read -r" not in content
        assert "按回车" not in content

    def test_logs_to_background_sync_log(self, content: str):
        assert "background-sync.log" in content

    def test_uses_lock_directory(self, content: str):
        assert "background-sync.lock" in content
        assert "mkdir \"$LOCK_DIR\"" in content


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3 — crontab template validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrontabTemplate:
    """Crontab template must contain correct command and dry-run example."""

    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _read(CRONTAB_PATH)

    def test_contains_sync_pending_command(self, content: str):
        """At least one line invokes sync-pending."""
        found = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                if "sync_trigger sync-pending" in stripped:
                    found = True
                    break
        assert found, "No active crontab line with sync-pending"

    def test_contains_dry_run_example(self, content: str):
        assert "--dry-run" in content, "Missing dry-run example"

    def test_interval_5_minutes_present(self, content: str):
        assert "*/5" in content, "Missing 5-minute interval"

    def test_interval_15_minutes_present(self, content: str):
        assert "*/15" in content, "Missing 15-minute option"

    def test_placeholder_python(self, content: str):
        assert "{{PYTHON}}" in content, "Missing {{PYTHON}} placeholder"

    def test_placeholder_project_root(self, content: str):
        assert "{{PROJECT_ROOT}}" in content, "Missing {{PROJECT_ROOT}} placeholder"

    def test_log_redirection(self, content: str):
        assert "sync_trigger_cron.log" in content, "Missing cron log redirection"

    def test_no_crontab_install_command(self, content: str):
        """Template should not contain 'crontab' as an active command line."""
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                assert "crontab " not in stripped.lower(), f"Active line has crontab: {line}"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4 — Windows schtasks template validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchtaskTemplate:
    """Windows schtasks template must contain correct command and dry-run example."""

    @pytest.fixture(scope="class")
    def content(self) -> str:
        return _read(SCHTASK_PATH)

    def test_contains_sync_pending_command(self, content: str):
        assert "sync_trigger sync-pending" in content

    def test_contains_dry_run_example(self, content: str):
        assert "--dry-run" in content, "Missing dry-run example"

    def test_interval_5_minutes(self, content: str):
        assert "/mo 5" in content, "Missing 5-minute interval"

    def test_contains_python_placeholder(self, content: str):
        assert "{{PYTHON}}" in content, "Missing {{PYTHON}} placeholder"

    def test_contains_project_root_placeholder(self, content: str):
        assert "{{PROJECT_ROOT}}" in content

    def test_contains_username_placeholder(self, content: str):
        assert "{{USERNAME}}" in content, "Missing {{USERNAME}} placeholder"

    def test_contains_schtasks_create(self, content: str):
        assert "schtasks /create" in content, "Missing create command"

    def test_contains_schtasks_delete(self, content: str):
        assert "schtasks /delete" in content, "Missing delete command"

    def test_do_not_execute_warning(self, content: str):
        assert "DO NOT execute" in content


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5 — Template cross-consistency
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossConsistency:
    """All templates reference the same sync command pattern."""

    def test_all_use_python_m_flag(self):
        for path, name in [
            (PLIST_PATH, "plist"),
            (CRONTAB_PATH, "crontab"),
            (SCHTASK_PATH, "schtask"),
        ]:
            content = _read(path)
            assert "local_api.scripts.sync_trigger" in content, \
                f"{name}: missing module path"

    def test_all_include_limit_10(self):
        for path, name in [
            (PLIST_PATH, "plist"),
            (CRONTAB_PATH, "crontab"),
            (SCHTASK_PATH, "schtask"),
        ]:
            content = _read(path)
            assert "--limit" in content and "10" in content, \
                f"{name}: missing --limit 10"
