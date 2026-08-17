"""
TDD Tests for Swarm Lock Cockpit Status & Eviction API in Prismatic Engine.
"""

import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from prismatic.core.locking import SwarmLockManager


def test_swarm_lock_manager_enriched_status_and_contention(tmp_path):
    lock_file = tmp_path / "swarm_locks.json"
    mgr = SwarmLockManager(lock_file=lock_file)

    # 1. Acquire with rich metadata
    acquired = mgr.acquire(
        resource_id="src/gateway/server.py",
        agent_id="agent-fred",
        timeout_s=5.0,
        metadata={
            "intention": "EXCLUSIVE_MUTATION",
            "model": "claude-3.7-sonnet",
            "task_id": "GRO-4762",
            "task_provider": "linear",
        },
    )
    assert acquired is True

    # 2. Get enriched status
    enriched = mgr.get_enriched_status()
    assert len(enriched["locks"]) == 1
    lock_item = enriched["locks"][0]
    assert lock_item["resource"] == "src/gateway/server.py"
    assert lock_item["holder"] == "agent-fred"
    assert lock_item["intention"] == "EXCLUSIVE_MUTATION"
    assert lock_item["model"] == "claude-3.7-sonnet"
    assert lock_item["task_id"] == "GRO-4762"
    assert lock_item["task_provider"] == "linear"
    assert lock_item["remaining_seconds"] > 0
    assert lock_item["is_expired"] is False

    # 3. Simulate second agent contention
    mgr2 = SwarmLockManager(lock_file=lock_file)
    contended = mgr2.acquire(
        resource_id="src/gateway/server.py",
        agent_id="agent-kai",
        timeout_s=0.2,
        metadata={"intention": "READ_INSPECT", "model": "gpt-4o", "task_id": "GRO-4763"},
    )
    assert contended is False

    # 4. Verify contention tracked in enriched status
    enriched_after = mgr.get_enriched_status()
    assert enriched_after["deflected_collisions"] >= 1
    assert len(enriched_after["locks"][0]["contentions"]) >= 1
    contention = enriched_after["locks"][0]["contentions"][0]
    assert contention["agent_id"] == "agent-kai"

    # 5. Operator Eviction
    evicted = mgr.evict("src/gateway/server.py", reason="operator_eviction_test")
    assert evicted is True
    assert len(mgr.get_status()) == 0


def test_gateway_swarmlock_status_and_evict_api(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    from prismatic.gateway.server import app

    client = TestClient(app)

    # Initially empty
    res = client.get("/api/gateway/swarmlock/status")
    assert res.status_code == 200
    data = res.json()
    assert "locks" in data
    assert "deflected_collisions" in data

    # Acquire lock via manager
    from prismatic.lock import _get_lock_manager
    mgr = _get_lock_manager()
    mgr.acquire(
        "src/core/dispatcher.py",
        "agent-antigravity",
        metadata={"intention": "SAFE_REFACTOR", "model": "gemini-3.7", "task_id": "#142", "task_provider": "github"},
    )

    # Query status endpoint
    res_after = client.get("/api/gateway/swarmlock/status")
    assert res_after.status_code == 200
    data_after = res_after.json()
    assert len(data_after["locks"]) == 1
    assert data_after["locks"][0]["resource"] == "src/core/dispatcher.py"
    assert data_after["locks"][0]["holder"] == "agent-antigravity"
    assert data_after["locks"][0]["intention"] == "SAFE_REFACTOR"

    # Test Evict endpoint
    evict_res = client.post(
        "/api/gateway/swarmlock/evict",
        json={"resource": "src/core/dispatcher.py", "reason": "admin force release"},
    )
    assert evict_res.status_code == 200
    assert evict_res.json()["ok"] is True

    # Verify evicted
    res_final = client.get("/api/gateway/swarmlock/status")
    assert len(res_final.json()["locks"]) == 0

    # Test History endpoint
    history_res = client.get("/api/gateway/swarmlock/history?limit=10")
    assert history_res.status_code == 200
    history_data = history_res.json()
    assert history_data["ok"] is True
    assert history_data["count"] >= 1
    events = history_data["events"]
    assert any(e["event_type"] == "evicted" for e in events)

    # Test Config endpoints
    config_res = client.get("/api/gateway/swarmlock/config")
    assert config_res.status_code == 200
    assert "stale_ttl_seconds" in config_res.json()

    config_update_res = client.post(
        "/api/gateway/swarmlock/config",
        json={"stale_ttl_seconds": 60.0},
    )
    assert config_update_res.status_code == 200
    assert config_update_res.json()["config"]["stale_ttl_seconds"] == 60.0

