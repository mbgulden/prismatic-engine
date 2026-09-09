"""Tests for Dynamic Harness Discovery and Hermes Profile Runner."""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.harnesses.discovery import HarnessDiscoveryManager, DiscoveredHarness
from prismatic.worker.harness import HermesProfileRunner
from prismatic.worker.protocol import WorkerJob


def test_harness_discovery_manager_antigravity():
    mgr = HarnessDiscoveryManager()
    harnesses = mgr.discover_antigravity()
    assert len(harnesses) > 0
    ids = [h.id for h in harnesses]
    assert any("gemini-3.8-flash-high" in hid for hid in ids)
    assert any("claude-sonnet-4-6" in hid for hid in ids)
    first = harnesses[0]
    assert first.kind == "agy"
    assert "antigravity" in first.tags
    assert first.status == "online"


def test_harness_discovery_manager_hermes():
    mgr = HarnessDiscoveryManager()
    harnesses = mgr.discover_hermes_profiles()
    assert len(harnesses) > 0
    targets = [h.target for h in harnesses]
    assert "george" in targets
    assert "kai" in targets
    george_h = next(h for h in harnesses if h.target == "george")
    assert george_h.kind == "hermes"
    assert "HERMES_HOME" in george_h.env
    assert "review" in george_h.tags


def test_hermes_profile_runner_execution(monkeypatch):
    import subprocess
    runner = HermesProfileRunner()
    assert runner.is_available() is True

    # Deterministic mocked test
    def mock_subprocess_run(cmd, capture_output=True, text=True, timeout=None, env=None):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="HERMES_PROFILE_OK\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    job = WorkerJob(
        id="job-hermes-discovery-test",
        task_id="GRO-HERMES-DISCOVERY",
        command="Respond with: HERMES_PROFILE_OK",
        tags=["hermes:george"],
        metadata={"harness": "hermes", "profile": "george"},
        timeout_seconds=30,
    )
    result = runner.execute(job)
    assert result.exit_code == 0
    assert "HERMES_PROFILE_OK" in result.stdout
    assert result.artifacts["harness"] == "hermes"
    assert result.artifacts["profile"] == "george"


def test_gateway_harnesses_endpoints():
    client = TestClient(app)

    # 1. GET /api/gateway/harnesses
    res = client.get("/api/gateway/harnesses")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["total"] > 0
    harness_ids = [h["id"] for h in data["harnesses"]]
    assert any(h.startswith("agy:") for h in harness_ids)
    assert any(h.startswith("hermes:") for h in harness_ids)

    # 2. POST /api/gateway/harnesses/discover
    res_disc = client.post("/api/gateway/harnesses/discover", json={"auto_bench": False})
    assert res_disc.status_code == 200
    disc_data = res_disc.json()
    assert disc_data["ok"] is True
    assert disc_data["total"] >= len(harness_ids)
