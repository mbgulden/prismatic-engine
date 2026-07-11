import json
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway.server import app


def test_dashboard_recovery_controls_record_server_side_status(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    client = TestClient(app)

    initial = client.get("/api/dashboard/recovery-control/status")
    assert initial.status_code == 200
    assert initial.json()["actions"] == []

    for action in ("restart", "retry", "replay"):
        response = client.post(
            "/api/dashboard/recovery-control",
            json={"action": action, "agent": "Ned", "ref": "GRO-3529"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["entry"]["action"] == action
        assert payload["entry"]["agent"] == "Ned"
        assert payload["entry"]["ref"] == "GRO-3529"
        assert action in payload["status"]
        assert payload["state"]["last_status"] == payload["status"]

    state_path = Path(tmp_path) / "dashboard_recovery_controls.json"
    state = json.loads(state_path.read_text())
    assert state["last_status"].startswith("replay queued")
    assert [entry["action"] for entry in state["actions"][:3]] == ["replay", "retry", "restart"]

    status = client.get("/api/dashboard/recovery-control/status")
    assert status.status_code == 200
    assert status.json()["last_status"].startswith("replay queued")


def test_dashboard_recovery_controls_reject_invalid_action(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/dashboard/recovery-control",
        json={"action": "shutdown", "agent": "Ned", "ref": "GRO-3529"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_action"
    assert sorted(response.json()["allowed"]) == ["replay", "restart", "retry"]
    assert not (Path(tmp_path) / "dashboard_recovery_controls.json").exists()
