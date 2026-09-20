"""Test suite for Prismatic Hub Bidirectional Control Plane and Swarm Emergency Eviction."""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.core.fleet_control import FleetControlManager
from prismatic.client import HypervisorClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_control_state():
    mgr = FleetControlManager.get_instance()
    mgr.resume("all")
    yield
    mgr.resume("all")


def test_fleet_control_pause_and_resume(client):
    # 1. Initial status is unpaused
    res = client.get("/api/gateway/control/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["status"]["fleet_paused"] is False

    # 2. Pause the entire fleet
    res_pause = client.post("/api/gateway/control/pause", json={"reason": "operator_drill"})
    assert res_pause.status_code == 200
    assert res_pause.json()["state"]["fleet_paused"] is True

    # 3. Check client status
    res = client.get("/api/gateway/control/status")
    assert res.json()["status"]["fleet_paused"] is True

    # 4. Resume the fleet
    res_resume = client.post("/api/gateway/control/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["state"]["fleet_paused"] is False


def test_per_agent_pause(client):
    mgr = FleetControlManager.get_instance()
    
    # Pause only agent 'kai'
    res = client.post("/api/gateway/control/pause", json={"agent_id": "kai"})
    assert res.status_code == 200
    assert "kai" in res.json()["state"]["paused_agents"]

    assert mgr.is_paused("kai") is True
    assert mgr.is_paused("agy") is False

    # Resume 'kai'
    res_res = client.post("/api/gateway/control/resume", json={"agent_id": "kai"})
    assert res_res.status_code == 200
    assert "kai" not in res_res.json()["state"]["paused_agents"]
    assert mgr.is_paused("kai") is False


def test_emergency_swarmlock_evict_all(client):
    # Evict all locks when none or some active
    res = client.post("/api/gateway/swarmlock/evict-all", json={"reason": "test_emergency_purge"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["evicted"], list)
    assert data["reason"] == "test_emergency_purge"
