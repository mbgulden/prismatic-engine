from pathlib import Path

from fastapi.testclient import TestClient


def test_dispatcher_controls_emit_timeline_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    from prismatic.gateway.server import app

    client = TestClient(app)

    start = client.post("/api/gateway/dispatcher/start")
    assert start.status_code == 200
    assert start.json()["ok"] is True
    assert start.json()["timeline_item"]["source"] == "DispatcherControl"

    restart = client.post("/api/gateway/dispatcher/restart")
    assert restart.status_code == 200
    assert restart.json()["ok"] is True

    bad = client.post("/api/gateway/dispatcher/nope")
    assert bad.status_code == 400

    timeline = client.get("/api/timeline?source=DispatcherControl")
    assert timeline.status_code == 200
    titles = {item["title"] for item in timeline.json()["items"]}
    assert "Start dispatcher" in titles
    assert "Restart dispatcher" in titles


def test_queue_controls_emit_timeline_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    from prismatic.gateway.server import app

    client = TestClient(app)

    retry = client.post("/api/gateway/webhooks/queue/retry/task-123")
    assert retry.status_code == 200
    assert retry.json()["ok"] is True
    assert retry.json()["timeline_item"]["source"] == "QueueControl"

    purge = client.post("/api/gateway/webhooks/queue/purge")
    assert purge.status_code == 200
    assert purge.json()["ok"] is True

    timeline = client.get("/api/timeline?source=QueueControl")
    assert timeline.status_code == 200
    titles = {item["title"] for item in timeline.json()["items"]}
    assert "Retry queue task" in titles
    assert "Purge queue history" in titles


def test_recovery_control_emits_timeline_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    from prismatic.gateway.server import app

    client = TestClient(app)

    recovery = client.post(
        "/api/dashboard/recovery-control",
        json={"action": "retry", "agent": "agy", "ref": "run-42"},
    )
    assert recovery.status_code == 200
    assert recovery.json()["ok"] is True

    timeline = client.get("/api/timeline?source=RecoveryControl")
    assert timeline.status_code == 200
    items = timeline.json()["items"]
    assert any(item["title"] == "Retry failed run" and item["entity_id"] == "run-42" for item in items)


def test_dashboard_control_paths_match_audited_endpoints() -> None:
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(encoding="utf-8")
    assert "${API_PREFIX}/dispatcher/${action}" in html
    assert "${API_PREFIX}/webhooks/queue/retry/${taskId}" in html
    assert "${API_PREFIX}/webhooks/queue/purge" in html
    assert "const API_PREFIX = \"/api/gateway\"" in html
