"""Phase 46 — Tests for create task wrapper script."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WRAPPER_SCRIPT = SCRIPTS_DIR / "life-sync-create-task.command"


def test_wrapper_script_exists_and_is_executable():
    assert WRAPPER_SCRIPT.exists()
    assert (WRAPPER_SCRIPT.stat().st_mode & 0o111) != 0


def test_wrapper_script_uses_correct_python_and_passes_args():
    content = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert ".venv/bin/python" in content
    assert "local_api.scripts.create_task" in content
    assert "\"$@\"" in content

def test_wrapper_script_has_interactive_fallback():
    content = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "if [ $# -eq 0 ]; then" in content
    assert "read -p" in content
    assert 'ARGS=("--title" "$TITLE"' in content
    assert 'exec "$PYTHON" -m local_api.scripts.create_task "$@"' in content


