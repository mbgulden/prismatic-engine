from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.plugin_artifacts import PluginArtifactStore
from prismatic.plugin_jobs import PluginJobStore
from prismatic.plugin_policy import (
    evaluate_artifact_action_policy,
    evaluate_job_request_policy,
    preview_policy,
)

SAFE_PLUGIN = "example-plugin"
PWP = "pwp-design-token-plugin"


def assert_policy_shape(policy: dict) -> None:
    assert set(
        [
            "allowed",
            "requires_approval",
            "decision",
            "reason",
            "blockers",
            "warnings",
            "approval_reasons",
            "checks",
            "evaluated_at",
        ]
    ).issubset(policy)
    assert policy["decision"] in {"allow", "needs_approval", "block"}
    assert isinstance(policy["checks"], list)
    assert all(
        {"name", "status", "details"}.issubset(check) for check in policy["checks"]
    )


def test_policy_evaluator_decision_shape_and_request_cases() -> None:
    safe = evaluate_job_request_policy(
        SAFE_PLUGIN, "smoke_validate", input_summary={"theme": "demo"}
    )
    assert_policy_shape(safe)
    assert safe["decision"] == "allow"
    assert safe["allowed"] is True

    approval = evaluate_job_request_policy(
        PWP, "publish_site", input_summary={"theme": "demo"}
    )
    assert_policy_shape(approval)
    assert approval["decision"] == "needs_approval"
    assert approval["requires_approval"] is True
    assert approval["approval_reasons"]

    unknown = evaluate_job_request_policy("missing-plugin", "run")
    assert_policy_shape(unknown)
    assert unknown["decision"] == "block"
    assert any("unknown plugin" in blocker for blocker in unknown["blockers"])

    secret = evaluate_job_request_policy(
        SAFE_PLUGIN, "smoke_validate", input_summary={"authorization": "Bearer demo"}
    )
    assert secret["decision"] == "block"
    assert secret["context"]["input_summary"]["authorization"] == "[REDACTED]"


def test_job_start_enforcement_and_status_bypass_blocked(tmp_path: Path) -> None:
    store = PluginJobStore(tmp_path / "plugin_jobs.json")
    safe_job = store.create_job(SAFE_PLUGIN, "smoke_validate", actor="kai")
    started, policy = store.start_job(safe_job["job_id"], actor="worker")
    assert started is not None and policy is not None
    assert policy["decision"] == "allow"
    assert started["status"] == "running"
    assert any(evt["event_type"] == "start_allowed" for evt in started["events"])

    pwp_job = store.create_job(PWP, "publish_site", actor="kai")
    blocked, blocked_policy = store.start_job(pwp_job["job_id"], actor="worker")
    assert blocked is not None and blocked_policy is not None
    assert blocked_policy["decision"] == "needs_approval"
    assert blocked["status"] == "needs_approval"
    assert any(evt["event_type"] == "start_blocked" for evt in blocked["events"])

    approved = store.approve_job(pwp_job["job_id"], actor="michael")
    assert approved is not None
    running = store.update_status(pwp_job["job_id"], "running", actor="worker")
    assert running is not None
    assert running["status"] == "running"
    assert running["policy_result"]["decision"] == "allow"

    rejected = store.create_job(PWP, "publish_site", actor="kai")
    rejected = store.reject_job(rejected["job_id"], actor="michael")
    assert rejected is not None
    cannot_restart = store.update_status(rejected["job_id"], "running", actor="worker")
    assert cannot_restart is not None
    assert cannot_restart["status"] == "failed"
    assert cannot_restart["policy_result"]["decision"] == "block"


def test_policy_preview_does_not_mutate_state(tmp_path: Path) -> None:
    store = PluginJobStore(tmp_path / "plugin_jobs.json")
    job = store.create_job(PWP, "publish_site")
    before = json.loads((tmp_path / "plugin_jobs.json").read_text(encoding="utf-8"))
    policy = preview_policy("job_start", job=store.get_job(job["job_id"]))
    after = json.loads((tmp_path / "plugin_jobs.json").read_text(encoding="utf-8"))
    assert policy["decision"] == "needs_approval"
    assert before == after


