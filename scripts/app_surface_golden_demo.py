#!/usr/bin/env python3
"""App Surface Golden Demo Integration Check — GRO-3892

This script runs the final golden demo that proves the cohesive app surface:
clean run → dashboard readiness → plugin selection → policy preview → job start →
approval/rejection path → artifact/provenance → audit event →
export/publish-safe action → safe disconnect.

It uses FastAPI's TestClient to call gateway REST routes directly in a
fully-controlled, isolated temporary directory.
"""

from __future__ import annotations

import os
import sys
import shutil
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

# Ensure repo root is in python path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Setup temporary state paths under the designated target folder
TEST_DIR = Path("/tmp/agy-controlled-stage-proofs/20260715T205110Z/GRO-3892")
STATE_DIR = TEST_DIR / "state"

os.environ["PRISMATIC_STATE_DIR"] = str(STATE_DIR)
os.environ["PRISMATIC_PWP_INTEGRATION_STATE"] = str(STATE_DIR / "pwp_integration.json")
os.environ["PRISMATIC_PLUGIN_JOBS_STATE"] = str(STATE_DIR / "plugin_jobs.json")
os.environ["PRISMATIC_PLUGIN_ARTIFACTS_STATE"] = str(STATE_DIR / "plugin_artifacts.json")
os.environ["PRISMATIC_AGENT_REGISTRY"] = str(STATE_DIR / "agent_registry.json")

from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.pwp_integration import PWP_PLUGIN_ID

def log_step(name: str, status: str, details: str = "") -> None:
    box = "=" * 72
    print(box)
    print(f"STEP: {name}")
    print(f"STATUS: {status}")
    if details:
        print(f"DETAIL: {details}")
    print(box, flush=True)

