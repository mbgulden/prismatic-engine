from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismatic.agent_governance_status import MARKER, build_agent_governance_status
from prismatic.gateway import server


@dataclass
class FakeRun:
    run_id: str
    issue_id: str
    agent_name: str
    status: str
    started_at: str
    completed_at: str | None = None
    output_path: str | None = None
    verification_status: str | None = None
    error_message: str | None = None


class FakeRunStore:
    def __init__(self, runs):
        self.runs = runs
        self.reloaded = False

    def reload(self):
        self.reloaded = True

    def get_recent_runs(self, limit=200):
        return self.runs[:limit]


def test_agent_governance_read_model_surfaces_policy_packet_and_proof_links():
    payload = build_agent_governance_status(
        agents=("kai", "fred"),
        run_records=[
            FakeRun(
                run_id="run-kai",
                issue_id="GRO-4100",
                agent_name="kai",
                status="completed",
                started_at="2026-07-19T22:00:00+00:00",
                completed_at="2026-07-19T22:03:00+00:00",
                output_path="/tmp/kai-result.md",
                verification_status="verified",
            )
        ],
        registry={"fred": {"task_id": "GRO-4101", "status": "running"}},
        completed_work_rows=[
            {
                "agent": "kai",
                "updated_at": "2026-07-19T22:05:00+00:00",
                "source_path": "/tmp/kai-artifact",
                "integration_classification": "pass_ready_for_review",
                "proof_result": "PASS",
                "proof_marker": "KAI_PACKET_OK",
                "packet": {
                    "agent": "kai",
                    "issue_identifier": "GRO-4100",
                    "proof": {"log": "/tmp/kai-proof.log", "marker": "KAI_PACKET_OK"},
                },
            }
        ],
    )

    assert payload["marker"] == MARKER
    assert payload["marker"] == "AGENT_GOVERNANCE_CORE_STATE_MODEL_OK"
    assert (
        payload["ui_api_contract_marker"] == "GOVERNANCE_DASHBOARD_UI_API_CONTRACT_OK"
    )
    assert payload["contract_version"] == "governance-dashboard-ui-api-contract-v1"
    assert payload["ui_api_contract"]["fields"] == [
        "lane_status",
        "task_detail",
        "proof_links",
        "audit_events",
        "approval_gates",
        "side_effect_policy",
        "durability_status",
        "portability_readiness",
        "readiness_fields",
        "source_labels",
    ]
    assert payload["ui_api_contract"]["mock_data_allowed"] is False
    assert payload["ui_api_contract"]["secret_values_allowed"] is False
    assert payload["side_effect_policy"] == {
        "merge": False,
        "deploy": False,
        "linear_writeback": False,
        "github_pr_create": False,
        "auto_merge": False,
        "bulk_dispatch": False,
        "production_restart": False,
    }
    kai = next(agent for agent in payload["agents"] if agent["agent"] == "kai")
    assert kai["lane_status"] == "policy_guarded"
    assert kai["current_task"] == "GRO-4100"
    assert kai["task_detail"]["current_task"] == "GRO-4100"
    assert kai["audit_result"] == "pass_ready_for_review"
    assert kai["audit_events"][0]["event_type"] == "agent_run_completed"
    assert kai["audit_events"][1]["event_type"] == "completed_work_packet_seen"
    assert kai["approval_gates"]
    assert all(gate["state"] == "blocked_by_default" for gate in kai["approval_gates"])
    assert kai["durability_status"]["state"] == "durable_packet_seen"
    assert kai["portability_readiness"]["state"] == "portable_or_not_applicable"
    assert kai["readiness_fields"] == {
        "packet_seen": True,
        "run_seen": True,
        "approval_required_for_real_effects": True,
        "source_label": payload["source"],
    }
    assert "source:dashboard_read_model_contract" in kai["source_labels"]
    assert kai["proof_result"] == "PASS"
    assert any(link["href"] == "/tmp/kai-proof.log" for link in kai["proof_links"])
    assert kai["interim_source_label"].startswith("interim-source:")


