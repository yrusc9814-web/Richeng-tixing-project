"""Phase 52 — Tests for interactive wrapper script."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WRAPPER_SCRIPT = SCRIPTS_DIR / "创建日程任务-交互式.command"


def test_interactive_wrapper_exists_and_is_executable():
    assert WRAPPER_SCRIPT.exists()
    assert (WRAPPER_SCRIPT.stat().st_mode & 0o111) != 0


def test_interactive_wrapper_calls_underlying_wrapper():
    content = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "./scripts/创建日程任务.command" in content
    # Should not call sync services directly
    assert "build_services" not in content
    assert "SyncService" not in content
    # Should use the wrapper args passing
    assert "${ARGS[@]}" in content or '"${ARGS[@]}"' in content

def test_interactive_wrapper_has_q_cancel():
    content = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert '="$TITLE" = "q"' in content or '="$TITLE" = "Q"' in content or 'TITLE" = "q"' in content or 'TITLE" = "Q"' in content
    assert '已取消任务创建。' in content
