from __future__ import annotations

import importlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.agent_packet_normalizer import RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
from prismatic.agent_raw_output_queue import RawAgentOutputStore


_GATE_ACCEPTED_SOURCE_PATH = f"{'/home'}/ubuntu/work/agy-gro-3952-proof"


def valid_packet(**overrides):
    packet = {
        "agent": "agy",
        "source_branch": "feature/gro-3952-proof",
        "source_path": _GATE_ACCEPTED_SOURCE_PATH,
        "base_branch": "main",
        "changed_files": ["prismatic/agent_raw_output_queue.py"],
        "result_summary": "raw output queue implemented",
        "proof": {
            "command": "python -m pytest tests/test_agent_raw_output_queue_api.py",
            "result": "PASS",
            "log": "/tmp/fred-raw-agent-output-repair-queue-api-verify.log",
            "scope": "raw output queue API",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
            "non_claims": [
                "auto_rerun_enabled",
                "auto_merge_enabled",
                "production_deploy",
            ],
        },
        "lane_scope": {
            "allowed_paths": ["prismatic/"],
            "touched_paths": ["prismatic/agent_raw_output_queue.py"],
        },
    }
    packet.update(overrides)
    return packet


def client_for_raw_queue(
    tmp_path: Path, monkeypatch
) -> tuple[TestClient, RawAgentOutputStore]:
    db_path = tmp_path / "agent_raw_output_queue.sqlite3"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_DB", str(db_path))
    import prismatic.gateway.server as server

    server = importlib.reload(server)
    return TestClient(server.app), RawAgentOutputStore(db_path)


def test_raw_output_list_and_detail_routes_expose_real_queue_rows(
    tmp_path: Path, monkeypatch
) -> None:
    client, store = client_for_raw_queue(tmp_path, monkeypatch)
    accepted = store.persist(
        raw_text=json.dumps(valid_packet()),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )
    repairable_packet = valid_packet()
    repairable_packet["proof"].pop("log")
    repairable = store.persist(
        raw_text=json.dumps(repairable_packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    listing = client.get("/api/gateway/agents/raw-output?limit=10")
    detail = client.get(f"/api/agents/raw-output/{repairable.raw_output_id}")

    assert listing.status_code == 200
    payload = listing.json()
    assert payload["marker"] == RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
    assert payload["count"] == 2
    assert payload["counts"]["accepted"] == 1
    assert payload["counts"]["repairable"] == 1
    assert payload["non_claims"] == {
        "demo_fixture_rows": False,
        "auto_repair_success": False,
        "auto_rerun_enabled": False,
        "auto_merge_enabled": False,
        "production_deploy": False,
    }
    assert {row["raw_output_id"] for row in payload["raw_outputs"]} == {
        accepted.raw_output_id,
        repairable.raw_output_id,
    }

    assert detail.status_code == 200
    assert detail.json()["raw_output"]["raw_output_id"] == repairable.raw_output_id
    assert detail.json()["raw_output"]["repair_hint"] == "missing_proof_log"
    assert detail.json()["raw_output"]["canonical_packet_id"] is None


def test_raw_output_repair_preview_route_is_read_only(
    tmp_path: Path, monkeypatch
) -> None:
    client, store = client_for_raw_queue(tmp_path, monkeypatch)
    packet = valid_packet()
    packet["proof"].pop("log")
    row = store.persist(
        raw_text=json.dumps(packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    response = client.post(
        f"/api/gateway/agents/raw-output/{row.raw_output_id}/repair-preview"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["marker"] == RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
    assert payload["status"] == "preview"
    assert payload["normalization_status"] == "rejected_repairable"
    assert payload["repair_hint"] == "missing_proof_log"
    assert payload["would_auto_repair"] is False
    assert payload["would_auto_rerun"] is False
    assert payload["raw_output"]["canonical_packet_id"] is None
    assert store.get(row.raw_output_id).rerun_requested is False


def test_raw_output_mark_rerun_requested_route_is_operator_only_and_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    client, store = client_for_raw_queue(tmp_path, monkeypatch)
    packet = valid_packet()
    packet.pop("source_path")
    row = store.persist(
        raw_text=json.dumps(packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    response = client.post(
        f"/api/agents/raw-output/{row.raw_output_id}/mark-rerun-requested"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rerun_requested"
    assert payload["marker"] == RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
    assert payload["auto_rerun_enabled"] is False
    assert payload["raw_output"]["rerun_requested"] is True
    assert payload["raw_output"]["rerun_requested_at"]
    assert store.get(row.raw_output_id).rerun_requested is True


def test_raw_output_mark_rerun_requested_rejects_repairable_rows(
    tmp_path: Path, monkeypatch
) -> None:
    client, store = client_for_raw_queue(tmp_path, monkeypatch)
    packet = valid_packet()
    packet["proof"].pop("log")
    row = store.persist(
        raw_text=json.dumps(packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    response = client.post(
        f"/api/gateway/agents/raw-output/{row.raw_output_id}/mark-rerun-requested"
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "rerun is not allowed for this raw output classification"
    )
    assert store.get(row.raw_output_id).rerun_requested is False


def test_raw_output_routes_return_404_for_missing_ids(
    tmp_path: Path, monkeypatch
) -> None:
    client, _store = client_for_raw_queue(tmp_path, monkeypatch)

    assert client.get("/api/agents/raw-output/raw_missing").status_code == 404
    assert (
        client.post("/api/agents/raw-output/raw_missing/repair-preview").status_code
        == 404
    )
    assert (
        client.post(
            "/api/agents/raw-output/raw_missing/mark-rerun-requested"
        ).status_code
        == 404
    )
