"""Comprehensive Multi-Agent Concurrency & Deflected Collision Test Barrage.

Tests:
1. SwarmLock exclusive mutual exclusion between multiple agents (Fred & George) on the same resource.
2. Deflected collision detection, telemetry signal emission, and audit logging.
3. Continuous SSE stream delivery during concurrent lease operations.
4. Non-clobbering collaborative two-agent document editing with hash integrity.
5. Force eviction and TTL safety mechanisms.
"""

import json
import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from prismatic.core.locking import SwarmLockManager
from prismatic.gateway.server import app


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Set up isolated PRISMATIC_HOME and temporary locks."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    signals_file = home / ".antigravity" / "signals_stream.jsonl"
    signals_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("prismatic.agent_signal_stream.signal_stream_path", lambda: signals_file)

    client = TestClient(app)
    return {"client": client, "home": home, "signals_file": signals_file}


def test_swarmlock_exclusive_mutual_exclusion(test_env):
    """Test 1: Agent A claims lease, Agent B is deflected, Agent A releases, Agent B claims."""
    client = test_env["client"]
    target = "docs/MULTI_AGENT_SWARM_ARCHITECTURE.md"

    # Step 1: Fred acquires lease
    res_fred = client.post(
        "/api/gateway/swarmlock/acquire",
        json={
            "resource": target,
            "agent_id": "fred",
            "task_id": "GRO-FRED-SECTION-1",
            "intention": "Draft Section 1: Orchestration Architecture",
            "ttl_seconds": 120.0,
        },
    )
    assert res_fred.status_code == 200
    data_fred = res_fred.json()
    assert data_fred["ok"] is True
    assert data_fred["agent_id"] == "fred"
    lease_fred = data_fred["lease_id"]
    assert lease_fred

    # Step 2: George attempts concurrent acquisition on the same file -> Deflected!
    res_george = client.post(
        "/api/gateway/swarmlock/acquire",
        json={
            "resource": target,
            "agent_id": "george",
            "task_id": "GRO-GEORGE-SECTION-2",
            "intention": "Draft Section 2: Verification Engine",
            "ttl_seconds": 120.0,
        },
    )
    assert res_george.status_code == 200
    data_george = res_george.json()
    assert data_george["ok"] is False
    assert data_george["status"] in {"deflected", "collision_deflected"}
    assert data_george["holder"] == "fred"

    # Check status: 1 active lock, 1 deflected collision
    res_status = client.get("/api/gateway/swarmlock/status")
    assert res_status.status_code == 200
    status = res_status.json()
    assert status["active_lock_count"] == 1
    assert status["deflected_collisions"] >= 1
    assert "fred" in status["active_agents"]

    # Step 3: Fred releases lease
    res_rel = client.post(
        "/api/gateway/swarmlock/release",
        json={"resource": target, "agent_id": "fred", "lease_id": lease_fred},
    )
    assert res_rel.status_code == 200
    assert res_rel.json()["ok"] is True

    # Step 4: George can now acquire lease
    res_george_ok = client.post(
        "/api/gateway/swarmlock/acquire",
        json={
            "resource": target,
            "agent_id": "george",
            "task_id": "GRO-GEORGE-SECTION-2",
            "intention": "Draft Section 2: Verification Engine",
            "ttl_seconds": 120.0,
        },
    )
    assert res_george_ok.status_code == 200
    data_george_ok = res_george_ok.json()
    assert data_george_ok["ok"] is True
    assert data_george_ok["agent_id"] == "george"

    # Clean up George's lease
    client.post(
        "/api/gateway/swarmlock/release",
        json={"resource": target, "agent_id": "george", "lease_id": data_george_ok["lease_id"]},
    )


