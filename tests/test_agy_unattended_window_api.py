from __future__ import annotations

from fastapi.testclient import TestClient

import prismatic.agy_unattended_window as window
import prismatic.gateway.server as server
from prismatic.agy_unattended_window import AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER


def queue_ok():
    return {
        "marker": "LINEAR_WEBHOOK_QUEUE_ACTIVE_OK",
        "assigned_agent_marker": "ASSIGNED_AGENT_EVENT_DISPATCH_OK",
        "result_writeback_marker": "ASSIGNED_AGENT_RESULT_WRITEBACK_OK",
        "dispatch_recovery_marker": "ASSIGNED_AGENT_DISPATCH_RECOVERY_OK",
        "queue_depth": 0,
        "pending_count": 0,
    }


def guard_ok(**kwargs):
    return {
        "allowed": True,
        "readiness_state": "ready",
        "reason": "ready",
        "blockers": [],
        "marker": "AGY_OVERNIGHT_READINESS_GUARD_OK",
    }


def latest_ok():
    return {
        "marker": "AGY_LIMITED_OVERNIGHT_DRY_RUN_OK",
        "status": "pass",
        "stop_reason": "completed_one_task_and_stopped",
        "launched_tasks": 1,
        "verification_gate": "pass",
    }


def test_unattended_window_api_status_evaluate_and_control(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(window, "queue_status_payload", queue_ok, raising=False)
    monkeypatch.setattr(window, "evaluate_overnight_readiness", guard_ok, raising=False)
    monkeypatch.setattr(window, "default_model_preflight", lambda model: (True, "OK"), raising=False)
    monkeypatch.setattr(window, "_latest_limited_run", latest_ok, raising=False)

    client = TestClient(server.app)
    status = client.get("/api/gateway/agy/unattended-window/status")
    assert status.status_code == 200
    assert status.json()["marker"] == AGY_LIMITED_UNATTENDED_WINDOW_GUARD_MARKER

    approval = client.post("/api/gateway/agy/unattended-window/request-approval", json={"agent": "agy", "max_tasks": 2})
    assert approval.status_code == 200
    assert approval.json()["status"] == "approval_requested"

    pause = client.post("/api/gateway/agy/unattended-window/pause")
    assert pause.status_code == 200
    assert pause.json()["operator_pause"] is True
    resume = client.post("/api/gateway/agy/unattended-window/resume")
    assert resume.status_code == 200
    assert resume.json()["operator_pause"] is False

    blocked = client.post("/api/gateway/agy/unattended-window/evaluate", json={"agent": "agy", "max_tasks": 3})
    assert blocked.status_code == 409
    assert blocked.json()["launched_tasks"] == 0
    assert "max_tasks > 2" in blocked.json()["blockers"]
