"""Phase 39 — Tests for background sync alert/health status checks."""

from __future__ import annotations

from local_api.health import to_json


REQUIRED_ALERT_KEYS = {"status", "alerts", "checked_at", "health", "thresholds"}

# ── Fake helpers ─────────────────────────────────────────────────────────────

def _make_healthy_report():
    return {
        "launchagent_loaded": True,
        "launchagent_program": "/Users/vantawork/Projects/life-sync-hub/scripts/life-sync-background.sh",
        "last_exit_code": 0,
        "preflight_ready": True,
        "helper_auth": "authorized",
        "pending_count": 0,
        "latest_sync_log": {
            "sync_result": "success",
            "created_at": "2026-01-01T01:00:00",
        },
        "latest_success_at": "2026-01-01T01:00:00",
        "latest_failure_at": None,
    }


def _report(*, status="ok", alerts=None):
    from local_api.alert import Alert
    return {
        "status": status,
        "alerts": alerts or [],
        "checked_at": "2026-01-01T01:00:00",
        "health": _make_healthy_report(),
        "thresholds": {"pending_max": 50},
    }


# ── Tests ────────────────────────────────────────────────────────────────────


def test_alert_check_all_healthy():
    from local_api.alert import assess_alerts

    report = assess_alerts(_make_healthy_report(), pending_max=50)
    assert set(report) == REQUIRED_ALERT_KEYS
    assert report["status"] == "ok"
    assert report["alerts"] == []


def test_alert_check_helper_auth_critical():
    from local_api.alert import assess_alerts

    health = _make_healthy_report()
    health["helper_auth"] = "notDetermined"
    report = assess_alerts(health, pending_max=50)

    assert report["status"] == "critical"
    assert any("TCC" in a["title"] or "helper" in a["title"].lower() for a in report["alerts"])


def test_alert_check_launchagent_not_loaded_critical():
    from local_api.alert import assess_alerts

    health = _make_healthy_report()
    health["launchagent_loaded"] = False
    report = assess_alerts(health, pending_max=50)

    assert report["status"] == "critical"
    assert any("launch" in a["title"].lower() or "LaunchAgent" in a["title"] for a in report["alerts"])


def test_alert_check_pending_beyond_threshold_warn():
    from local_api.alert import assess_alerts

    health = _make_healthy_report()
    health["pending_count"] = 55
    report = assess_alerts(health, pending_max=50)

    assert report["status"] == "warn"
    assert any("pending" in a["title"].lower() for a in report["alerts"])


def test_alert_check_failure_newer_than_success_critical():
    from local_api.alert import assess_alerts

    health = _make_healthy_report()
    health["latest_failure_at"] = "2026-02-01T00:00:00"
    health["latest_success_at"] = "2026-01-01T00:00:00"
    report = assess_alerts(health, pending_max=50)

    assert report["status"] == "critical"
    assert any(("failure" in a["title"].lower() or "fail" in a["title"].lower()) for a in report["alerts"])


def test_alert_check_last_exit_code_non_zero_critical():
    from local_api.alert import assess_alerts

    health = _make_healthy_report()
    health["last_exit_code"] = 1
    report = assess_alerts(health, pending_max=50)

    assert report["status"] == "critical"
    assert any(("exit" in a["title"].lower()) for a in report["alerts"])


def test_alert_check_preflight_not_ready_critical():
    from local_api.alert import assess_alerts

    health = _make_healthy_report()
    health["preflight_ready"] = False
    report = assess_alerts(health, pending_max=50)

    assert report["status"] == "critical"
    assert any("preflight" in a["title"].lower() for a in report["alerts"])


def test_alert_cli_outputs_json(monkeypatch, capsys):
    from local_api.alert import Alert, assess_alerts

    monkeypatch.setattr(
        "local_api.health.collect_background_health",
        lambda: _make_healthy_report(),
    )

    from local_api.scripts import sync_alert_check

    rc = sync_alert_check.main([])
    output = capsys.readouterr().out

    assert rc == 0
    assert "ok" in output or '"status"' in output
    assert "alerts" in output


def test_notify_stub_returns_default(monkeypatch):
    from local_api.alert import send_alert_notifications
    from local_api.alert import Alert

    alerts = [Alert(title="Test alert", severity="critical", detail="unit test")]
    assert send_alert_notifications(alerts) is True