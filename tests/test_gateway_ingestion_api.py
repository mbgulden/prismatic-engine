from pathlib import Path
import hashlib
import hmac

from fastapi.testclient import TestClient

from prismatic.run_records import AgentRunRecordStore


def _client_with_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    from prismatic.gateway import server

    server._run_store = AgentRunRecordStore(str(tmp_path / "state" / "run_records.json"))
    for key in list(server._webhook_counters):
        server._webhook_counters[key] = 0
    server._webhook_counters["linear_received"] = 2
    server._webhook_counters["linear_published"] = 1
    server._webhook_counters["github_auth_failed"] = 1

    pending = server._run_store.create_run("GRO-QUEUE", "fred")
    processing = server._run_store.create_run("GRO-RUN", "agy")
    failed = server._run_store.create_run("GRO-FAIL", "kai")
    completed = server._run_store.create_run("GRO-DONE", "jules")
    server._run_store.update_run(processing, "running")
    server._run_store.update_run(failed, "failed", error="boom")
    server._run_store.update_run(completed, "completed", output_path="/tmp/proof.txt")
    return TestClient(server.app), {"pending": pending, "processing": processing, "failed": failed, "completed": completed}


def test_ingestion_read_endpoints_normalize_existing_state(tmp_path: Path, monkeypatch) -> None:
    client, runs = _client_with_runs(tmp_path, monkeypatch)

    stats = client.get("/api/gateway/webhooks/stats")
    assert stats.status_code == 200
    stats_payload = stats.json()
    assert stats_payload["source"] == "gateway_counters+run_records"
    assert stats_payload["received"] == 2
    assert stats_payload["auth_failed"] == 1
    assert stats_payload["queue_depths"]["pending"] == 1
    assert stats_payload["queue_depths"]["processing"] == 1
    assert stats_payload["queue_depths"]["failed"] == 1

    queue = client.get("/api/gateway/webhooks/queue")
    assert queue.status_code == 200
    queue_payload = queue.json()
    assert queue_payload["source"] == "run_records"
    assert queue_payload["total"] == 4
    assert {item["dispatch_status"] for item in queue_payload["items"]} >= {"pending", "processing", "failed", "completed"}
    failed_only = client.get("/api/gateway/webhooks/queue?status=failed")
    assert failed_only.status_code == 200
    assert failed_only.json()["total"] == 1
    assert failed_only.json()["items"][0]["run_id"] == runs["failed"]

    dispatcher = client.get("/api/gateway/dispatcher/status")
    assert dispatcher.status_code == 200
    dispatcher_payload = dispatcher.json()
    assert dispatcher_payload["source"] == "dashboard_dispatcher_state+run_records"
    assert dispatcher_payload["status"] == "active"
    assert dispatcher_payload["running"] is True
    assert "agy" in dispatcher_payload["active_agents"]

    recovery = client.get("/api/gateway/recovery/status")
    assert recovery.status_code == 200
    recovery_payload = recovery.json()
    assert recovery_payload["source"] == "dashboard_recovery_controls+run_records"
    assert recovery_payload["first_failing_layer"] == "ingest_auth"
    assert any(item["id"] == "ingest_auth" for item in recovery_payload["failure_taxonomy"])
    assert recovery_payload["recent_failures"][0]["run_id"] == runs["failed"]


def test_ingestion_controls_persist_state_and_emit_timeline(tmp_path: Path, monkeypatch) -> None:
    client, runs = _client_with_runs(tmp_path, monkeypatch)

    stop = client.post("/api/gateway/dispatcher/stop")
    assert stop.status_code == 200
    assert stop.json()["timeline_item"]["source"] == "DispatcherControl"
    dispatcher = client.get("/api/gateway/dispatcher/status").json()
    assert dispatcher["status"] == "paused"
    assert dispatcher["last_command"]["action"] == "stop"

    retry = client.post(f"/api/gateway/webhooks/queue/retry/{runs['failed']}")
    assert retry.status_code == 200
    assert retry.json()["timeline_item"]["source"] == "QueueControl"
    purge = client.post("/api/gateway/webhooks/queue/purge")
    assert purge.status_code == 200
    assert purge.json()["timeline_item"]["source"] == "QueueControl"

    timeline_dispatcher = client.get("/api/timeline?source=DispatcherControl").json()
    assert any(item["title"] == "Stop dispatcher" for item in timeline_dispatcher["items"])
    timeline_queue = client.get("/api/timeline?source=QueueControl").json()
    titles = {item["title"] for item in timeline_queue["items"]}
    assert "Retry queue task" in titles
    assert "Purge queue history" in titles


def test_linear_simulator_alias_and_dashboard_contract(tmp_path: Path, monkeypatch) -> None:
    client, _runs = _client_with_runs(tmp_path, monkeypatch)

    secret = "test-linear-secret"
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", secret)
    body = b'{"action":"update","data":{"identifier":"GRO-SIM"}}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    simulator = client.post("/webhooks/linear", content=body, headers={"linear-signature": signature, "content-type": "application/json"})
    assert simulator.status_code == 200
    assert simulator.json()["status"] == "ok"

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    html = dashboard.text
    assert "fetch(`${API_PREFIX}/webhooks/stats`)" in html
    assert "fetch(`${API_PREFIX}/webhooks/queue`)" in html
    assert "fetch(`${API_PREFIX}/dispatcher/status`)" in html
    assert "fetch(`${API_PREFIX}/recovery/status`)" in html
    assert "Ingestion Queue API unavailable" in html
    assert "Queue is empty" in html
    assert "mockQueue" not in html