def test_gateway_agent_governance_status_endpoint_uses_dashboard_inputs(
    monkeypatch, tmp_path: Path
):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"fred": {"task_id": "GRO-4101", "status": "running"}})
    )
    monkeypatch.setenv("PRISMATIC_AGENT_REGISTRY", str(registry))
    fake_store = FakeRunStore(
        [
            FakeRun(
                run_id="run-fred",
                issue_id="GRO-4101",
                agent_name="fred",
                status="running",
                started_at="2026-07-19T22:00:00+00:00",
                output_path="/tmp/fred-result.md",
            )
        ]
    )
    monkeypatch.setattr(server, "_run_store", fake_store)

    response = TestClient(server.app).get("/api/gateway/agents/governance-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["marker"] == MARKER
    fred = next(agent for agent in payload["agents"] if agent["agent"] == "fred")
    assert fred["current_task"] == "GRO-4101"
    assert fred["audit_result"] == "run_running"
    assert fred["side_effect_policy"]["merge"] is False
    assert fred["task_detail"]["registry_status"] == "running"
    assert (
        fred["approval_gates"][0]["gate"]
        == "operator_approval_required_before_real_side_effects"
    )
    assert fred["durability_status"]["state"] == "run_record_only"
    assert (
        payload["ui_api_contract"]["route"] == "/api/gateway/agents/governance-status"
    )
    assert (
        payload["portability_readiness"]["host_local_links_are_evidence_only"] is True
    )
    assert "no deploy" in payload["non_claims"]
    assert fake_store.reloaded is True


@pytest.mark.parametrize(
    ("unsafe_value", "dummy_secret"),
    [
        ("https://user:dummy-password@example.com/proof.log", "dummy-password"),
        ("https://example.com/proof.log?token=dummy-token", "dummy-token"),
        ("Bearer dummy-bearer-token", "dummy-bearer-token"),
        ("Authorization: Bearer dummy-auth-token", "dummy-auth-token"),
        ("/tmp/password/dummy-proof.log", "password"),
        ("/tmp/dummy-secret-proof.log", "dummy-secret"),
        (
            "-----BEGIN "
            + "PRIVATE KEY-----\ndummy-key\n-----END "
            + "PRIVATE KEY-----",
            "dummy-key",
        ),
        ("/tmp/proof\nlog", "proof\\nlog"),
        ("javascript:alert('dummy-js-token')", "dummy-js-token"),
        ("../dummy-traversal-proof.log", "dummy-traversal"),
    ],
)
def test_agent_governance_proof_links_drop_unsafe_locators_without_echoing_secret(
    unsafe_value, dummy_secret
):
    payload = build_agent_governance_status(
        agents=("kai",),
        completed_work_rows=[
            {
                "agent": "kai",
                "source_path": unsafe_value,
                "packet": {
                    "agent": "kai",
                    "issue_identifier": "GRO-4100",
                    "proof": {"log": unsafe_value},
                },
            }
        ],
    )

    serialized = json.dumps(payload)
    kai = payload["agents"][0]
    hrefs = [link["href"] for link in kai["proof_links"]]
    assert unsafe_value not in hrefs
    assert dummy_secret not in serialized
    assert all("source_path" != link["label"] for link in kai["proof_links"])
    assert all("proof_log" != link["label"] for link in kai["proof_links"])


def test_agent_governance_proof_links_preserve_safe_local_path_url_and_issue_link():
    payload = build_agent_governance_status(
        agents=("kai",),
        run_records=[
            FakeRun(
                run_id="run-kai",
                issue_id="GRO-4100",
                agent_name="kai",
                status="completed",
                started_at="2026-07-19T22:00:00+00:00",
                output_path="/tmp/kai-result.md",
            )
        ],
        completed_work_rows=[
            {
                "agent": "kai",
                "proof_log": "https://proofs.example.com/proof.log?utm_source=noise#fragment",
                "source_path": "/tmp/kai-artifact/proof.log",
                "packet": {"agent": "kai", "issue_identifier": "GRO-4100"},
            }
        ],
    )

    hrefs = [link["href"] for link in payload["agents"][0]["proof_links"]]
    assert "https://proofs.example.com/proof.log" in hrefs
    assert "https://proofs.example.com/proof.log?utm_source=noise#fragment" not in hrefs
    assert "/tmp/kai-artifact/proof.log" in hrefs
    assert "/tmp/kai-result.md" in hrefs
    assert "https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4100" in hrefs


def test_gateway_agent_governance_status_endpoint_serialization_drops_dummy_secrets(
    monkeypatch, tmp_path: Path
):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"kai": {"task_id": "GRO-4100", "status": "completed"}})
    )
    monkeypatch.setenv("PRISMATIC_AGENT_REGISTRY", str(registry))
    fake_store = FakeRunStore(
        [
            FakeRun(
                run_id="run-kai",
                issue_id="GRO-4100",
                agent_name="kai",
                status="completed",
                started_at="2026-07-19T22:00:00+00:00",
                output_path="/tmp/kai-result.md",
            )
        ]
    )
    monkeypatch.setattr(server, "_run_store", fake_store)

    import prismatic.agent_governance_status as governance_status

    monkeypatch.setattr(
        governance_status,
        "_load_completed_work",
        lambda limit=100: [
            {
                "agent": "kai",
                "source_path": "/tmp/password/dummy-secret-source.log",
                "proof_log": "https://example.com/proof.log?api_key=dummy-api-key",
                "packet": {
                    "agent": "kai",
                    "issue_identifier": "GRO-4100",
                    "proof": {"log": "Authorization: Bearer dummy-auth-token"},
                },
            }
        ],
    )

    response = TestClient(server.app).get("/api/gateway/agents/governance-status")

    assert response.status_code == 200
    body = response.text
    assert "dummy-secret-source" not in body
    assert "dummy-api-key" not in body
    assert "dummy-auth-token" not in body
    kai = next(agent for agent in response.json()["agents"] if agent["agent"] == "kai")
    assert all(
        link["label"] not in {"proof_log", "source_path"} for link in kai["proof_links"]
    )
    assert "https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4100" in [
        link["href"] for link in kai["proof_links"]
    ]
