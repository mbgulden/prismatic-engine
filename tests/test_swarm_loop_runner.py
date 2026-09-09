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
    prompt = "Launch high-converting self-serve rental booking platform for Oahu kayaks at 134B Hamakua Dr (Kailua, HI) to replace activeoahu.com"

    result = runner.run(
        prompt=prompt,
        target_domain="activeoahu.growthwebdev.com",
        voice="kai",
    )

    assert result.status == "COMPLETED"
    assert result.task_id == "GRO-TEST-LOOP"
    assert result.archetype == "web_property"
    assert len(result.contracts) == 4
    assert result.deliverable is not None
    assert result.deliverable["project_slug"] == "active-oahu"
    assert "134B Hamakua Dr" in result.deliverable["location"]

    # Assert all major steps were logged
    logged_steps = [s["step"] for s in result.execution_steps]
    assert "DECOMPOSE" in logged_steps
    assert "DISPATCH" in logged_steps
    assert "EXECUTE" in logged_steps
    assert "REVIEW" in logged_steps
    assert "INTEGRATE" in logged_steps


def test_deliverables_persistence():
    delivs = load_all_deliverables()
    assert len(delivs) >= 1
    assert any(d["project_slug"] == "active-oahu" for d in delivs)

    oahu = get_deliverable_by_slug("active-oahu")
    assert oahu is not None
    assert oahu["title"] == "Active Oahu Rentals & Beach Gear"
    assert "134B Hamakua Dr" in oahu["location"]


def test_gateway_swarm_decompose_api():
    client = TestClient(app)

    res = client.post(
        "/api/gateway/swarm/decompose",
        json={"prompt": "Launch high-converting self-serve rental booking platform for Oahu kayaks at 134B Hamakua Dr"},
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
            "prompt": "Launch high-converting self-serve rental booking platform for Oahu kayaks at 134B Hamakua Dr",
            "target_domain": "activeoahu.growthwebdev.com",
            "voice": "kai",
            "task_id": "GRO-4854",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    result = data["result"]
    assert result["status"] == "COMPLETED"
    assert result["deliverable"]["project_slug"] == "active-oahu"


def test_gateway_deliverables_list_and_preview_api():
    client = TestClient(app)

    # 1. List deliverables
    res_list = client.get("/api/gateway/deliverables")
    assert res_list.status_code == 200
    data_list = res_list.json()
    assert data_list["ok"] is True
    assert data_list["total"] >= 1

    # 2. Get single deliverable
    res_single = client.get("/api/gateway/deliverables/active-oahu")
    assert res_single.status_code == 200
    data_single = res_single.json()
    assert data_single["deliverable"]["project_slug"] == "active-oahu"

    # 3. Live HTML Preview
    res_preview = client.get("/api/deliverables/active-oahu/preview")
    assert res_preview.status_code == 200
    assert "text/html" in res_preview.headers["content-type"]
    assert "Active Oahu Rentals" in res_preview.text
    assert "134B Hamakua Dr" in res_preview.text
    assert "SportsActivityLocation" in res_preview.text

    # 4. 404 on missing
    res_missing = client.get("/api/deliverables/non-existent-project/preview")
    assert res_missing.status_code == 404
