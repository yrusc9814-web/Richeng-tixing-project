"""Phase 41 — Tests for sync_doctor."""

from __future__ import annotations

import json
from local_api.scripts import sync_doctor

def test_sync_doctor_cli_outputs_json_with_correct_structure(monkeypatch, capsys):
    # Mock collect_doctor to return a predictable dict without side effects
    def _mock_collect_doctor():
        return {
            "git": {"branch": "test-branch", "head": "abc1234", "dirty": False},
            "main_agent": {"loaded": True, "program": "/mock/main.sh", "interval": 300, "last_exit_code": 0},
            "alert_agent": {"loaded": True, "program": "/mock/alert.sh", "interval": 900, "last_exit_code": 0},
            "preflight_ready": True,
            "helper_auth": "authorized",
            "pending_count": 0,
            "latest_sync_log": None,
            "latest_success_at": None,
            "latest_failure_at": None,
            "alert_status": "ok",
            "recent_alert_logs": ["[mock] status=ok | exit=0"]
        }

    monkeypatch.setattr(sync_doctor, "collect_doctor", _mock_collect_doctor)

    rc = sync_doctor.main([])
    output = capsys.readouterr().out

    assert rc == 0
    parsed = json.loads(output)
    
    # Assert top-level keys
    expected_keys = {
        "git", "main_agent", "alert_agent", "preflight_ready", "helper_auth",
        "pending_count", "latest_sync_log", "latest_success_at", "latest_failure_at",
        "alert_status", "recent_alert_logs"
    }
    assert set(parsed.keys()) == expected_keys

    # Assert specific mock values propagated
    assert parsed["git"]["branch"] == "test-branch"
    assert parsed["alert_agent"]["interval"] == 900
    assert parsed["alert_status"] == "ok"
    assert len(parsed["recent_alert_logs"]) == 1
