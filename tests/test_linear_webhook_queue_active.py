from __future__ import annotations

import argparse
import importlib
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


def client_for_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    import prismatic.gateway.server as server

    server = importlib.reload(server)
    return TestClient(server.app)


def fixture_payload(event_id: str = "evt-gro-test-queue") -> dict:
    return {
        "id": event_id,
        "action": "update",
        "type": "Issue",
        "data": {
            "id": "issue-gro-test-queue",
            "identifier": "GRO-TEST-WEBHOOK-QUEUE",
            "labels": {"nodes": [{"name": "agent:fred"}]},
        },
    }


def load_drainer(repo: Path):
    spec = importlib.util.spec_from_file_location("drain_webhook_queue", repo / "scripts" / "drain_webhook_queue.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linear_webhook_persists_durable_queue_row_idempotently(tmp_path: Path, monkeypatch):
    client = client_for_state(tmp_path, monkeypatch)
    payload = fixture_payload()

    first = client.post("/api/gateway/linear", json=payload)
    second = client.post("/webhooks/linear", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["queue"]["inserted"] is True
    assert second.json()["queue"]["inserted"] is False

    queue = client.get("/api/gateway/webhooks/queue").json()
    assert queue["source"] == "linear_webhook_queue.db"
    assert queue["total"] == 1
    row = queue["items"][0]
    assert row["event_id"] == payload["id"]
    assert row["identifier"] == "GRO-TEST-WEBHOOK-QUEUE"
    assert row["action"] == "update"
    assert row["event_type"] == "Issue"
    assert row["dispatch_status"] == "pending"
    assert row["agent_name"] == "fred"
    assert row["raw_json"]

    status = client.get("/api/gateway/webhooks/queue/status").json()
    assert status["marker"] == "LINEAR_WEBHOOK_QUEUE_ACTIVE_OK"
    assert status["source"] == "linear_webhook_queue.db"
    assert status["pending_count"] == 1
    assert status["latest_event_identifier"] == "GRO-TEST-WEBHOOK-QUEUE"
    assert status["latest_event_status"] == "pending"


def test_bounded_drain_transitions_one_fixture_without_live_linear(tmp_path: Path, monkeypatch):
    client = client_for_state(tmp_path, monkeypatch)
    payload = fixture_payload("evt-gro-test-drain")
    client.post("/api/gateway/linear", json=payload)

    calls: list[str] = []

    def fake_dispatch(*, identifier: str):
        calls.append(identifier)
        return {"ok": True, "message": "fixture dispatch accepted"}

    repo = Path(__file__).resolve().parents[1]
    drainer = load_drainer(repo)
    args = argparse.Namespace(
        max=1,
        dry_run=False,
        stale_only=False,
        backfill=False,
        reset=False,
        since=None,
        until=None,
    )
    rc = drainer.drain(args, dispatch_fn=fake_dispatch)

    assert rc == 0
    assert calls == ["GRO-TEST-WEBHOOK-QUEUE"]
    queue = client.get("/api/gateway/webhooks/queue").json()
    assert queue["items"][0]["dispatch_status"] == "dispatched"
    status = client.get("/api/gateway/webhooks/queue/status").json()
    assert status["marker"] == "LINEAR_WEBHOOK_QUEUE_ACTIVE_OK"
    assert status["pending_count"] == 0
    assert status["latest_event_status"] == "dispatched"
    assert status["last_drain_result"] == "ok"
    assert status["last_drain_counts"]["processed"] == 1
    assert status["last_drain_counts"]["dispatched"] == 1


def test_retry_and_purge_are_real_queue_mutations(tmp_path: Path, monkeypatch):
    client = client_for_state(tmp_path, monkeypatch)
    client.post("/api/gateway/linear", json=fixture_payload("evt-gro-test-mut"))
    item = client.get("/api/gateway/webhooks/queue").json()["items"][0]

    retry = client.post(f"/api/gateway/webhooks/queue/retry/{item['id']}").json()
    assert retry["status"] == "ok"
    assert retry["updated"] == 1
    assert retry["status"] != "accepted_noop"

    from prismatic.ingestion_queue import QUEUE_TABLE, _connect

    with _connect() as conn:
        conn.execute(f"UPDATE {QUEUE_TABLE} SET dispatch_status = 'dispatched' WHERE id = ?", (item["id"],))
        conn.commit()

    purge = client.post("/api/gateway/webhooks/queue/purge").json()
    assert purge["status"] == "ok"
    assert purge["deleted"] == 1
    assert client.get("/api/gateway/webhooks/queue").json()["total"] == 0
