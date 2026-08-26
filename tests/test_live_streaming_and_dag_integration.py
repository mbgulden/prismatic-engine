"""Integration test suite for live streaming, swarmlock elasticity, and DAG topology event sourcing."""

import pytest
from starlette.testclient import TestClient

from prismatic.gateway.server import app
from prismatic.hypervisor.ledger import get_hypervisor_ledger


@pytest.fixture
def client():
    return TestClient(app)


def test_swarmlock_acquire_and_release_schema_elasticity(client):
    # 1. Acquire with interceptor payload schema (paths, owner, ttl)
    acquire_payload = {
        "paths": ["prismatic/gateway/server.py", "prismatic/lock.py"],
        "owner": "lightbringer-agy",
        "task_id": "GRO-4203",
        "ttl": 300,
    }
    res = client.post("/api/gateway/swarmlock/acquire", json=acquire_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert "lease_id" in data
    assert data["resource"] == "prismatic/gateway/server.py"
    assert data["agent_id"] == "lightbringer-agy"

    lease_id = data["lease_id"]

    # 2. Heartbeat renewal
    hb_res = client.post("/api/gateway/swarmlock/heartbeat", json={
        "paths": ["prismatic/gateway/server.py"],
        "owner": "lightbringer-agy",
        "lease_id": lease_id,
    })
    assert hb_res.status_code == 200
    assert hb_res.json()["ok"] is True

    # 3. Release
    rel_res = client.post("/api/gateway/swarmlock/release", json={
        "paths": ["prismatic/gateway/server.py"],
        "owner": "lightbringer-agy",
        "task_id": "GRO-4203",
        "lease_id": lease_id,
    })
    assert rel_res.status_code == 200
    assert rel_res.json()["ok"] is True


def test_signals_emit_and_hypervisor_ledger_sync(client):
    sig_payload = {
        "agent": "hermes",
        "event_type": "build_success",
        "issue_id": "GRO-4203",
        "status": "success",
        "message": "Hermes built release package successfully",
        "severity": "info",
    }
    res = client.post("/api/gateway/signals/emit", json=sig_payload)
    assert res.status_code == 200

    # Verify signal appears in /api/gateway/signals
    signals_res = client.get("/api/gateway/signals")
    assert signals_res.status_code == 200
    assert any(s.get("message") == "Hermes built release package successfully" for s in signals_res.json().get("items", []))

    # Verify event appears in /api/gateway/hypervisor/ledger
    ledger_res = client.get("/api/gateway/hypervisor/ledger")
    assert ledger_res.status_code == 200
    ledger_data = ledger_res.json()
    assert ledger_data["ok"] is True
    assert any(e.get("producer") == "hermes" for e in ledger_data.get("events", []))


def test_dag_topology_event_sourcing(client):
    res = client.get("/api/gateway/dag/topology?span_limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) > 0
    # Check that stage 1 (Prompt Ingestion) and stage 6 (Merkle DAG Commit) nodes exist
    stages = {n["stage"] for n in data["nodes"]}
    assert 1 in stages
    assert 6 in stages