def test_artifact_publish_and_export_enforcement(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    store = PluginArtifactStore(state_dir / "plugin_artifacts.json")
    artifact_file = state_dir / "artifact.html"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_text("<h1>ok</h1>", encoding="utf-8")

    pending = store.create_artifact(
        plugin_name=SAFE_PLUGIN,
        artifact_type="text/html",
        path_or_url=str(artifact_file),
        provenance={"provider": "pytest"},
    )
    blocked_ready = store.mark_publish_ready(pending["artifact_id"], actor="kai")
    assert blocked_ready is not None
    assert blocked_ready["publish_state"] == "draft"
    assert blocked_ready["policy_result"]["decision"] == "needs_approval"
    assert blocked_ready["export_history"][-1]["event"] == "publish_ready_blocked"

    approved = store.set_approval(pending["artifact_id"], "approved", actor="michael")
    assert approved is not None
    ready = store.mark_publish_ready(pending["artifact_id"], actor="kai")
    assert ready is not None
    assert ready["publish_state"] == "publish_ready"
    assert ready["policy_result"]["decision"] == "allow"

    exported = store.add_export(pending["artifact_id"], target="preview", actor="kai")
    assert exported is not None
    assert exported["export_history"][-1]["event"] == "export"
    assert exported["export_history"][-1]["allowed"] is True

    rejected = store.create_artifact(
        plugin_name=SAFE_PLUGIN,
        artifact_type="text/html",
        provenance={"provider": "pytest"},
    )
    rejected = store.set_approval(rejected["artifact_id"], "rejected", actor="michael")
    assert rejected is not None
    blocked_export = store.add_export(
        rejected["artifact_id"], target="preview", actor="kai"
    )
    assert blocked_export is not None
    assert blocked_export["policy_result"]["decision"] == "block"
    assert blocked_export["export_history"][-1]["event"] == "export_blocked"

    missing_provenance = store.create_artifact(
        plugin_name=SAFE_PLUGIN, artifact_type="text/html", approval_state="approved"
    )
    policy = evaluate_artifact_action_policy(
        missing_provenance, "export", target="preview"
    )
    assert policy["decision"] == "block"
    assert any("provenance" in blocker for blocker in policy["blockers"])


def test_policy_gateway_endpoints(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_JOBS_STATE", str(state_dir / "plugin_jobs.json")
    )
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_ARTIFACTS_STATE", str(state_dir / "plugin_artifacts.json")
    )
    artifact_file = state_dir / "artifact.html"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_text("<h1>ok</h1>", encoding="utf-8")
    client = TestClient(server.app)

    preview = client.post(
        "/api/plugins/policy/preview",
        json={
            "kind": "job_request",
            "plugin_name": SAFE_PLUGIN,
            "action": "smoke_validate",
        },
    )
    assert preview.status_code == 200
    assert preview.json()["decision"] == "allow"

    pwp_job = client.post(
        "/api/plugins/jobs", json={"plugin_name": PWP, "action": "publish_site"}
    ).json()
    blocked_start = client.post(
        f"/api/plugins/jobs/{pwp_job['job_id']}/start", json={"actor": "worker"}
    )
    assert blocked_start.status_code == 409
    assert blocked_start.json()["policy_result"]["decision"] == "needs_approval"

    direct_running = client.post(
        f"/api/plugins/jobs/{pwp_job['job_id']}/status",
        json={"status": "running", "actor": "worker"},
    )
    assert direct_running.status_code == 409

    client.post(
        f"/api/plugins/jobs/{pwp_job['job_id']}/approve", json={"actor": "michael"}
    )
    allowed_start = client.post(
        f"/api/plugins/jobs/{pwp_job['job_id']}/start", json={"actor": "worker"}
    )
    assert allowed_start.status_code == 200
    assert allowed_start.json()["job"]["status"] == "running"

    artifact = client.post(
        "/api/plugins/artifacts",
        json={
            "plugin_name": SAFE_PLUGIN,
            "artifact_type": "text/html",
            "path_or_url": str(artifact_file),
            "provenance": {"provider": "pytest"},
        },
    ).json()
    blocked_ready = client.post(
        f"/api/plugins/artifacts/{artifact['artifact_id']}/publish-ready",
        json={"actor": "kai"},
    )
    assert blocked_ready.status_code == 409
    assert blocked_ready.json()["policy_result"]["decision"] == "needs_approval"

    client.post(
        f"/api/plugins/artifacts/{artifact['artifact_id']}/approve",
        json={"actor": "michael"},
    )
    ready = client.post(
        f"/api/plugins/artifacts/{artifact['artifact_id']}/publish-ready",
        json={"actor": "kai"},
    )
    assert ready.status_code == 200
    exported = client.post(
        f"/api/plugins/artifacts/{artifact['artifact_id']}/export",
        json={"target": "preview", "actor": "kai"},
    )
    assert exported.status_code == 200
    assert exported.json()["policy_result"]["decision"] == "allow"

    export_preview = client.post(
        "/api/plugins/policy/preview",
        json={
            "kind": "artifact_export",
            "artifact_id": artifact["artifact_id"],
            "target": "preview",
        },
    )
    assert export_preview.status_code == 200
    assert export_preview.json()["decision"] == "allow"


def test_dashboard_contains_policy_enforcement_surface() -> None:
    html = (
        Path(__file__).resolve().parents[1]
        / "prismatic/gateway/templates/dashboard.html"
    ).read_text(encoding="utf-8")
    for marker in [
        "plugin-policy-summary",
        "plugin-policy-decision",
        "renderPluginPolicy",
        "Policy Enforcement",
        "blocked_reason",
        "/api/plugins/policy/preview",
    ]:
        assert marker in html
