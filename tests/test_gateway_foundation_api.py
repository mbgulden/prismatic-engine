from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.run_records import AgentRunRecordStore


def _client_with_foundation_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    from prismatic.gateway import server

    server._run_store = AgentRunRecordStore(str(tmp_path / "state" / "run_records.json"))
    jules = server._run_store.create_run("GRO-JULES", "jules")
    ned = server._run_store.create_run("GRO-NED", "ned")
    agy = server._run_store.create_run("GRO-AGY", "agy")
    other = server._run_store.create_run("GRO-FRED", "fred")
    server._run_store.update_run(jules, "completed", output_path="/tmp/jules-proof.md")
    server._run_store.update_run(ned, "failed", error="review failed")
    server._run_store.update_run(agy, "running")
    server._run_store.update_run(other, "completed", output_path="/tmp/fred-proof.md")
    return TestClient(server.app), {"jules": jules, "ned": ned, "agy": agy, "other": other}


def test_foundation_peer_review_endpoint_normalizes_run_evidence(tmp_path: Path, monkeypatch) -> None:
    client, runs = _client_with_foundation_runs(tmp_path, monkeypatch)

    res = client.get("/api/gateway/foundation/peer_review")
    assert res.status_code == 200
    payload = res.json()
    assert payload["source"] == "run_records+foundation_control_state"
    assert payload["empty"] is False
    assert payload["jules_count"] == 1
    assert payload["ned_count"] == 1
    assert payload["agy_count"] == 1
    assert payload["counts"] == {"jules": 1, "ned": 1, "agy": 1}
    assert payload["limits"]["jules"] == 300
    assert payload["current_agy_reviewer"] == "agent:agy"
    assert payload["evidence"]["run_record_count"] == 3
    assert set(payload["evidence"]["agents_seen"]) == {"jules", "ned", "agy"}
    assert {item["run_id"] for item in payload["recent_activity"]} >= {runs["jules"], runs["ned"], runs["agy"]}
    assert runs["other"] not in {item["run_id"] for item in payload["recent_activity"]}


def test_foundation_control_is_allowlisted_and_emits_timeline(tmp_path: Path, monkeypatch) -> None:
    client, _runs = _client_with_foundation_runs(tmp_path, monkeypatch)

    invalid = client.post("/api/gateway/foundation/control/rm-rf")
    assert invalid.status_code == 400
    assert invalid.json()["allowed_actions"] == ["orchestrate", "sync"]

    sync = client.post("/api/gateway/foundation/control/sync")
    assert sync.status_code == 200
    sync_payload = sync.json()
    assert sync_payload["status"] == "ok"
    assert sync_payload["timeline_item"]["source"] == "FoundationControl"
    assert sync_payload["entry"]["action"] == "sync"
    assert sync_payload["stdout"] == ""
    assert sync_payload["stderr"] == ""

    peer_review = client.get("/api/gateway/foundation/peer_review").json()
    assert peer_review["last_control_action"]["action"] == "sync"
    timeline = client.get("/api/timeline?source=FoundationControl").json()
    assert any(item["title"] == "Verify sync / git status" for item in timeline["items"])

    orchestrate = client.post("/api/gateway/foundation/control/orchestrate")
    assert orchestrate.status_code == 200
    assert orchestrate.json()["timeline_item"]["source"] == "FoundationControl"


def test_foundation_dashboard_fetch_action_contract(tmp_path: Path, monkeypatch) -> None:
    client, _runs = _client_with_foundation_runs(tmp_path, monkeypatch)

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    html = dashboard.text
    assert "fetch(`${API_PREFIX}/foundation/peer_review`)" in html
    assert "fetch(`${API_PREFIX}/foundation/control/${action}`, { method: \"POST\" })" in html
    assert "Foundation / Peer Review API unavailable" in html
    assert "No Jules/Ned/AGY peer-review run evidence yet" in html
    assert "No fallback data rendered" in html
    assert "Timeline source: ${data.timeline_item?.source || 'FoundationControl'}" in html
    assert "mockFoundation" not in html
    assert "STDOUT" not in html
