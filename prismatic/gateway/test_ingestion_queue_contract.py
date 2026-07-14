from __future__ import annotations

import importlib
import os

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    import prismatic.gateway.server as server

    server = importlib.reload(server)
    return TestClient(server.app)


def test_linear_webhook_populates_durable_gateway_queue(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = {
        "id": "evt-gro-queue-test",
        "action": "update",
        "type": "Issue",
        "data": {
            "id": "issue-gro-queue-test",
            "identifier": "GRO-QUEUE-TEST",
            "labels": {"nodes": [{"name": "agent:fred"}]},
        },
    }

    response = client.post("/webhooks/linear", json=payload)
    assert response.status_code == 200
    assert response.json()["queue"]["item"]["identifier"] == "GRO-QUEUE-TEST"

    stats = client.get("/api/gateway/webhooks/stats").json()
    assert stats["source"] == "linear_webhook_queue.db"
    assert stats["queue_depths"]["pending"] == 1

    queue = client.get("/api/gateway/webhooks/queue").json()
    assert queue["source"] == "linear_webhook_queue.db"
    assert queue["total"] == 1
    item = queue["items"][0]
    assert item["identifier"] == "GRO-QUEUE-TEST"
    assert item["agent_name"] == "fred"
    assert item["dispatch_status"] == "pending"


def test_retry_and_purge_mutate_queue_and_emit_timeline(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = {
        "id": "evt-gro-queue-mut",
        "action": "update",
        "type": "Issue",
        "data": {
            "id": "issue-gro-queue-mut",
            "identifier": "GRO-QUEUE-MUT",
            "labels": [{"name": "agent:kai"}],
        },
    }
    client.post("/api/gateway/linear", json=payload)
    item = client.get("/api/gateway/webhooks/queue").json()["items"][0]

    retry = client.post(f"/api/gateway/webhooks/queue/retry/{item['id']}")
    assert retry.status_code == 200
    retry_json = retry.json()
    assert retry_json["status"] == "ok"
    assert retry_json["updated"] == 1
    assert retry_json["status"] != "accepted_noop"

    timeline = client.get("/api/gateway/timeline?source=QueueControl&limit=10").json()
    assert any(entry.get("source") == "QueueControl" for entry in timeline.get("items", []))

    # Mark the row terminal through the adapter DB, then verify purge removes it.
    from prismatic.ingestion_queue import _connect, QUEUE_TABLE

    with _connect() as conn:
        conn.execute(f"UPDATE {QUEUE_TABLE} SET dispatch_status = 'completed' WHERE id = ?", (item["id"],))
        conn.commit()

    purge = client.post("/api/gateway/webhooks/queue/purge")
    assert purge.status_code == 200
    assert purge.json()["deleted"] == 1
    assert client.get("/api/gateway/webhooks/queue").json()["total"] == 0


def test_dispatcher_gateway_alias_records_timeline(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/gateway/dispatcher/restart")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted_noop"
    assert body["timeline_item"]["source"] == "DispatcherControl"

    timeline = client.get("/api/gateway/timeline?source=DispatcherControl&limit=10").json()
    assert any(entry.get("source") == "DispatcherControl" for entry in timeline.get("items", []))
