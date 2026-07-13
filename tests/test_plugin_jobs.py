from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.plugin_jobs import PluginJobStore, evaluate_plugin_policy

PWP = "pwp-design-token-plugin"


def test_plugin_job_store_persists_policy_approval_events_and_artifacts(tmp_path: Path) -> None:
    store_path = tmp_path / "plugin_jobs.json"
    store = PluginJobStore(store_path)

    job = store.create_job(
        PWP,
        "theme_validate",
        actor="kai",
        source="pytest",
        input_summary={"theme": "demo", "api_token": "sk-should-redact"},
        operator_notes="dry-run validation",
    )

    assert job["job_id"].startswith("plugjob_")
    assert job["plugin_name"] == PWP
    assert job["status"] == "failed"
    assert job["approval_required"] is True
    assert job["approval_state"] == "pending"
    assert job["input_summary"]["api_token"] == "[REDACTED]"
    assert "raw secret" in job["error"]
    assert [event["event_type"] for event in job["events"]] == [
        "job_created",
        "policy_checked",
        "approval_required",
        "failed",
    ]

    safe_job = store.create_job(PWP, "theme_validate", actor="kai", source="pytest", input_summary={"theme": "demo"})
    assert safe_job["status"] == "needs_approval"
    approved = store.approve_job(safe_job["job_id"], actor="michael", note="approved dry run")
    assert approved is not None
    assert approved["status"] == "queued"
    assert approved["approval_state"] == "approved"
    running = store.update_status(safe_job["job_id"], "running", actor="worker", message="started")
    assert running is not None
    assert running["status"] == "running"
    with_artifact = store.append_event(
        safe_job["job_id"],
        "artifact_emitted",
        actor="worker",
        source="pwp",
        message="theme report emitted",
        artifact={
            "artifact_type": "application/x-prismatic-theme+json",
            "path_or_url": "state/pwp/theme-report.json",
            "metadata": {"summary": "ok"},
        },
    )
    assert with_artifact is not None
    assert len(with_artifact["artifact_ids"]) == 1
    assert with_artifact["artifacts"][0]["plugin_name"] == PWP
    completed = store.update_status(safe_job["job_id"], "completed", actor="worker")
    assert completed is not None
    assert completed["completed_at"]

    reloaded = PluginJobStore(store_path)
    assert reloaded.summary()["job_count"] == 2
    assert reloaded.summary()["artifact_count"] == 1
    reloaded_job = reloaded.get_job(safe_job["job_id"])
    assert reloaded_job is not None
    assert reloaded_job["events"][-1]["event_type"] == "completed"
    raw = json.loads(store_path.read_text())
    assert set(raw) == {"schema_version", "plugin_jobs", "plugin_job_events", "plugin_artifacts"}


def test_plugin_policy_blocks_unknown_plugin_and_marks_approvals() -> None:
    unknown = evaluate_plugin_policy("missing-plugin", "run")
    assert unknown["allowed"] is False
    assert "unknown plugin" in unknown["blockers"][0]

    publish = evaluate_plugin_policy(PWP, "publish_theme", input_summary={"theme": "demo"})
    assert publish["allowed"] is True
    assert publish["approval_required"] is True
    assert any("publish_theme" in reason for reason in publish["approval_reasons"])


def test_plugin_jobs_gateway_endpoints_are_durable(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "plugin_jobs.json"
    monkeypatch.setenv("PRISMATIC_PLUGIN_JOBS_STATE", str(state_path))
    client = TestClient(server.app)

    create = client.post(
        "/api/plugins/jobs",
        json={
            "plugin_name": PWP,
            "action": "theme_validate",
            "actor": "kai",
            "source": "pytest",
            "input_summary": {"theme": "demo"},
        },
    )
    assert create.status_code == 201
    job = create.json()
    assert job["status"] == "needs_approval"
    assert job["events"][0]["event_type"] == "job_created"

    list_response = client.get("/api/plugins/jobs")
    assert list_response.status_code == 200
    assert list_response.json()["summary"]["job_count"] == 1

    detail = client.get(f"/api/plugins/jobs/{job['job_id']}")
    assert detail.status_code == 200
    assert detail.json()["job_id"] == job["job_id"]

    approve = client.post(f"/api/plugins/jobs/{job['job_id']}/approve", json={"actor": "michael", "note": "ship it"})
    assert approve.status_code == 200
    assert approve.json()["approval_state"] == "approved"
    assert approve.json()["status"] == "queued"

    event = client.post(
        f"/api/plugins/jobs/{job['job_id']}/events",
        json={
            "event_type": "artifact_emitted",
            "actor": "worker",
            "message": "artifact registered",
            "artifact": {"artifact_type": "text/html", "path_or_url": "state/pwp/out.html"},
        },
    )
    assert event.status_code == 200
    assert event.json()["artifact_ids"]
    assert event.json()["artifacts"][0]["artifact_type"] == "text/html"

    completed = client.post(f"/api/plugins/jobs/{job['job_id']}/status", json={"status": "completed"})
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    governance = client.get("/api/plugins/governance")
    assert governance.status_code == 200
    assert governance.json()["jobs"]["job_count"] == 1
    assert governance.json()["jobs"]["artifact_count"] == 1

    assert state_path.exists()
    reloaded = PluginJobStore(state_path).get_job(job["job_id"])
    assert reloaded is not None
    assert reloaded["status"] == "completed"
    assert any(evt["event_type"] == "artifact_emitted" for evt in reloaded["events"])


def test_plugin_jobs_gateway_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRISMATIC_PLUGIN_JOBS_STATE", str(tmp_path / "plugin_jobs.json"))
    client = TestClient(server.app)

    bad_create = client.post("/api/plugins/jobs", json={"plugin_name": PWP})
    assert bad_create.status_code == 400

    missing = client.get("/api/plugins/jobs/plugjob_missing")
    assert missing.status_code == 404

    create = client.post("/api/plugins/jobs", json={"plugin_name": PWP, "action": "theme_validate"})
    job_id = create.json()["job_id"]
    bad_status = client.post(f"/api/plugins/jobs/{job_id}/status", json={"status": "bogus"})
    assert bad_status.status_code == 400


def test_dashboard_contains_plugin_job_audit_surface() -> None:
    html = (Path(__file__).resolve().parents[1] / "prismatic/gateway/templates/dashboard.html").read_text(encoding="utf-8")
    for marker in [
        "plugin-job-summary",
        "plugin-jobs-table",
        "/api/plugins/jobs",
        "renderPluginJobs",
        "Durable Plugin Jobs / Audit Trail",
        "approval_state",
    ]:
        assert marker in html
