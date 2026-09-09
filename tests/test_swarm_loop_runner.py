"""Tests for the Prismatic 7-Step Iterative Swarm Loop Runner and Gateway Routes."""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.swarm.loop_runner import (
    SwarmLoopRunner,
    get_deliverable_by_slug,
    load_all_deliverables,
)


def test_swarm_loop_runner_execution():
    runner = SwarmLoopRunner(task_id="GRO-TEST-LOOP")
    prompt = "Build high-performance distributed key-value store in Rust with Raft consensus"

    result = runner.run(
        prompt=prompt,
        target_domain="kv-engine.local",
        voice="technical",
    )

    assert result.status == "COMPLETED"
    assert result.task_id == "GRO-TEST-LOOP"
    assert result.archetype == "software"
    assert len(result.contracts) == 2
    assert result.deliverable is not None
    assert "build" in result.deliverable["project_slug"] or "distribut" in result.deliverable["project_slug"]

    # Assert all major steps were logged
    logged_steps = [s["step"] for s in result.execution_steps]
    assert "DECOMPOSE" in logged_steps
    assert "DISPATCH" in logged_steps
    assert "EXECUTE" in logged_steps
    assert "REVIEW" in logged_steps
    assert "INTEGRATE" in logged_steps


def test_deliverables_persistence():
    runner = SwarmLoopRunner(task_id="GRO-TEST-PERSIST")
    runner.run(prompt="Build developer documentation portal", target_domain="docs.local")

    delivs = load_all_deliverables()
    assert len(delivs) >= 1

    first = delivs[0]
    slug = first["project_slug"]
    found = get_deliverable_by_slug(slug)
    assert found is not None
    assert found["project_slug"] == slug


def test_gateway_swarm_decompose_api():
    client = TestClient(app)

    res = client.post(
        "/api/gateway/swarm/decompose",
        json={"prompt": "Build modern developer tooling landing page and web portal"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["archetype"] == "web_property"
    assert data["total_contracts"] == 4


def test_gateway_swarm_run_api():
    client = TestClient(app)

    res = client.post(
        "/api/gateway/studio/manifest",
        json={
            "prompt": "Build developer documentation portal with markdown rendering",
            "target_domain": "docs.local",
            "voice": "technical",
            "task_id": "GRO-4854",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    result = data["result"]
    assert result["status"] == "COMPLETED"
    assert result["deliverable"] is not None


def test_gateway_deliverables_list_and_preview_api():
    client = TestClient(app)

    # 1. Manifest a deliverable first
    runner = SwarmLoopRunner(task_id="GRO-PREVIEW-TEST")
    res = runner.run(prompt="Sovereign Hypervisor Runtime Engine")
    slug = res.deliverable["project_slug"]

    # 2. List deliverables
    res_list = client.get("/api/gateway/deliverables")
    assert res_list.status_code == 200
    data_list = res_list.json()
    assert data_list["ok"] is True
    assert data_list["total"] >= 1

    # 3. Get single deliverable
    res_single = client.get(f"/api/gateway/deliverables/{slug}")
    assert res_single.status_code == 200
    data_single = res_single.json()
    assert data_single["deliverable"]["project_slug"] == slug

    # 4. Live HTML Preview
    res_preview = client.get(f"/api/deliverables/{slug}/preview")
    assert res_preview.status_code == 200
    assert "text/html" in res_preview.headers["content-type"]
    assert "Sovereign Swarm" in res_preview.text

    # 5. 404 on missing
    res_missing = client.get("/api/deliverables/non-existent-project/preview")
    assert res_missing.status_code == 404