def run_golden_demo() -> dict[str, Any]:
    client = TestClient(app)
    results = {}
    steps_log = []

    # -------------------------------------------------------------------------
    # 1. Clean Run
    # -------------------------------------------------------------------------
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    
    log_step("1. Clean Run", "PASS", f"Cleared and initialized state at {STATE_DIR}")
    steps_log.append({"step": "clean_run", "ok": True, "detail": f"State dir initialized: {STATE_DIR}"})

    # -------------------------------------------------------------------------
    # 2. Dashboard Readiness
    # -------------------------------------------------------------------------
    r_catalog = client.get("/api/plugins/catalog")
    r_gov = client.get("/api/plugins/governance")
    
    catalog_ok = r_catalog.status_code == 200 and r_catalog.json().get("count", 0) > 0
    gov_ok = r_gov.status_code == 200 and len(r_gov.json().get("plugins", [])) > 0
    
    db_ready = catalog_ok and gov_ok
    status = "PASS" if db_ready else "FAIL"
    log_step(
        "2. Dashboard Readiness", 
        status, 
        f"Catalog status: {r_catalog.status_code}, Gov status: {r_gov.status_code}"
    )
    steps_log.append({
        "step": "dashboard_readiness", 
        "ok": db_ready, 
        "detail": f"Catalog items: {r_catalog.json().get('count')}, Governance plugins: {len(r_gov.json().get('plugins', []))}"
    })
    if not db_ready:
        raise RuntimeError("Dashboard readiness failed.")

    # -------------------------------------------------------------------------
    # 3. Plugin Selection
    # -------------------------------------------------------------------------
    r_pwp_status = client.get("/api/pwp/status")
    pwp_initial_state = r_pwp_status.json().get("state")
    
    # Connect the plugin
    r_connect = client.post("/api/pwp/connect")
    pwp_connected = r_connect.status_code == 200 and r_connect.json().get("connected") is True
    
    status = "PASS" if pwp_connected else "FAIL"
    log_step(
        "3. Plugin Selection", 
        status, 
        f"Initial state: {pwp_initial_state} -> Connected state: {r_connect.json().get('state')}"
    )
    steps_log.append({
        "step": "plugin_selection", 
        "ok": pwp_connected, 
        "detail": f"Plugin {PWP_PLUGIN_ID} connected. Version: {r_connect.json().get('manifest', {}).get('version')}"
    })
    if not pwp_connected:
        raise RuntimeError("Plugin connection failed.")

    # -------------------------------------------------------------------------
    # 4. Policy Preview
    # -------------------------------------------------------------------------
    # Preview a job that requires approval
    r_preview_risk = client.post(
        "/api/plugins/policy/preview",
        json={
            "kind": "job_request",
            "plugin_name": PWP_PLUGIN_ID,
            "action": "pwp_reference_lifecycle_publish",
            "input_summary": {"mode": "production"}
        }
    )
    risk_decision = r_preview_risk.json().get("decision")
    
    # Preview a safe low-risk job
    r_preview_safe = client.post(
        "/api/plugins/policy/preview",
        json={
            "kind": "job_request",
            "plugin_name": "example-plugin",
            "action": "smoke_validate",
            "input_summary": {"mode": "local"}
        }
    )
    safe_decision = r_preview_safe.json().get("decision")
    
    policy_ok = risk_decision == "needs_approval" and safe_decision == "allow"
    status = "PASS" if policy_ok else "FAIL"
    log_step(
        "4. Policy Preview", 
        status, 
        f"Risk decision: {risk_decision} (expected: needs_approval), Safe decision: {safe_decision} (expected: allow)"
    )
    steps_log.append({
        "step": "policy_preview", 
        "ok": policy_ok, 
        "detail": f"Risk preview: {risk_decision}, Safe preview: {safe_decision}"
    })
    if not policy_ok:
        raise RuntimeError("Policy preview check failed.")

    # -------------------------------------------------------------------------
    # 5. Job Start (Blocked before approval)
    # -------------------------------------------------------------------------
    r_create_job = client.post(
        "/api/plugins/jobs",
        json={
            "plugin_name": PWP_PLUGIN_ID,
            "action": "pwp_reference_lifecycle_publish",
            "actor": "operator",
            "source": "golden-demo",
            "input_summary": {"demo": "full-path"},
            "approval_required": True
        }
    )
    job = r_create_job.json()
    job_id = job.get("job_id")
    
    # Try to start it immediately (should be blocked / HTTP 409 or needs_approval)
    r_start_blocked = client.post(f"/api/plugins/jobs/{job_id}/start", json={"actor": "system"})
    start_blocked_ok = r_start_blocked.status_code == 409 and r_start_blocked.json().get("policy_result", {}).get("decision") == "needs_approval"
    
    status = "PASS" if start_blocked_ok else "FAIL"
    log_step(
        "5. Job Start (Blocked)", 
        status, 
        f"Job status: {job.get('status')}, Start response: {r_start_blocked.status_code} ({r_start_blocked.json().get('policy_result', {}).get('reason')})"
    )
    steps_log.append({
        "step": "job_start_blocked", 
        "ok": start_blocked_ok, 
        "detail": f"Job {job_id} blocked as expected before approval."
    })
    if not start_blocked_ok:
        raise RuntimeError("Job start blocking check failed.")

    # -------------------------------------------------------------------------
    # 6. Approval & Job execution
    # -------------------------------------------------------------------------
    # Approve the job
    r_approve = client.post(f"/api/plugins/jobs/{job_id}/approve", json={"actor": "operator", "note": "Approved for golden demo"})
    approved = r_approve.json().get("approval_state") == "approved"
    
    # Start the job now
    r_start = client.post(f"/api/plugins/jobs/{job_id}/start", json={"actor": "system"})
    started = r_start.status_code == 200 and r_start.json().get("job", {}).get("status") == "running"
    
    # Complete the job
    r_complete = client.post(f"/api/plugins/jobs/{job_id}/status", json={"status": "completed", "actor": "system", "message": "Job finished successfully"})
    completed = r_complete.status_code == 200 and r_complete.json().get("status") == "completed"
    
    execution_ok = approved and started and completed
    status = "PASS" if execution_ok else "FAIL"
    log_step(
        "6. Approval & Job Execution", 
        status, 
        f"Approved: {approved}, Started: {started}, Completed: {completed}"
    )
    steps_log.append({
        "step": "job_execution", 
        "ok": execution_ok, 
        "detail": f"Job {job_id} approved, started, and completed."
    })
    if not execution_ok:
        raise RuntimeError("Job approval or execution failed.")

    # -------------------------------------------------------------------------
    # 7. Artifact/Provenance
    # -------------------------------------------------------------------------
    artifact_path = STATE_DIR / f"{job_id}.html"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("<html><body>Golden Demo Provenance Check</body></html>", encoding="utf-8")
    
    r_artifact = client.post(
        "/api/plugins/artifacts",
        json={
            "plugin_name": PWP_PLUGIN_ID,
            "job_id": job_id,
            "artifact_type": "text/html",
            "mime_type": "text/html",
            "path_or_url": str(artifact_path),
            "asset_id": f"pwp-golden-{job_id}",
            "metadata": {
                "title": "PWP Golden Demo Artifact",
                "requires_approval_before_publish": True
            },
            "provenance": {
                "source_plugin": PWP_PLUGIN_ID,
                "source_job": job_id,
                "generated_by": "app_surface_golden_demo",
                "registry": "universal-plugin-artifacts"
            },
            "input_summary": {"source": "controlled golden demo"},
            "provider_or_service": "pwp-local-ref",
            "approval_state": "pending",
            "publish_state": "draft"
        }
    )
    artifact = r_artifact.json()
    artifact_id = artifact.get("artifact_id")
    
    artifact_ok = r_artifact.status_code == 201 and artifact_id is not None
    status = "PASS" if artifact_ok else "FAIL"
    log_step(
        "7. Artifact & Provenance", 
        status, 
        f"Artifact ID: {artifact_id}, Provenance: {json.dumps(artifact.get('provenance'))}"
    )
    steps_log.append({
        "step": "artifact_provenance", 
        "ok": artifact_ok, 
        "detail": f"Artifact {artifact_id} registered with full provenance details."
    })
    if not artifact_ok:
        raise RuntimeError("Artifact registration failed.")

    # -------------------------------------------------------------------------
    # 8. Export/Publish-Safe Action (Blocked)
    # -------------------------------------------------------------------------
    # Try to publish before approval
    r_pub_blocked = client.post(f"/api/plugins/artifacts/{artifact_id}/publish-ready", json={"actor": "operator"})
    pub_blocked_ok = r_pub_blocked.status_code == 409 and r_pub_blocked.json().get("policy_result", {}).get("decision") != "allow"
    
    # Try to export before approval
    r_exp_blocked = client.post(f"/api/plugins/artifacts/{artifact_id}/export", json={"target": "s3://mybucket/publish", "actor": "operator"})
    exp_blocked_ok = r_exp_blocked.status_code == 409 and r_exp_blocked.json().get("policy_result", {}).get("decision") != "allow"
    
    blocked_actions_ok = pub_blocked_ok and exp_blocked_ok
    status = "PASS" if blocked_actions_ok else "FAIL"
    log_step(
        "8. Export & Publish Safety (Blocked)", 
        status, 
        f"Publish Blocked (409): {pub_blocked_ok}, Export Blocked (409): {exp_blocked_ok}"
    )
    steps_log.append({
        "step": "safety_gating_blocked", 
        "ok": blocked_actions_ok, 
        "detail": f"Publish-ready and Export requests blocked on pending artifact."
    })
    if not blocked_actions_ok:
        raise RuntimeError("Safety gating check failed.")

    # -------------------------------------------------------------------------
    # 9. Approval & Export/Publish-Safe Action (Allowed)
    # -------------------------------------------------------------------------
    # Approve the artifact
    r_art_approve = client.post(f"/api/plugins/artifacts/{artifact_id}/approve", json={"actor": "operator", "note": "Approved artifact for publish"})
    art_approved = r_art_approve.json().get("approval_state") == "approved"
    
    # Try to publish after approval
    r_pub = client.post(f"/api/plugins/artifacts/{artifact_id}/publish-ready", json={"actor": "operator"})
    published = r_pub.status_code == 200 and r_pub.json().get("publish_state") == "publish_ready"
    
    # Try to export after approval
    r_exp = client.post(f"/api/plugins/artifacts/{artifact_id}/export", json={"target": "s3://mybucket/publish", "actor": "operator"})
    exported = r_exp.status_code == 200 and r_exp.json().get("policy_result", {}).get("decision") == "allow"
    
    allowed_actions_ok = art_approved and published and exported
    status = "PASS" if allowed_actions_ok else "FAIL"
    log_step(
        "9. Export & Publish Safety (Allowed)", 
        status, 
        f"Approved: {art_approved}, Published: {published}, Exported: {exported}"
    )
    steps_log.append({
        "step": "safety_gating_allowed", 
        "ok": allowed_actions_ok, 
        "detail": f"Publish and Export permitted post operator approval."
    })
    if not allowed_actions_ok:
        raise RuntimeError("Safety gating post-approval failed.")

    # -------------------------------------------------------------------------
    # 10. Audit Event
    # -------------------------------------------------------------------------
    r_audit = client.get("/api/plugins/audit-events")
    events = r_audit.json().get("events", [])
    
    # Find expected milestones in the stream
    milestones = {e.get("event_type") for e in events}
    expected_milestones = {"job_created", "started", "completed", "artifact_created", "artifact_approved"}
    
    audit_ok = expected_milestones.issubset(milestones)
    status = "PASS" if audit_ok else "FAIL"
    log_step(
        "10. Audit Event Stream", 
        status, 
        f"Found event types: {milestones} (expected to contain: {expected_milestones})"
    )
    steps_log.append({
        "step": "audit_event_stream", 
        "ok": audit_ok, 
        "detail": f"Audit stream records {len(events)} events; matches expected integration milestones."
    })
    if not audit_ok:
        raise RuntimeError("Audit event stream validation failed.")

    # -------------------------------------------------------------------------
    # 11. Safe Disconnect
    # -------------------------------------------------------------------------
    r_disconnect = client.post("/api/pwp/disconnect")
    disconnected = r_disconnect.status_code == 200 and r_disconnect.json().get("state") == "disconnected"
    
    # Verify continuity: Job and artifact registries must survive disconnect
    r_jobs_check = client.get("/api/plugins/jobs")
    r_art_check = client.get("/api/plugins/artifacts")
    
    jobs_survived = any(j.get("job_id") == job_id for j in r_jobs_check.json().get("jobs", []))
    arts_survived = any(a.get("artifact_id") == artifact_id for a in r_art_check.json().get("artifacts", []))
    
    disconnect_ok = disconnected and jobs_survived and arts_survived
    status = "PASS" if disconnect_ok else "FAIL"
    log_step(
        "11. Safe Disconnect", 
        status, 
        f"Disconnected: {disconnected}, Jobs preserved: {jobs_survived}, Artifacts preserved: {arts_survived}"
    )
    steps_log.append({
        "step": "safe_disconnect", 
        "ok": disconnect_ok, 
        "detail": f"Plugin disconnected. Registry data preserved successfully."
    })
    if not disconnect_ok:
        raise RuntimeError("Safe disconnect check failed.")

    # Generate final verdict
    verdict = "PASS" if all(s["ok"] for s in steps_log) else "FAIL"
    results = {
        "verdict": verdict,
        "run_id": f"golden-demo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_dir": str(STATE_DIR),
        "steps": steps_log
    }
    
    # Save target proof files in target dir
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(TEST_DIR / "golden-demo-results.json", "w") as f:
        json.dump(results, f, indent=2)
        
    return results

if __name__ == "__main__":
    try:
        res = run_golden_demo()
        print("\n🎉 APP SURFACE GOLDEN DEMO VERDICT:", res["verdict"])
        if res["verdict"] == "PASS":
            print("APP_SURFACE_GOLDEN_DEMO_OK")
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ APP SURFACE GOLDEN DEMO FAILED: {e}", file=sys.stderr)
        sys.exit(1)
