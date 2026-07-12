import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.run_records import AgentRunRecord, AgentRunRecordStore


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _seed_agent_state(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    registry_path = tmp_path / "agent_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "agent:agy": {"status": "active", "role": "Vision", "branch": "feature/agy", "workspace": "prismatic", "pid": 1111, "service_name": "agy-worker.service"},
                "agent:fred": {"status": "idle", "role": "Staging Governor", "branch": "feature/fred"},
                "agent:churner": {"status": "error", "role": "Loop Detector", "service_name": "churner.service"},
            }
        )
    )
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv("PRISMATIC_AGENT_REGISTRY", str(registry_path))
    store = AgentRunRecordStore(str(state_dir / "run_records.json"))
    records = [
        AgentRunRecord("run-active", "GRO-100", "agent:agy", status="running", started_at=_iso(-120)),
        AgentRunRecord("run-queued", "GRO-101", "agent:jules", status="pending", started_at=_iso(-300)),
        AgentRunRecord("run-feedback", "GRO-102", "agent:ned", status="failed", started_at=_iso(-400), completed_at=_iso(-350), error_message="Awaiting user feedback before continuing"),
        AgentRunRecord("run-complete", "GRO-103", "agent:kai", status="completed", started_at=_iso(-800), completed_at=_iso(-120)),
        AgentRunRecord("run-error", "GRO-104", "agent:codex", status="failed", started_at=_iso(-900), completed_at=_iso(-850), error_message="unit test failure"),
        AgentRunRecord("run-churn", "GRO-105", "agent:churner", status="failed", started_at=_iso(-700), completed_at=_iso(-650), error_message="restart loop / crashloop detected"),
    ]
    store._records = {record.run_id: record for record in records}
    store._flush_to_disk()
    from prismatic.gateway import server

    server._run_store = store
    return TestClient(server.app)


def test_agents_status_normalized_shape_and_classifications(tmp_path, monkeypatch):
    client = _seed_agent_state(tmp_path, monkeypatch)

    response = client.get("/api/gateway/agents/status")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["source"] == "run_records+agent_registry+queue_state+timeline+health_context"
    assert data["empty"] is False
    for key in ["agents", "workers", "queues", "recent_activity", "awaiting_user_feedback", "completed_recently", "churning_or_launch_failing", "idle", "queue_starved", "evidence"]:
        assert key in data
    statuses = {agent["id"]: agent["status"] for agent in data["agents"]}
    assert statuses["agy"] == "active"
    assert statuses["jules"] == "queue_starved"
    assert statuses["ned"] == "awaiting_user_feedback"
    assert statuses["kai"] == "completed_recently"
    assert statuses["codex"] == "errored"
    assert statuses["churner"] == "churning"
    assert statuses["fred"] == "idle"
    assert data["status_counts"]["active"] >= 1
    assert data["status_counts"]["idle"] >= 1
    assert data["status_counts"]["queue_starved"] >= 1
    assert data["status_counts"]["awaiting_user_feedback"] >= 1
    assert data["status_counts"]["completed_recently"] >= 1
    assert data["status_counts"]["errored"] >= 1
    assert data["status_counts"]["churning"] >= 1
    jules = next(agent for agent in data["agents"] if agent["id"] == "jules")
    assert jules["queue_starved_reason"]
    fred = next(agent for agent in data["agents"] if agent["id"] == "fred")
    assert fred["idle_reason"]


def test_agent_detail_shape_uses_same_live_data(tmp_path, monkeypatch):
    client = _seed_agent_state(tmp_path, monkeypatch)

    response = client.get("/api/gateway/agents/agy")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["agent"]["id"] == "agy"
    assert data["agent"]["status"] == "active"
    assert data["agent"]["current_issue"] == "GRO-100"
    assert data["agent"]["current_issue_url"].endswith("issue=GRO-100")
    assert data["recent_runs"]
    assert data["queue_context"]["source"] == "webhook_queue"
    assert "health_context" in data
    assert data["evidence"]["source"] == "run_records+agent_registry+queue_state+timeline+health_context"


def test_dashboard_agent_contract_has_live_wiring_and_no_mock_fallback(tmp_path, monkeypatch):
    client = _seed_agent_state(tmp_path, monkeypatch)

    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text
    assert 'fetch(`${API_PREFIX}/agents/status`)' in html
    assert 'fetch(`${API_PREFIX}/agents/${encodeURIComponent(id)}`)' in html
    assert "agent-cards-grid" in html
    assert "agent-status-counts" in html
    assert "Agent status API unavailable" in html
    assert "No mock fallback rendered" in html
    assert "mockAgents" not in html
    assert "mockWorkers" not in html
    assert "Creating rebase branches for GRO-671" not in html
    assert "Synchronizing SQLite file watch events" not in html
