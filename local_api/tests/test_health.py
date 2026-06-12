"""Phase 38 — Tests for read-only background sync health checks."""

from __future__ import annotations

from pathlib import Path

from local_api.database import get_db, init_db, reset_db


REQUIRED_HEALTH_KEYS = {
    "launchagent_loaded",
    "launchagent_program",
    "last_exit_code",
    "preflight_ready",
    "helper_auth",
    "pending_count",
    "latest_sync_log",
    "latest_success_at",
    "latest_failure_at",
}


def test_background_health_report_contains_required_read_only_fields(monkeypatch, tmp_path):
    from local_api import health

    reset_db()
    init_db()
    try:
        conn = get_db()
        conn.execute(
            """INSERT INTO tasks (
                task_id, title, status, priority, created_channel, created_at, updated_at
            ) VALUES (?, ?, 'pending', 'P2', 'api_test', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
            ("task_health_pending", "Health pending task"),
        )
        conn.execute(
            """INSERT INTO sync_state (
                sync_id, task_id, sync_target, sync_key, sync_status, created_at, updated_at
            ) VALUES (?, ?, 'apple_calendar', ?, 'pending', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
            ("sync_health_pending", "task_health_pending", "task_health_pending:apple_calendar"),
        )
        conn.execute(
            """INSERT INTO sync_logs (
                log_id, sync_id, local_task_id, sync_target, sync_attempt, sync_result,
                created_at, triggered_by
            ) VALUES (?, ?, ?, 'apple_calendar', 1, 'success', '2026-01-01T01:00:00', 'test')""",
            ("log_health_success", "sync_health_pending", "task_health_pending"),
        )
        conn.commit()

        log_file = tmp_path / "background-sync.log"
        log_file.write_text("[2026-01-01 01:00:00] ✅ background sync completed\n", encoding="utf-8")

        monkeypatch.setattr(
            health,
            "_read_launchagent_status",
            lambda label, plist_path: {
                "loaded": True,
                "program": "/Users/vantawork/Projects/life-sync-hub/scripts/life-sync-background.sh",
                "last_exit_code": 0,
            },
        )
        monkeypatch.setattr(
            health,
            "_read_preflight_status",
            lambda: {"preflight_ready": True, "helper_auth": "authorized"},
        )

        report = health.collect_background_health(log_path=log_file)

        assert set(report) == REQUIRED_HEALTH_KEYS
        assert report["launchagent_loaded"] is True
        assert report["launchagent_program"].endswith("scripts/life-sync-background.sh")
        assert report["last_exit_code"] == 0
        assert report["preflight_ready"] is True
        assert report["helper_auth"] == "authorized"
        assert report["pending_count"] == 1
        assert report["latest_sync_log"]["sync_result"] == "success"
        assert report["latest_sync_log"]["created_at"] == "2026-01-01T01:00:00"
        assert report["latest_success_at"] == "2026-01-01T01:00:00"
        assert report["latest_failure_at"] is None
    finally:
        reset_db()


def test_background_health_cli_outputs_json(monkeypatch, capsys):
    from local_api.scripts import sync_health

    monkeypatch.setattr(
        sync_health,
        "collect_background_health",
        lambda: {key: None for key in REQUIRED_HEALTH_KEYS},
    )

    rc = sync_health.main([])
    output = capsys.readouterr().out

    assert rc == 0
    assert "launchagent_loaded" in output
    assert "latest_failure_at" in output