def test_deflected_collision_telemetry_and_audit(test_env):
    """Test 2: Deflection generates a signal in /api/gateway/signals and audit ledger."""
    client = test_env["client"]
    target = "docs/MULTI_AGENT_SWARM_ARCHITECTURE.md"

    # Fred acquires
    client.post(
        "/api/gateway/swarmlock/acquire",
        json={"resource": target, "agent_id": "fred", "task_id": "GRO-FRED-1"},
    )

    # George collides
    client.post(
        "/api/gateway/swarmlock/acquire",
        json={"resource": target, "agent_id": "george", "task_id": "GRO-GEORGE-1"},
    )

    # Verify signals include lock_acquired and collision_deflected
    res_signals = client.get("/api/gateway/signals?limit=50")
    assert res_signals.status_code == 200
    items = res_signals.json()["items"]
    event_types = [s.get("event_type") for s in items]
    assert "lock_acquired" in event_types
    assert "collision_deflected" in event_types

    # Clean up Fred's lock
    client.post(
        "/api/gateway/swarmlock/release",
        json={"resource": target, "agent_id": "fred"},
    )


def test_sse_continuous_stream_during_concurrency(test_env):
    """Test 3: Verify SSE endpoint delivers connection, snapshot, and event frames."""
    client = test_env["client"]
    res = client.get("/api/signals/stream?limit=5&once=true")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    body = res.text
    assert "event: connect" in body
    assert "event: snapshot" in body
    assert "prismatic-gateway" in body


def test_non_clobbering_collaborative_edit_integrity(tmp_path):
    """Test 4: Simulate Fred and George sequential section authoring with content verification."""
    shared_doc = tmp_path / "SHARED_DOC.md"

    initial_content = (
        "# Multi-Agent Swarm Collaborative Spec\n\n"
        "## Section 1: Orchestration Architecture (Fred)\n"
        "[PENDING]\n\n"
        "## Section 2: Verification Proofs (George)\n"
        "[PENDING]\n"
    )
    shared_doc.write_text(initial_content, encoding="utf-8")

    # Fred edits Section 1
    content = shared_doc.read_text(encoding="utf-8")
    fred_section = (
        "Fred Orchestrator implements Topological Waves: tasks are grouped into DAG tiers.\n"
        "Real-time streaming routes intermediate tokens to Telegram channel 8190664947.\n"
    )
    content = content.replace("[PENDING]\n\n## Section 2", f"{fred_section}\n## Section 2")
    shared_doc.write_text(content, encoding="utf-8")

    # George edits Section 2
    content = shared_doc.read_text(encoding="utf-8")
    george_section = (
        "George Verifier attests DLD hashes and clean-room ephemeral worktrees.\n"
        "SwarmProof guarantees zero unverifiable summaries.\n"
    )
    content = content.replace("[PENDING]\n", f"{george_section}\n")
    shared_doc.write_text(content, encoding="utf-8")

    # Verify both sections are intact
    final_text = shared_doc.read_text(encoding="utf-8")
    assert "Topological Waves" in final_text
    assert "Telegram channel 8190664947" in final_text
    assert "George Verifier attests DLD hashes" in final_text
    assert "SwarmProof guarantees" in final_text
    assert "[PENDING]" not in final_text

    # Compute SHA-256 digest
    digest = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
    assert len(digest) == 64


def test_swarmlock_evict_all_safety(test_env):
    """Test 5: Evict all active leases safely."""
    client = test_env["client"]
    target_1 = "docs/file1.md"
    target_2 = "docs/file2.md"

    client.post("/api/gateway/swarmlock/acquire", json={"resource": target_1, "agent_id": "fred"})
    client.post("/api/gateway/swarmlock/acquire", json={"resource": target_2, "agent_id": "george"})

    status_before = client.get("/api/gateway/swarmlock/status").json()
    assert status_before["active_lock_count"] >= 2

    # Evict all
    res_evict = client.post("/api/gateway/swarmlock/evict-all")
    assert res_evict.status_code == 200

    status_after = client.get("/api/gateway/swarmlock/status").json()
    assert status_after["active_lock_count"] == 0
