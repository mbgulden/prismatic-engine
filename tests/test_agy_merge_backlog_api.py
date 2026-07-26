from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.completed_work_gate import demo_completed_work_packet
from prismatic.gateway import server


def completed_work_text(marker: str = "AGY_API_LOG_PACKET_OK") -> str:
    return f"""
COMMAND=$HOME/.prismatic/venv_stable/bin/python -m pytest tests/test_agy_completed_work.py -q
RESULT=PASS
LOG=/tmp/agy-api-log-packet-proof.log
SCOPE=completed-work API text ingestion gate
AD_HOC_OR_CANONICAL=ad-hoc targeted
NOT_CLAIMING=production_deployed,auto_merge_enabled,real_github_pr_created,real_Linear_writeback_posted,bulk_agent_dispatch,overnight_autopilot
MARKER={marker}
AGENT=agy
ISSUE_IDENTIFIER=GRO-AGY-API-1
SOURCE_BRANCH=feature/agy-api-log-packet
SOURCE_PATH={Path.home() / ".prismatic" / "agy-result-packets" / "GRO-AGY-API-1"}
BASE_BRANCH=main
CHANGED_FILES=prismatic/agy_completed_work.py,tests/test_agy_completed_work.py
RESULT_SUMMARY=AGY compact log packet ingested through API
VERIFICATION_LANE=backend-api
""".strip()


def packet():
    p = deepcopy(demo_completed_work_packet())
    p["issue_identifier"] = "GRO-3837"
    p["source_branch"] = "feature/agy/GRO-3837-clean-pr"
    p["base_branch"] = "origin/main"
    p["verification_lane"] = "backend-api"
    p["changed_files"] = [
        "prismatic/agy_merge_backlog.py",
        "tests/test_agy_merge_backlog.py",
    ]
    p["proof"].update(
        {
            "command": "python3 -m pytest -q tests/test_agy_merge_backlog.py tests/test_agy_merge_backlog_api.py",
            "scope": "backend-api verification proof",
            "log": "/tmp/fred-agy-clean-pr-verification-gate-verify.log",
            "marker": "AGY_PR_VERIFICATION_GATE_OK",
            "non_claims": ["auto_merge", "production_deploy"],
        }
    )
    return p


VALID_SOURCE_SHA = "c" * 40
VALID_BASE_SHA = "d" * 40


def _retained_inputs(tmp_path):
    source = tmp_path / "RESULT.md"
    source.write_text(
        "RESULT=PASS\nMARKER=AGY_PR_VERIFICATION_GATE_OK", encoding="utf-8"
    )
    proof = tmp_path / "proof.log"
    proof.write_text("pytest passed", encoding="utf-8")
    return source, proof


def retained_packet(tmp_path, *, source_commit_sha: str | None = VALID_SOURCE_SHA):
    source, proof = _retained_inputs(tmp_path)
    p = packet()
    p["source_path"] = str(source)
    p["source_commit_sha"] = source_commit_sha
    p["base_commit_sha"] = VALID_BASE_SHA
    p["proof"]["log"] = str(proof)
    return p


def accepted_native_receipt(row_payload):
    task_id = str((row_payload.get("packet") or {}).get("issue_identifier") or "")
    candidate_sha = str(
        (row_payload.get("evidence_retention") or {}).get("source_commit_sha") or ""
    )
    return {
        "status": "accepted",
        "reason": None,
        "authoritative": True,
        "receipt_id": "pnvr-" + "a" * 64,
        "receipt_sha256": "b" * 64,
        "repository_id": "repo-prismatic-engine",
        "task_id": task_id,
        "base_sha": VALID_BASE_SHA,
        "base_tree_sha": "e" * 40,
        "candidate_sha": candidate_sha,
        "tree_sha": "f" * 40,
        "checkout_clean_state": {
            "status": "clean",
            "observed_at": "2026-07-26T00:00:00+00:00",
            "porcelain_sha256": "sha256:" + "0" * 64,
        },
        "merge_authorized": True,
        "deploy_authorized": True,
        "hosted_signals_required": False,
    }


@pytest.fixture(autouse=True)
def _accepted_native_authority(monkeypatch):
    monkeypatch.setattr(
        "prismatic.agy_promotion_ledger._native_acceptance_for",
        accepted_native_receipt,
    )
    monkeypatch.setattr(
        "prismatic.agy_operator_action_approval._authoritative_receipt_matches",
        lambda _expected: True,
    )


def seed(monkeypatch, tmp_path):
    db = tmp_path / "completed_work.db"
    executor_runs = tmp_path / "executor-runs.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(executor_runs))
    monkeypatch.setattr(
        "prismatic.agy_promotion_ledger._native_acceptance_for",
        accepted_native_receipt,
    )
    monkeypatch.setattr(
        "prismatic.agy_operator_action_approval._authoritative_receipt_matches",
        lambda _expected: True,
    )
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    return row


def test_completed_work_api_ingests_text_and_lists_bridge_payload(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    client = TestClient(server.app)

    ingested = client.post(
        "/api/gateway/agy/completed-work/ingest",
        json={"completed_work_text": completed_work_text()},
    )
    assert ingested.status_code == 200
    completed = ingested.json()["completed_work"]
    assert completed["integration_classification"] == "pass_ready_for_review"
    assert completed["linear_writeback"]["posted"] is False
    assert completed["linear_writeback"]["dry_run"] is True
    assert (
        completed["linear_writeback"]["marker"]
        == "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"
    )
    assert "real_Linear_writeback_posted" in completed["non_claims"]

    listed = client.get("/api/gateway/agy/completed-work")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["count"] == 1
    assert listed_body["completed_work"][0]["id"] == completed["id"]
    assert (
        listed_body["completed_work"][0]["integration_classification"]
        == "pass_ready_for_review"
    )
    assert (
        listed_body["completed_work"][0]["promotion_decision"]
        == completed["promotion_decision"]
    )
    detail = client.get(f"/api/gateway/agy/completed-work/{completed['id']}")
    assert detail.status_code == 200
    assert (
        detail.json()["completed_work"]["promotion_decision"]
        == completed["promotion_decision"]
    )
    assert completed["promotion_decision"]["status"] == ("hold_needs_durable_evidence")
    assert completed["promotion_decision"]["side_effects"]["github_pr_created"] is False

    rejected = client.post(
        "/api/gateway/agy/completed-work/ingest",
        json={
            "completed_work_text": completed_work_text(marker="<EXPECTED_OK_MARKER>")
        },
    )
    assert rejected.status_code == 422
    assert (
        "template completed-work packet field is not proof" in rejected.json()["detail"]
    )


def test_one_agent_dashboard_linear_dry_run_bridge(monkeypatch, tmp_path):
    db = tmp_path / "completed_work.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    client = TestClient(server.app)

    ingested = client.post(
        "/api/gateway/agy/completed-work/ingest",
        json={
            "completed_work_text": completed_work_text(
                marker="ONE_AGENT_BRIDGE_PACKET_OK"
            )
        },
    )
    assert ingested.status_code == 200
    completed = ingested.json()["completed_work"]

    bridge = client.get(
        f"/api/gateway/agy/completed-work/{completed['id']}/dashboard-linear-dry-run",
        params={"requested_by": "dashboard-test"},
    )
    assert bridge.status_code == 200
    body = bridge.json()
    assert body["marker"] == "ONE_AGENT_COMPLETED_WORK_TO_DASHBOARD_LINEAR_DRY_RUN_OK"
    assert (
        body["dashboard"]["marker"]
        == "ONE_AGENT_COMPLETED_WORK_TO_DASHBOARD_LINEAR_DRY_RUN_OK"
    )
    assert body["dashboard"]["status"] == "ready"
    assert body["dashboard"]["integration_classification"] == "pass_ready_for_review"
    assert body["linear_writeback"]["posted"] is False
    assert body["linear_writeback"]["dry_run"] is True
    assert body["side_effects"]["linear_comment_posted"] is False
    assert body["side_effects"]["github_pr_created"] is False
    assert body["side_effects"]["auto_merge_enabled"] is False
    assert body["pr_dry_run"]["linear_writeback"]["posted"] is False
    assert body["pr_dry_run"]["side_effects"]["github_pr_created"] is False

    latest = client.get(
        "/api/gateway/agy/completed-work/dashboard-linear-dry-run/latest"
    )
    assert latest.status_code == 200
    assert latest.json()["completed_work"]["id"] == completed["id"]


def test_one_agent_completed_work_verified_pr_dry_run_bridge(monkeypatch, tmp_path):
    db = tmp_path / "completed_work.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    client = TestClient(server.app)

    ingested = client.post(
        "/api/gateway/agy/completed-work/ingest",
        json={
            "completed_work_text": completed_work_text(
                marker="ONE_AGENT_VERIFIED_PR_PACKET_OK"
            )
        },
    )
    assert ingested.status_code == 200
    completed = ingested.json()["completed_work"]

    verified = client.get(
        f"/api/gateway/agy/completed-work/{completed['id']}/verified-pr-dry-run",
        params={"requested_by": "dashboard-test"},
    )
    assert verified.status_code == 200
    body = verified.json()
    assert body["marker"] == "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK"
    assert (
        body["dashboard"]["marker"]
        == "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK"
    )
    assert body["dashboard"]["status"] == "ready"
    assert body["dashboard"]["verified_pr_dry_run"] is True
    assert body["dashboard"]["verification_gate"] == "pass"
    assert body["pr_dry_run"]["github_pr_plan"]["created"] is False
    assert body["pr_dry_run"]["branch_plan"]["executed"] is False
    assert body["verification"]["marker"] == "AGY_PR_VERIFICATION_GATE_OK"
    assert body["verification"]["verification_gate"] == "pass"
    assert body["verification_artifact"]["status"] == "dry_run_verified"
    assert (
        body["verification_artifact"]["marker"]
        == "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK"
    )
    assert body["verification_artifact"]["selected_commands"]
    assert body["verification_artifact"]["real_git_branch_created"] is False
    assert body["verification_artifact"]["real_github_pr_created"] is False
    assert body["linear_writeback"]["posted"] is False
    assert body["linear_writeback"]["dry_run"] is True
    assert "real_github_pr_created=false" in body["linear_writeback"]["body"]
    assert body["side_effects"]["linear_comment_posted"] is False
    assert body["side_effects"]["github_pr_created"] is False
    assert body["side_effects"]["git_branch_created"] is False
    assert body["side_effects"]["auto_merge_enabled"] is False
    assert body["side_effects"]["production_deployed"] is False
    assert body["non_claims"]["canonical_full_suite_green"] is False

    latest = client.get("/api/gateway/agy/completed-work/verified-pr-dry-run/latest")
    assert latest.status_code == 200
    assert latest.json()["completed_work"]["id"] == completed["id"]

    missing = client.get(
        "/api/gateway/agy/completed-work/no-such-row/verified-pr-dry-run"
    )
    assert missing.status_code == 404


def test_dashboard_renders_provider_neutral_verification_receipt_marker():
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(
        encoding="utf-8"
    )
    assert "PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_OK" in html
    assert "provider-neutral-verification-receipt-card" in html
    assert "OPTIONAL ${item.provider}" in html
    assert "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK" not in html


def test_merge_backlog_api_list_detail_and_verify_use_persisted_rows(
    monkeypatch, tmp_path
):
    row = seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    listed = client.get("/api/gateway/agy/merge-backlog")
    assert listed.status_code == 200
    body = listed.json()
    assert body["marker"] == "AGY_CLEAN_PR_AND_VERIFICATION_GATE_OK"
    assert body["count"] == 1
    item = body["merge_backlog"][0]
    assert item["completed_work_id"] == row.id
    assert item["recommended_action"] == "open_or_update_pr"
    assert item["verification_gate"] == "pass"
    assert item["eligible_for_auto_merge"] is False
    assert body["non_claims"]["auto_merge"] is False

    detail = client.get(f"/api/gateway/agy/merge-backlog/{row.id}")
    assert detail.status_code == 200
    assert detail.json()["merge_backlog"]["completed_work_id"] == row.id
    assert detail.json()["merge_backlog"]["dry_run"] is True

    verify = client.post(f"/api/gateway/agy/merge-backlog/{row.id}/verify")
    assert verify.status_code == 200
    verify_body = verify.json()
    assert verify_body["marker"] == "AGY_PR_VERIFICATION_GATE_OK"
    assert verify_body["verification_gate"] == "pass"
    assert verify_body["eligible_for_auto_merge"] is False
    assert verify_body["non_claims"]["production_deploy"] is False


def test_merge_backlog_api_local_aliases_and_unknown_404(monkeypatch, tmp_path):
    row = seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    local_detail = client.get(f"/api/agy/merge-backlog/{row.id}")
    assert local_detail.status_code == 200
    assert local_detail.json()["merge_backlog"]["completed_work_id"] == row.id

    missing = client.get("/api/gateway/agy/merge-backlog/no-such-row")
    assert missing.status_code == 404

    missing_verify = client.post("/api/gateway/agy/merge-backlog/no-such-row/verify")
    assert missing_verify.status_code == 404


def test_prompt5_pr_candidate_endpoint_is_explicit_operator_metadata_only(
    monkeypatch, tmp_path
):
    row = seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    res = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-candidate",
        json={"requested_by": "dashboard-test", "action": "stage_pr_candidate"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["marker"] == "PROMPT5_PR_CANDIDATE_LIFECYCLE_OK"
    assert body["lifecycle_state"] == "candidate_metadata_ready"
    assert body["operator_action_required"] is True
    assert body["candidate"]["recommended_action"] == "open_or_update_pr"
    assert body["candidate"]["eligible_for_auto_merge"] is False
    assert body["side_effects"]["github_pr_created"] is False
    assert body["side_effects"]["auto_merge"] is False
    assert body["side_effects"]["production_deploy"] is False
    assert body["non_claims"]["real_github_pr_created"] is False

    missing = client.post("/api/gateway/agy/merge-backlog/no-such-row/pr-candidate")
    assert missing.status_code == 404


def test_prompt53_operator_pr_dry_run_endpoint_returns_plan_without_side_effects(
    monkeypatch, tmp_path
):
    row = seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    res = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-dry-run",
        json={
            "requested_by": "dashboard-test",
            "action": "operator_pr_creation_dry_run",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["marker"] == "PROMPT5_OPERATOR_PR_DRY_RUN_OK"
    assert body["dry_run_only"] is True
    assert body["operator_approved_action"] is True
    assert body["branch_plan"]["executed"] is False
    assert body["github_pr_plan"]["created"] is False
    assert body["github_pr_plan"]["create_command"].startswith("DRY_RUN_ONLY:")
    assert body["verification_gate_selection"]["status"] == "selected"
    assert body["linear_writeback"]["posted"] is False
    assert body["linear_writeback"]["dry_run_payload_only"] is True
    assert body["side_effects"]["github_pr_created"] is False
    assert body["side_effects"]["linear_comment_posted"] is False
    assert body["non_claims"]["git_branch_created"] is False

    missing = client.post("/api/gateway/agy/merge-backlog/no-such-row/pr-dry-run")
    assert missing.status_code == 404


def test_prompt54_real_pr_approval_gate_api_requires_explicit_approval(
    monkeypatch, tmp_path
):
    row = seed(monkeypatch, tmp_path)
    client = TestClient(server.app)

    blocked = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-approval",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": "wrong-token",
        },
    )
    assert blocked.status_code == 200
    blocked_body = blocked.json()
    assert blocked_body["status"] == "blocked"
    assert blocked_body["approval_record"]["approved"] is False
    assert blocked_body["real_pr_creation_action"]["exposed"] is False
    assert blocked_body["side_effects"]["github_pr_created"] is False

    approved = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-approval",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": f"APPROVE_REAL_PR:{row.id}",
            "approval_note": "explicit api approval test",
        },
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["marker"] == "PROMPT5_REAL_PR_APPROVAL_GATE_OK"
    assert body["approval_record"]["approved"] is True
    assert body["policy_gate"]["status"] == "pass"
    assert body["policy_gate"]["requires_separate_approved_action"] is True
    assert body["real_pr_creation_action"]["exposed"] is True
    assert body["real_pr_creation_action"]["executed"] is False
    assert body["side_effects"]["github_pr_created"] is False
    assert body["non_claims"]["real_github_pr_created"] is False

    action = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-create-approved",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": f"APPROVE_REAL_PR:{row.id}",
            "approval_id": body["approval_record"]["approval_id"],
        },
    )
    assert action.status_code == 200
    action_body = action.json()
    assert action_body["status"] == "ok"
    assert (
        action_body["real_pr_creation_action"]["status"] == "ready_for_future_executor"
    )
    assert action_body["real_pr_creation_action"]["execution_implemented"] is False
    assert action_body["real_pr_creation_action"]["executed"] is False
    assert action_body["side_effects"]["github_pr_created"] is False

    missing = client.post("/api/gateway/agy/merge-backlog/no-such-row/pr-approval")
    assert missing.status_code == 404


def test_prompt55_approved_real_pr_executor_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    row = seed(monkeypatch, tmp_path)
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_PR_EXECUTOR", raising=False)
    client = TestClient(server.app)

    blocked = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-executor",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": f"APPROVE_REAL_PR:{row.id}",
            "approval_id": "missing",
            "final_operator_trigger": True,
            "execute": False,
            "executor_mode": "dry_run",
            "allow_real_side_effects": False,
        },
    )
    assert blocked.status_code == 200
    blocked_body = blocked.json()
    assert blocked_body["status"] == "blocked"
    assert blocked_body["commands_rendered"] is True
    assert blocked_body["commands_executed"] is False
    assert blocked_body["side_effects"]["github_pr_created"] is False

    approval = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-approval",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": f"APPROVE_REAL_PR:{row.id}",
        },
    )
    assert approval.status_code == 200
    approval_id = approval.json()["approval_record"]["approval_id"]

    dry = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-executor",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": f"APPROVE_REAL_PR:{row.id}",
            "approval_id": approval_id,
            "final_operator_trigger": True,
            "execute": False,
            "executor_mode": "dry_run",
            "allow_real_side_effects": False,
        },
    )
    assert dry.status_code == 200
    body = dry.json()
    assert body["marker"] == "PROMPT5_APPROVED_REAL_PR_EXECUTOR_OK"
    assert body["executor_mode"] == "dry_run"
    assert body["commands_rendered"] is True
    assert body["commands_executed"] is False
    assert body["executor_result"]["real_github_pr_created"] is False
    assert body["side_effects"]["git_branch_created"] is False
    assert body["side_effects"]["github_pr_created"] is False
    assert body["side_effects"]["auto_merge"] is False
    assert body["side_effects"]["production_deploy"] is False
    assert (
        body["audit_writeback"]["marker"] == "PROMPT7_EXECUTOR_API_AUDIT_WRITEBACK_OK"
    )
    assert body["audit_writeback"]["recorded"] is True
    assert body["executor_run_id"] == body["audit_writeback"]["run_id"]
    executor_runs = client.get("/api/gateway/agy/executor-runs?limit=5")
    assert executor_runs.status_code == 200
    assert any(
        run["run_id"] == body["executor_run_id"] for run in executor_runs.json()["runs"]
    )

    real_blocked = client.post(
        f"/api/gateway/agy/merge-backlog/{row.id}/pr-executor",
        json={
            "requested_by": "dashboard-test",
            "approved_by": "operator",
            "approval_token": f"APPROVE_REAL_PR:{row.id}",
            "approval_id": approval_id,
            "final_operator_trigger": True,
            "execute": True,
            "executor_mode": "real",
            "allow_real_side_effects": True,
        },
    )
    assert real_blocked.status_code == 200
    real_body = real_blocked.json()
    assert real_body["status"] == "blocked"
    assert "real_executor_env_enabled" in real_body["policy_gate"]["blocked_reasons"]
    assert real_body["commands_executed"] is False
    assert real_body["side_effects"]["real_github_pr_created"] is False

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "prompt5-approved-real-pr-executor-action" in text
    assert "Plan Approved PR Executor" in text
    assert "stageApprovedPrExecutor" in text
    assert "audit_writeback" in text

    missing = client.post("/api/gateway/agy/merge-backlog/no-such-row/pr-executor")
    assert missing.status_code == 404


def test_prompt6_executor_run_api_and_dashboard_are_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(tmp_path / "cw.db"))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_PR_EXECUTOR", raising=False)
    client = TestClient(server.app)

    canary = client.post(
        "/api/gateway/agy/executor-runs/canary-dry-run",
        json={
            "requested_by": "dashboard-test",
            "executor_mode": "dry_run",
            "execute": False,
            "allow_real_side_effects": False,
        },
    )
    assert canary.status_code == 200
    body = canary.json()
    assert body["status"] == "ok"
    assert body["marker"] == "PROMPT6_EXECUTOR_AUDIT_CANARY_OK"
    assert body["executor_mode"] == "dry_run"
    assert body["commands_rendered"] is True
    assert body["commands_executed"] is False
    assert body["real_github_pr_created"] is False
    assert body["git_branch_created"] is False
    assert body["auto_merge_enabled"] is False
    assert body["production_deployed"] is False
    assert body["AGY_dispatch"] is False
    run_id = body["run_id"]

    listed = client.get("/api/gateway/agy/executor-runs?limit=3")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["count"] == 1
    assert listed_body["runs"][0]["run_id"] == run_id

    detail = client.get(f"/api/gateway/agy/executor-runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run"]["marker"] == "PROMPT6_EXECUTOR_AUDIT_CANARY_OK"

    real_blocked = client.post(
        "/api/gateway/agy/executor-runs/canary-dry-run",
        json={
            "requested_by": "dashboard-test",
            "executor_mode": "real",
            "execute": True,
            "allow_real_side_effects": True,
        },
    )
    assert real_blocked.status_code == 200
    real_body = real_blocked.json()
    assert real_body["status"] == "blocked"
    assert real_body["marker"] == "PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED"
    assert "real_executor_env_enabled" in real_body["blocked_reasons"]
    assert real_body["commands_executed"] is False
    assert real_body["real_github_pr_created"] is False
    assert real_body["git_branch_created"] is False

    missing = client.get("/api/gateway/agy/executor-runs/no-such-run")
    assert missing.status_code == 404

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "prompt6-executor-audit-canary" in text
    assert "Run Executor Canary Dry Run" in text
    assert "runPrompt6ExecutorCanaryDryRun" in text
    assert "recent_executor_run_history" in text


def test_one_agent_promotion_decision_ledger_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    ledger = tmp_path / "promotion-ledger.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(ledger))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    preview = client.get(
        f"/api/gateway/agy/completed-work/{row.id}/promotion-decision/preview",
        params={"requested_by": "dashboard-test"},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["marker"] == "ONE_AGENT_PROMOTION_DECISION_LEDGER_OK"
    assert preview_body["persisted"] is False
    assert preview_body["promotion_decision"]["recommendation"] == "open_or_update_pr"
    assert preview_body["promotion_decision"]["status"] == "decision_ready"
    assert (
        preview_body["promotion_decision"]["source_decision"]["promotion_decision"]
        == "open_or_update_pr_dry_run_only"
    )
    assert (
        preview_body["promotion_decision"]["evidence"]["source_decision_gate"][
            "allows_promotion"
        ]
        is True
    )
    assert (
        preview_body["promotion_decision"]["okf"]["promotion_decision"]
        == "open_or_update_pr"
    )
    assert preview_body["side_effects"]["github_pr_created"] is False
    assert preview_body["side_effects"]["auto_merge_enabled"] is False

    recorded = client.post(
        f"/api/gateway/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "dashboard-test"},
    )
    assert recorded.status_code == 200
    record_body = recorded.json()
    decision = record_body["promotion_decision"]
    assert record_body["persisted"] is True
    assert decision["completed_work_id"] == row.id
    assert decision["packet_classification"] == "packet_valid"
    assert decision["integration_classification"] == "pass_ready_for_review"
    assert decision["verification_gate"] == "pass"
    assert decision["source_decision"]["evidence_retention"]["status"] == "complete"
    assert (
        decision["source_decision"]["evidence_retention"]["proof_log_retained"] is True
    )
    assert (
        decision["source_decision"]["evidence_retention"]["source_commit_sha"]
        == VALID_SOURCE_SHA
    )
    assert decision["evidence"]["source_decision_gate"]["allows_promotion"] is True
    assert decision["side_effects"]["linear_comment_posted"] is False
    assert decision["side_effects"]["github_pr_created"] is False
    assert decision["side_effects"]["auto_merge_enabled"] is False
    assert ledger.exists()

    listed = client.get("/api/gateway/agy/promotion-decisions")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["count"] == 1
    assert (
        listed_body["promotion_decisions"][0]["promotion_decision_id"]
        == decision["promotion_decision_id"]
    )

    latest = client.get("/api/gateway/agy/promotion-decisions/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["promotion_decision"]["promotion_decision_id"]
        == decision["promotion_decision_id"]
    )

    detail = client.get(
        f"/api/gateway/agy/promotion-decisions/{decision['promotion_decision_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["promotion_decision"]["recommendation"] == "open_or_update_pr"

    missing = client.get("/api/gateway/agy/promotion-decisions/no-such-decision")
    assert missing.status_code == 404

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "ONE_AGENT_PROMOTION_DECISION_LEDGER_OK" in text
    assert "Promotion Decision Ledger" in text
    assert "promotion-decisions/latest" in text
    assert "fetchPromotionDecisionLedger" in text


def test_promotion_ledger_blocks_historical_and_incomplete_durable_evidence(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    client = TestClient(server.app)

    historical = ingest_completed_work(packet(), db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE agy_completed_work SET evidence_json = '{}' WHERE id = ?",
            (historical.id,),
        )
        conn.commit()
    historical_res = client.post(
        f"/api/gateway/agy/completed-work/{historical.id}/promotion-decision",
        json={"requested_by": "evidence-hold-test"},
    )
    assert historical_res.status_code == 200
    historical_decision = historical_res.json()["promotion_decision"]
    assert historical_decision["status"] == "hold_needs_durable_evidence"
    assert historical_decision["recommendation"] == "hold_for_durable_evidence"
    assert (
        historical_decision["evidence"]["source_decision_gate"]["allows_promotion"]
        is False
    )
    assert (
        historical_decision["source_decision"]["evidence_retention"]["status"]
        == "unavailable"
    )
    assert historical_decision["side_effects"]["github_pr_created"] is False

    approval_res = client.get(
        f"/api/gateway/agy/promotion-decisions/{historical_decision['promotion_decision_id']}/operator-action/preview",
        params={"operator_decision": "approve", "requested_by": "evidence-hold-test"},
    )
    assert approval_res.status_code == 200
    approval = approval_res.json()["operator_action_approval"]
    assert approval["policy_gate"] == "manual_review"
    assert approval["execution_preview"]["would_execute"] is False
    assert approval["side_effects"]["github_pr_created"] is False

    missing_commit = ingest_completed_work(
        retained_packet(tmp_path, source_commit_sha=None), db_path=db
    )
    missing_commit_res = client.get(
        f"/api/gateway/agy/completed-work/{missing_commit.id}/promotion-decision/preview",
        params={"requested_by": "evidence-hold-test"},
    )
    assert missing_commit_res.status_code == 200
    missing_commit_decision = missing_commit_res.json()["promotion_decision"]
    assert missing_commit_decision["status"] == "hold_needs_durable_evidence"
    assert missing_commit_decision["recommendation"] == "hold_for_durable_evidence"
    assert (
        missing_commit_decision["evidence"]["source_decision_gate"]["allows_promotion"]
        is False
    )
    assert (
        missing_commit_decision["source_decision"]["evidence_retention"][
            "source_commit_sha"
        ]
        is None
    )


def test_legacy_promotion_ledger_revalidates_against_current_missing_evidence(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    row = ingest_completed_work(packet(), db_path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE agy_completed_work SET evidence_json = '{}' WHERE id = ?",
            (row.id,),
        )
        conn.commit()
    legacy = {
        "promotion_decision_id": "promotion-59041b8e317299a5",
        "completed_work_id": row.id,
        "status": "decision_ready",
        "recommendation": "open_or_update_pr",
        "requested_by": "production-legacy",
        "recorded_at": "2026-07-20T20:00:00+00:00",
        "operator_request": {"requested_by": "production-legacy"},
        "side_effects": {"github_pr_created": False, "linear_comment_posted": False},
        "non_claims": {"production_deploy": False},
        "marker": "ONE_AGENT_PROMOTION_DECISION_LEDGER_OK",
    }
    ledger.write_text(json.dumps([legacy]), encoding="utf-8")
    client = TestClient(server.app)

    listed = client.get("/api/gateway/agy/promotion-decisions")
    assert listed.status_code == 200
    listed_decision = listed.json()["promotion_decisions"][0]
    assert listed_decision["promotion_decision_id"] == legacy["promotion_decision_id"]
    assert listed_decision["completed_work_id"] == row.id
    assert listed_decision["recorded_at"] == legacy["recorded_at"]
    assert listed_decision["requested_by"] == legacy["requested_by"]
    assert listed_decision["stored_legacy_record"] is True
    assert listed_decision["stored_status"] == "decision_ready"
    assert listed_decision["revalidation_status"] == "held_by_current_evidence"
    assert listed_decision["status"] == "hold_needs_durable_evidence"
    assert listed_decision["recommendation"] == "hold_for_durable_evidence"
    assert (
        listed_decision["source_decision"]["evidence_retention"]["status"]
        == "unavailable"
    )

    detail = client.get(
        f"/api/gateway/agy/promotion-decisions/{legacy['promotion_decision_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["promotion_decision"] == listed_decision

    approval_res = client.get(
        f"/api/gateway/agy/promotion-decisions/{legacy['promotion_decision_id']}/operator-action/preview",
        params={"operator_decision": "approve", "requested_by": "legacy-test"},
    )
    assert approval_res.status_code == 200
    approval = approval_res.json()["operator_action_approval"]
    assert approval["policy_gate"] == "manual_review"
    assert approval["execution_preview"]["would_execute"] is False


def test_legacy_promotion_ledger_missing_completed_work_linkage_fails_closed(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    ledger = tmp_path / "promotion-ledger.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(ledger))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    legacy = {
        "promotion_decision_id": "promotion-corrupt-linkage",
        "completed_work_id": "agy-cw-missing-row",
        "status": "decision_ready",
        "recommendation": "open_or_update_pr",
        "requested_by": "production-legacy",
        "recorded_at": "2026-07-20T20:00:00+00:00",
        "side_effects": {"github_pr_created": False, "linear_comment_posted": False},
        "non_claims": {"production_deploy": False},
        "marker": "ONE_AGENT_PROMOTION_DECISION_LEDGER_OK",
    }
    ledger.write_text(json.dumps([legacy]), encoding="utf-8")
    client = TestClient(server.app)

    listed = client.get("/api/gateway/agy/promotion-decisions")
    assert listed.status_code == 200
    decision = listed.json()["promotion_decisions"][0]
    assert decision["revalidation_status"] == "held_by_current_evidence"
    assert decision["status"] == "manual_review"
    assert decision["recommendation"] == "manual_review"
    assert decision["revalidation_reason"].startswith(
        "completed_work_revalidation_failed:"
    )

    detail = client.get(
        "/api/gateway/agy/promotion-decisions/promotion-corrupt-linkage"
    )
    assert detail.status_code == 200
    assert detail.json()["promotion_decision"] == decision


def test_legacy_promotion_ledger_can_remain_ready_with_current_complete_evidence(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    legacy = {
        "promotion_decision_id": "promotion-59041b8e317299a5",
        "completed_work_id": row.id,
        "status": "decision_ready",
        "recommendation": "open_or_update_pr",
        "requested_by": "production-legacy",
        "recorded_at": "2026-07-20T20:00:00+00:00",
        "side_effects": {"github_pr_created": False, "linear_comment_posted": False},
        "non_claims": {"production_deploy": False},
        "marker": "ONE_AGENT_PROMOTION_DECISION_LEDGER_OK",
    }
    ledger.write_text(json.dumps([legacy]), encoding="utf-8")
    client = TestClient(server.app)

    detail = client.get(
        f"/api/gateway/agy/promotion-decisions/{legacy['promotion_decision_id']}"
    )
    assert detail.status_code == 200
    decision = detail.json()["promotion_decision"]
    assert decision["promotion_decision_id"] == legacy["promotion_decision_id"]
    assert decision["recorded_at"] == legacy["recorded_at"]
    assert decision["requested_by"] == legacy["requested_by"]
    assert decision["stored_legacy_record"] is True
    assert decision["revalidation_status"] == "passed_current_evidence"
    assert decision["status"] == "decision_ready"
    assert decision["recommendation"] == "open_or_update_pr"
    assert decision["source_decision"]["evidence_retention"]["status"] == "complete"

    approval_res = client.get(
        f"/api/gateway/agy/promotion-decisions/{legacy['promotion_decision_id']}/operator-action/preview",
        params={"operator_decision": "approve", "requested_by": "legacy-test"},
    )
    assert approval_res.status_code == 200
    approval = approval_res.json()["operator_action_approval"]
    assert approval["policy_gate"] == "pass"
    assert approval["execution_preview"]["would_execute"] is True


def test_one_agent_operator_action_approval_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    promotion_ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(promotion_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    promotion = client.post(
        f"/api/gateway/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "approval-test"},
    )
    assert promotion.status_code == 200
    promotion_decision = promotion.json()["promotion_decision"]
    assert promotion_decision["status"] == "decision_ready"
    promotion_decision_id = promotion_decision["promotion_decision_id"]

    preview = client.get(
        f"/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action/preview",
        params={"operator_decision": "approve", "requested_by": "dashboard-test"},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["marker"] == "ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_OK"
    assert preview_body["persisted"] is False
    preview_approval = preview_body["operator_action_approval"]
    assert preview_approval["promotion_decision_id"] == promotion_decision_id
    assert preview_approval["completed_work_id"] == row.id
    assert preview_approval["requested_action"] == "open_or_update_pr"
    assert preview_approval["operator_decision"] == "approve"
    assert preview_approval["policy_gate"] == "pass"
    assert preview_approval["execution_preview"]["dry_run_only"] is True
    assert preview_approval["execution_preview"]["executed"] is False
    assert (
        preview_approval["execution_preview"]["side_effects"]["github_pr_created"]
        is False
    )
    assert preview_approval["side_effects"]["linear_comment_posted"] is False
    assert preview_approval["side_effects"]["github_pr_created"] is False
    assert preview_approval["side_effects"]["auto_merge_enabled"] is False

    recorded = client.post(
        f"/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action",
        json={"operator_decision": "approve", "requested_by": "dashboard-test"},
    )
    assert recorded.status_code == 200
    record_body = recorded.json()
    approval = record_body["operator_action_approval"]
    assert record_body["persisted"] is True
    assert approval["promotion_decision_id"] == promotion_decision_id
    assert approval["completed_work_id"] == row.id
    assert approval["requested_action"] == "open_or_update_pr"
    assert approval["operator_decision"] == "approve"
    assert approval["policy_gate"] == "pass"
    assert approval["execution_preview"]["would_execute"] is True
    assert approval["execution_preview"]["executed"] is False
    assert approval["side_effects"]["linear_comment_posted"] is False
    assert approval["side_effects"]["github_pr_created"] is False
    assert approval["side_effects"]["auto_merge_enabled"] is False
    assert approval_ledger.exists()

    listed = client.get("/api/gateway/agy/operator-action-approvals")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["count"] == 1
    assert (
        listed_body["operator_action_approvals"][0]["operator_action_approval_id"]
        == approval["operator_action_approval_id"]
    )

    latest = client.get("/api/gateway/agy/operator-action-approvals/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["operator_action_approval"]["operator_action_approval_id"]
        == approval["operator_action_approval_id"]
    )

    detail = client.get(
        f"/api/gateway/agy/operator-action-approvals/{approval['operator_action_approval_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["operator_action_approval"]["policy_gate"] == "pass"

    rejected = client.post(
        f"/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action",
        json={"operator_decision": "reject", "requested_by": "dashboard-test"},
    )
    assert rejected.status_code == 200
    rejected_approval = rejected.json()["operator_action_approval"]
    assert rejected_approval["operator_decision"] == "reject"
    assert rejected_approval["policy_gate"] == "blocked"
    assert rejected_approval["execution_preview"]["would_execute"] is False
    assert rejected_approval["side_effects"]["github_pr_created"] is False

    deferred = client.get(
        f"/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action/preview",
        params={"operator_decision": "defer", "requested_by": "dashboard-test"},
    )
    assert deferred.status_code == 200
    assert deferred.json()["operator_action_approval"]["policy_gate"] == "manual_review"

    invalid = client.get(
        f"/api/gateway/agy/promotion-decisions/{promotion_decision_id}/operator-action/preview",
        params={"operator_decision": "launch"},
    )
    assert invalid.status_code == 400

    missing = client.get("/api/gateway/agy/operator-action-approvals/no-such-approval")
    assert missing.status_code == 404

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "ONE_AGENT_LEDGER_TO_OPERATOR_ACTION_APPROVAL_OK" in text
    assert "Operator Action Approval" in text
    assert "operator-action-approvals/latest" in text
    assert "fetchOperatorActionApproval" in text


def test_one_agent_approved_action_executor_dry_run_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    promotion_ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    executor_ledger = tmp_path / "approved-executors.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(promotion_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv(
        "PRISMATIC_AGY_APPROVED_ACTION_EXECUTOR_STATE", str(executor_ledger)
    )
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR", raising=False)
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    promotion_res = client.post(
        f"/api/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "pytest"},
    )
    assert promotion_res.status_code == 200
    promotion = promotion_res.json()["promotion_decision"]

    approval_res = client.post(
        f"/api/agy/promotion-decisions/{promotion['promotion_decision_id']}/operator-action",
        json={"operator_decision": "approve", "requested_by": "pytest"},
    )
    assert approval_res.status_code == 200
    approval = approval_res.json()["operator_action_approval"]
    assert approval["operator_decision"] == "approve"
    assert approval["policy_gate"] == "pass"

    preview = client.get(
        f"/api/agy/operator-action-approvals/{approval['operator_action_approval_id']}/executor-dry-run/preview"
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert (
        preview_body["marker"] == "ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_OK"
    )
    assert preview_body["persisted"] is False
    executor = preview_body["approved_action_executor"]
    assert (
        executor["operator_action_approval_id"]
        == approval["operator_action_approval_id"]
    )
    assert executor["promotion_decision_id"] == promotion["promotion_decision_id"]
    assert executor["completed_work_id"] == row.id
    assert executor["requested_action"] == "open_or_update_pr"
    assert executor["executor_mode"] == "dry_run"
    assert executor["final_authorization_present"] is False
    assert executor["execution_status"] == "dry_run_ready"
    assert executor["command_preview"]["dry_run_only"] is True
    assert "DRY_RUN_ONLY" in executor["command_preview"]["summary"]
    assert executor["command_preview"]["executed"] is False
    assert executor["audit_writeback"]["posted"] is False
    assert executor["audit_writeback"]["dry_run"] is True
    assert all(value is False for value in executor["side_effects"].values())

    record = client.post(
        f"/api/agy/operator-action-approvals/{approval['operator_action_approval_id']}/executor-dry-run",
        json={"requested_by": "pytest"},
    )
    assert record.status_code == 200
    recorded = record.json()["approved_action_executor"]
    assert record.json()["persisted"] is True
    assert (
        recorded["approved_action_executor_id"]
        == executor["approved_action_executor_id"]
    )
    assert recorded["execution_status"] == "dry_run_ready"
    assert recorded["final_authorization_present"] is False
    assert recorded["audit_writeback"]["posted"] is False
    assert all(value is False for value in recorded["side_effects"].values())

    latest = client.get("/api/agy/approved-action-executors/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["approved_action_executor"]["approved_action_executor_id"]
        == recorded["approved_action_executor_id"]
    )
    listed = client.get("/api/agy/approved-action-executors")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(
        f"/api/agy/approved-action-executors/{recorded['approved_action_executor_id']}"
    )
    assert detail.status_code == 200
    assert (
        detail.json()["approved_action_executor"]["execution_status"] == "dry_run_ready"
    )

    reject_res = client.post(
        f"/api/agy/promotion-decisions/{promotion['promotion_decision_id']}/operator-action",
        json={"operator_decision": "reject", "requested_by": "pytest"},
    )
    assert reject_res.status_code == 200
    rejected = reject_res.json()["operator_action_approval"]
    blocked = client.get(
        f"/api/agy/operator-action-approvals/{rejected['operator_action_approval_id']}/executor-dry-run/preview"
    )
    assert blocked.status_code == 200
    blocked_executor = blocked.json()["approved_action_executor"]
    assert blocked_executor["execution_status"] == "blocked_operator_approval_required"
    assert blocked_executor["command_preview"]["would_execute"] is False
    assert blocked_executor["audit_writeback"]["posted"] is False
    assert all(value is False for value in blocked_executor["side_effects"].values())

    missing = client.get("/api/gateway/agy/approved-action-executors/no-such-executor")
    assert missing.status_code == 404

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "ONE_AGENT_OPERATOR_APPROVAL_TO_EXECUTOR_DRY_RUN_OK" in text
    assert "Approved Action Executor Dry Run" in text
    assert "approved-action-executors/latest" in text
    assert "fetchApprovedActionExecutor" in text


def test_one_agent_final_action_authorization_gate_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    promotion_ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    executor_ledger = tmp_path / "approved-executors.json"
    final_auth_ledger = tmp_path / "final-authorizations.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(promotion_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv(
        "PRISMATIC_AGY_APPROVED_ACTION_EXECUTOR_STATE", str(executor_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_FINAL_ACTION_AUTHORIZATION_STATE", str(final_auth_ledger)
    )
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR", raising=False)
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    promotion_res = client.post(
        f"/api/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "pytest"},
    )
    assert promotion_res.status_code == 200
    promotion = promotion_res.json()["promotion_decision"]
    approval_res = client.post(
        f"/api/agy/promotion-decisions/{promotion['promotion_decision_id']}/operator-action",
        json={"operator_decision": "approve", "requested_by": "pytest"},
    )
    assert approval_res.status_code == 200
    approval = approval_res.json()["operator_action_approval"]
    executor_res = client.post(
        f"/api/agy/operator-action-approvals/{approval['operator_action_approval_id']}/executor-dry-run",
        json={"requested_by": "pytest"},
    )
    assert executor_res.status_code == 200
    executor = executor_res.json()["approved_action_executor"]
    assert executor["execution_status"] == "dry_run_ready"

    preview = client.get(
        f"/api/gateway/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization/preview",
        params={"authorization_decision": "authorize", "requested_by": "pytest"},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert (
        preview_body["marker"]
        == "ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_OK"
    )
    assert preview_body["persisted"] is False
    final_auth = preview_body["final_action_authorization"]
    assert (
        final_auth["approved_action_executor_id"]
        == executor["approved_action_executor_id"]
    )
    assert (
        final_auth["operator_action_approval_id"]
        == approval["operator_action_approval_id"]
    )
    assert final_auth["promotion_decision_id"] == promotion["promotion_decision_id"]
    assert final_auth["completed_work_id"] == row.id
    assert final_auth["requested_action"] == "open_or_update_pr"
    assert final_auth["authorization_decision"] == "authorize"
    assert final_auth["authorization_token_expected"] == (
        f"APPROVE_EXECUTE_REAL_ACTION:{approval['operator_action_approval_id']}"
    )
    assert final_auth["authorization_token_present"] is False
    assert final_auth["real_execution_env_present"] is False
    assert final_auth["policy_gate"] == "blocked"
    assert final_auth["final_guard_state"] == "blocked_by_default"
    assert final_auth["execution_eligibility"]["eligible"] is False
    assert final_auth["execution_eligibility"]["executed"] is False
    assert all(value is False for value in final_auth["side_effects"].values())

    record = client.post(
        f"/api/gateway/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "authorize", "requested_by": "pytest"},
    )
    assert record.status_code == 200
    recorded = record.json()["final_action_authorization"]
    assert record.json()["persisted"] is True
    assert (
        recorded["final_action_authorization_id"]
        == final_auth["final_action_authorization_id"]
    )
    assert recorded["final_guard_state"] == "blocked_by_default"
    assert recorded["authorization_token_present"] is False
    assert recorded["real_execution_env_present"] is False
    assert all(value is False for value in recorded["side_effects"].values())

    latest = client.get("/api/gateway/agy/final-action-authorizations/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["final_action_authorization"]["final_action_authorization_id"]
        == recorded["final_action_authorization_id"]
    )
    listed = client.get("/api/agy/final-action-authorizations")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(
        f"/api/agy/final-action-authorizations/{recorded['final_action_authorization_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["final_action_authorization"]["policy_gate"] == "blocked"

    rejected = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "reject", "requested_by": "pytest"},
    )
    assert rejected.status_code == 200
    rejected_auth = rejected.json()["final_action_authorization"]
    assert rejected_auth["authorization_decision"] == "reject"
    assert rejected_auth["policy_gate"] == "blocked"
    assert rejected_auth["final_guard_state"] == "rejected_by_operator"
    assert rejected_auth["execution_eligibility"]["eligible"] is False
    assert all(value is False for value in rejected_auth["side_effects"].values())

    deferred = client.get(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization/preview",
        params={"authorization_decision": "defer", "requested_by": "pytest"},
    )
    assert deferred.status_code == 200
    deferred_auth = deferred.json()["final_action_authorization"]
    assert deferred_auth["policy_gate"] == "manual_review"
    assert deferred_auth["final_guard_state"] == "manual_review"

    invalid = client.get(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization/preview",
        params={"authorization_decision": "launch"},
    )
    assert invalid.status_code == 400
    missing = client.get(
        "/api/gateway/agy/final-action-authorizations/no-such-final-auth"
    )
    assert missing.status_code == 404

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "Final Action Authorization Gate" in text
    assert "ONE_AGENT_EXECUTOR_DRY_RUN_TO_FINAL_AUTHORIZATION_GATE_OK" in text
    assert "final-action-authorizations/latest" in text
    assert "fetchFinalActionAuthorization" in text


def test_one_agent_quarantined_execution_adapter_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    promotion_ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    executor_ledger = tmp_path / "approved-executors.json"
    final_auth_ledger = tmp_path / "final-authorizations.json"
    adapter_ledger = tmp_path / "quarantined-adapters.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(promotion_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv(
        "PRISMATIC_AGY_APPROVED_ACTION_EXECUTOR_STATE", str(executor_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_FINAL_ACTION_AUTHORIZATION_STATE", str(final_auth_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_QUARANTINED_EXECUTION_ADAPTER_STATE", str(adapter_ledger)
    )
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR", raising=False)
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    promotion = client.post(
        f"/api/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "pytest"},
    ).json()["promotion_decision"]
    approval = client.post(
        f"/api/agy/promotion-decisions/{promotion['promotion_decision_id']}/operator-action",
        json={"operator_decision": "approve", "requested_by": "pytest"},
    ).json()["operator_action_approval"]
    executor = client.post(
        f"/api/agy/operator-action-approvals/{approval['operator_action_approval_id']}/executor-dry-run",
        json={"requested_by": "pytest"},
    ).json()["approved_action_executor"]
    final_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "authorize", "requested_by": "pytest"},
    ).json()["final_action_authorization"]

    preview = client.get(
        f"/api/gateway/agy/final-action-authorizations/{final_auth['final_action_authorization_id']}/quarantined-adapter/preview",
        params={"requested_by": "pytest"},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert (
        preview_body["marker"]
        == "ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_OK"
    )
    assert preview_body["persisted"] is False
    adapter = preview_body["quarantined_execution_adapter"]
    assert (
        adapter["final_action_authorization_id"]
        == final_auth["final_action_authorization_id"]
    )
    assert (
        adapter["approved_action_executor_id"]
        == executor["approved_action_executor_id"]
    )
    assert (
        adapter["operator_action_approval_id"]
        == approval["operator_action_approval_id"]
    )
    assert adapter["promotion_decision_id"] == promotion["promotion_decision_id"]
    assert adapter["completed_work_id"] == row.id
    assert adapter["requested_action"] == "open_or_update_pr"
    assert adapter["adapter_mode"] == "quarantine_dry_run"
    assert adapter["adapter_state"] == "blocked_by_final_guard"
    assert adapter["egress_policy"]["policy"] == "deny_all_external_by_default"
    assert adapter["egress_policy"]["external_network_allowed"] is False
    assert adapter["egress_policy"]["github_api_allowed"] is False
    assert adapter["egress_policy"]["linear_api_allowed"] is False
    assert adapter["egress_policy"]["git_write_allowed"] is False
    assert adapter["egress_policy"]["production_deploy_allowed"] is False
    assert adapter["egress_policy"]["auto_merge_allowed"] is False
    assert adapter["egress_policy"]["bulk_agent_dispatch_allowed"] is False
    assert adapter["command_envelope"]["schema"] == (
        "prismatic.quarantined_execution_adapter.v1"
    )
    assert adapter["command_envelope"]["execute"] is False
    assert adapter["command_envelope"]["dry_run_only"] is True
    assert len(adapter["command_envelope_sha256"]) == 64
    assert adapter["audit_packet"]["audit_packet_id"].startswith("adapter-audit-")
    assert adapter["audit_packet"]["executed"] is False
    assert all(value is False for value in adapter["side_effects"].values())

    record = client.post(
        f"/api/agy/final-action-authorizations/{final_auth['final_action_authorization_id']}/quarantined-adapter",
        json={"requested_by": "pytest"},
    )
    assert record.status_code == 200
    recorded = record.json()["quarantined_execution_adapter"]
    assert record.json()["persisted"] is True
    assert (
        recorded["quarantined_execution_adapter_id"]
        == adapter["quarantined_execution_adapter_id"]
    )
    assert recorded["adapter_state"] == "blocked_by_final_guard"
    assert all(value is False for value in recorded["side_effects"].values())

    latest = client.get("/api/gateway/agy/quarantined-execution-adapters/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["quarantined_execution_adapter"][
            "quarantined_execution_adapter_id"
        ]
        == recorded["quarantined_execution_adapter_id"]
    )
    listed = client.get("/api/agy/quarantined-execution-adapters")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(
        f"/api/gateway/agy/quarantined-execution-adapters/{recorded['quarantined_execution_adapter_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["quarantined_execution_adapter"]["adapter_mode"] == (
        "quarantine_dry_run"
    )

    rejected_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "reject", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    rejected_adapter = client.get(
        f"/api/agy/final-action-authorizations/{rejected_auth['final_action_authorization_id']}/quarantined-adapter/preview"
    ).json()["quarantined_execution_adapter"]
    assert rejected_adapter["adapter_state"] == "manual_review"
    assert rejected_adapter["command_envelope"]["execute"] is False
    assert all(value is False for value in rejected_adapter["side_effects"].values())

    deferred_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "defer", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    deferred_adapter = client.get(
        f"/api/agy/final-action-authorizations/{deferred_auth['final_action_authorization_id']}/quarantined-adapter/preview"
    ).json()["quarantined_execution_adapter"]
    assert deferred_adapter["adapter_state"] == "manual_review"
    assert deferred_adapter["command_envelope"]["execute"] is False
    assert all(value is False for value in deferred_adapter["side_effects"].values())

    missing = client.get(
        "/api/gateway/agy/quarantined-execution-adapters/no-such-adapter"
    )
    assert missing.status_code == 404
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "Quarantined Execution Adapter" in text
    assert "ONE_AGENT_FINAL_AUTHORIZATION_TO_QUARANTINED_EXECUTION_ADAPTER_OK" in text
    assert "quarantined-execution-adapters/latest" in text
    assert "fetchQuarantinedExecutionAdapter" in text


def test_one_agent_sandboxed_execution_canary_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    promotion_ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    executor_ledger = tmp_path / "approved-executors.json"
    final_auth_ledger = tmp_path / "final-authorizations.json"
    adapter_ledger = tmp_path / "quarantined-adapters.json"
    canary_ledger = tmp_path / "sandbox-canaries.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(promotion_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv(
        "PRISMATIC_AGY_APPROVED_ACTION_EXECUTOR_STATE", str(executor_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_FINAL_ACTION_AUTHORIZATION_STATE", str(final_auth_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_QUARANTINED_EXECUTION_ADAPTER_STATE", str(adapter_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_SANDBOXED_EXECUTION_CANARY_STATE", str(canary_ledger)
    )
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_APPROVED_ACTION_EXECUTOR", raising=False)
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    promotion = client.post(
        f"/api/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "pytest"},
    ).json()["promotion_decision"]
    approval = client.post(
        f"/api/agy/promotion-decisions/{promotion['promotion_decision_id']}/operator-action",
        json={"operator_decision": "approve", "requested_by": "pytest"},
    ).json()["operator_action_approval"]
    executor = client.post(
        f"/api/agy/operator-action-approvals/{approval['operator_action_approval_id']}/executor-dry-run",
        json={"requested_by": "pytest"},
    ).json()["approved_action_executor"]
    final_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "authorize", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    adapter = client.post(
        f"/api/agy/final-action-authorizations/{final_auth['final_action_authorization_id']}/quarantined-adapter",
        json={"requested_by": "pytest"},
    ).json()["quarantined_execution_adapter"]

    preview = client.get(
        f"/api/gateway/agy/quarantined-execution-adapters/{adapter['quarantined_execution_adapter_id']}/sandbox-canary/preview",
        params={"requested_by": "pytest"},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert (
        preview_body["marker"]
        == "ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_OK"
    )
    assert preview_body["persisted"] is False
    canary = preview_body["sandboxed_execution_canary"]
    assert (
        canary["quarantined_execution_adapter_id"]
        == adapter["quarantined_execution_adapter_id"]
    )
    assert (
        canary["final_action_authorization_id"]
        == final_auth["final_action_authorization_id"]
    )
    assert (
        canary["approved_action_executor_id"] == executor["approved_action_executor_id"]
    )
    assert (
        canary["operator_action_approval_id"] == approval["operator_action_approval_id"]
    )
    assert canary["promotion_decision_id"] == promotion["promotion_decision_id"]
    assert canary["completed_work_id"] == row.id
    assert canary["requested_action"] == "open_or_update_pr"
    assert canary["canary_mode"] == "sandbox_noop_dry_run"
    assert canary["sandbox_state"] == "blocked_by_final_guard"
    assert canary["command_envelope_sha256"] == adapter["command_envelope_sha256"]
    assert canary["command_envelope_verified"] is True
    assert canary["sandbox_policy"]["policy"] == "local_noop_no_egress"
    assert canary["sandbox_policy"]["external_network_allowed"] is False
    assert canary["sandbox_policy"]["github_api_allowed"] is False
    assert canary["sandbox_policy"]["linear_api_allowed"] is False
    assert canary["sandbox_policy"]["git_write_allowed"] is False
    assert canary["sandbox_policy"]["production_deploy_allowed"] is False
    assert canary["sandbox_policy"]["auto_merge_allowed"] is False
    assert canary["sandbox_policy"]["bulk_agent_dispatch_allowed"] is False
    assert canary["noop_command_plan"]["real_commands_executed"] is False
    assert canary["noop_command_plan"]["external_calls_executed"] is False
    assert canary["sandbox_transcript"]["sandbox_transcript_id"].startswith(
        "sandbox-transcript-"
    )
    assert canary["sandbox_transcript"]["executed"] is False
    assert canary["egress_attempts"] == []
    assert all(value is False for value in canary["side_effects"].values())

    record = client.post(
        f"/api/agy/quarantined-execution-adapters/{adapter['quarantined_execution_adapter_id']}/sandbox-canary",
        json={"requested_by": "pytest"},
    )
    assert record.status_code == 200
    recorded = record.json()["sandboxed_execution_canary"]
    assert record.json()["persisted"] is True
    assert (
        recorded["sandboxed_execution_canary_id"]
        == canary["sandboxed_execution_canary_id"]
    )
    assert recorded["sandbox_state"] == "blocked_by_final_guard"
    assert all(value is False for value in recorded["side_effects"].values())

    latest = client.get("/api/gateway/agy/sandboxed-execution-canaries/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["sandboxed_execution_canary"]["sandboxed_execution_canary_id"]
        == recorded["sandboxed_execution_canary_id"]
    )
    listed = client.get("/api/agy/sandboxed-execution-canaries")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(
        f"/api/gateway/agy/sandboxed-execution-canaries/{recorded['sandboxed_execution_canary_id']}"
    )
    assert detail.status_code == 200
    assert (
        detail.json()["sandboxed_execution_canary"]["command_envelope_verified"] is True
    )

    rejected_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "reject", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    rejected_adapter = client.post(
        f"/api/agy/final-action-authorizations/{rejected_auth['final_action_authorization_id']}/quarantined-adapter",
        json={"requested_by": "pytest"},
    ).json()["quarantined_execution_adapter"]
    rejected_canary = client.get(
        f"/api/agy/quarantined-execution-adapters/{rejected_adapter['quarantined_execution_adapter_id']}/sandbox-canary/preview"
    ).json()["sandboxed_execution_canary"]
    assert rejected_canary["sandbox_state"] == "manual_review"
    assert rejected_canary["noop_command_plan"]["real_commands_executed"] is False
    assert all(value is False for value in rejected_canary["side_effects"].values())

    deferred_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "defer", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    deferred_adapter = client.post(
        f"/api/agy/final-action-authorizations/{deferred_auth['final_action_authorization_id']}/quarantined-adapter",
        json={"requested_by": "pytest"},
    ).json()["quarantined_execution_adapter"]
    deferred_canary = client.get(
        f"/api/agy/quarantined-execution-adapters/{deferred_adapter['quarantined_execution_adapter_id']}/sandbox-canary/preview"
    ).json()["sandboxed_execution_canary"]
    assert deferred_canary["sandbox_state"] == "manual_review"
    assert deferred_canary["noop_command_plan"]["real_commands_executed"] is False
    assert all(value is False for value in deferred_canary["side_effects"].values())

    missing = client.get("/api/gateway/agy/sandboxed-execution-canaries/no-such-canary")
    assert missing.status_code == 404
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "Sandboxed Execution Canary" in text
    assert "ONE_AGENT_QUARANTINED_ADAPTER_TO_SANDBOXED_EXECUTION_CANARY_OK" in text
    assert "sandboxed-execution-canaries/latest" in text
    assert "fetchSandboxedExecutionCanary" in text


def test_one_agent_real_executor_arming_gate_api_and_dashboard_are_safe(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
    promotion_ledger = tmp_path / "promotion-ledger.json"
    approval_ledger = tmp_path / "operator-approvals.json"
    executor_ledger = tmp_path / "approved-executors.json"
    final_auth_ledger = tmp_path / "final-authorizations.json"
    adapter_ledger = tmp_path / "quarantined-adapters.json"
    canary_ledger = tmp_path / "sandbox-canaries.json"
    arming_ledger = tmp_path / "real-executor-arming.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_PROMOTION_LEDGER_STATE", str(promotion_ledger))
    monkeypatch.setenv("PRISMATIC_AGY_OPERATOR_APPROVAL_STATE", str(approval_ledger))
    monkeypatch.setenv(
        "PRISMATIC_AGY_APPROVED_ACTION_EXECUTOR_STATE", str(executor_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_FINAL_ACTION_AUTHORIZATION_STATE", str(final_auth_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_QUARANTINED_EXECUTION_ADAPTER_STATE", str(adapter_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_SANDBOXED_EXECUTION_CANARY_STATE", str(canary_ledger)
    )
    monkeypatch.setenv(
        "PRISMATIC_AGY_REAL_EXECUTOR_ARMING_GATE_STATE", str(arming_ledger)
    )
    monkeypatch.delenv("PRISMATIC_REAL_EXECUTOR_ARMING_TOKEN", raising=False)
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_EXECUTOR_ARMING", raising=False)
    row = ingest_completed_work(retained_packet(tmp_path), db_path=db)
    client = TestClient(server.app)

    promotion = client.post(
        f"/api/agy/completed-work/{row.id}/promotion-decision",
        json={"requested_by": "pytest"},
    ).json()["promotion_decision"]
    approval = client.post(
        f"/api/agy/promotion-decisions/{promotion['promotion_decision_id']}/operator-action",
        json={"operator_decision": "approve", "requested_by": "pytest"},
    ).json()["operator_action_approval"]
    executor = client.post(
        f"/api/agy/operator-action-approvals/{approval['operator_action_approval_id']}/executor-dry-run",
        json={"requested_by": "pytest"},
    ).json()["approved_action_executor"]
    authorization = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "authorize", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    adapter = client.post(
        f"/api/agy/final-action-authorizations/{authorization['final_action_authorization_id']}/quarantined-adapter",
        json={"requested_by": "pytest"},
    ).json()["quarantined_execution_adapter"]
    canary = client.post(
        f"/api/agy/quarantined-execution-adapters/{adapter['quarantined_execution_adapter_id']}/sandbox-canary",
        json={"requested_by": "pytest"},
    ).json()["sandboxed_execution_canary"]

    preview = client.get(
        f"/api/agy/sandboxed-execution-canaries/{canary['sandboxed_execution_canary_id']}/real-executor-arming/preview"
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert (
        payload["marker"]
        == "ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_OK"
    )
    assert payload["persisted"] is False
    gate = payload["real_executor_arming_gate"]
    assert (
        gate["sandboxed_execution_canary_id"] == canary["sandboxed_execution_canary_id"]
    )
    assert (
        gate["quarantined_execution_adapter_id"]
        == adapter["quarantined_execution_adapter_id"]
    )
    assert (
        gate["final_action_authorization_id"]
        == authorization["final_action_authorization_id"]
    )
    assert (
        gate["approved_action_executor_id"] == executor["approved_action_executor_id"]
    )
    assert (
        gate["operator_action_approval_id"] == approval["operator_action_approval_id"]
    )
    assert gate["promotion_decision_id"] == promotion["promotion_decision_id"]
    assert gate["completed_work_id"] == row.id
    assert gate["requested_action"] == "open_or_update_pr"
    assert gate["arming_mode"] == "readiness_gate_only"
    assert gate["arming_state"] == "blocked_missing_real_authorization"
    assert gate["operator_token_expected"] == (
        f"ARM_REAL_EXECUTOR:{canary['sandboxed_execution_canary_id']}"
    )
    assert gate["operator_token_present"] is False
    assert gate["real_executor_env_present"] is False
    assert gate["real_executor_implemented"] is False
    assert gate["real_executor_invoked"] is False
    assert gate["execution_eligibility"]["executed"] is False
    missing_keys = {item["key"] for item in gate["missing_prerequisites"]}
    assert "operator_token_present" in missing_keys
    assert "real_executor_env_present" in missing_keys
    assert "real_executor_implementation_registered" in missing_keys
    satisfied_keys = {item["key"] for item in gate["satisfied_prerequisites"]}
    assert "command_envelope_verified" in satisfied_keys
    assert "sandbox_transcript_present" in satisfied_keys
    assert "sandbox_side_effects_false" in satisfied_keys
    assert gate["executor_contract_preview"]["real_executor_invoked"] is False
    assert gate["executor_contract_preview"]["executed"] is False
    assert all(value is False for value in gate["side_effects"].values())

    recorded = client.post(
        f"/api/agy/sandboxed-execution-canaries/{canary['sandboxed_execution_canary_id']}/real-executor-arming",
        json={"requested_by": "pytest"},
    )
    assert recorded.status_code == 200
    record = recorded.json()["real_executor_arming_gate"]
    assert recorded.json()["persisted"] is True
    assert (
        record["real_executor_arming_gate_id"] == gate["real_executor_arming_gate_id"]
    )

    latest = client.get("/api/agy/real-executor-arming-gates/latest")
    assert latest.status_code == 200
    assert (
        latest.json()["real_executor_arming_gate"]["real_executor_arming_gate_id"]
        == record["real_executor_arming_gate_id"]
    )
    listed = client.get("/api/gateway/agy/real-executor-arming-gates")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(
        f"/api/gateway/agy/real-executor-arming-gates/{record['real_executor_arming_gate_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["real_executor_arming_gate"]["arming_state"] == (
        "blocked_missing_real_authorization"
    )

    deferred_auth = client.post(
        f"/api/agy/approved-action-executors/{executor['approved_action_executor_id']}/final-authorization",
        json={"authorization_decision": "defer", "requested_by": "pytest"},
    ).json()["final_action_authorization"]
    deferred_adapter = client.post(
        f"/api/agy/final-action-authorizations/{deferred_auth['final_action_authorization_id']}/quarantined-adapter",
        json={"requested_by": "pytest"},
    ).json()["quarantined_execution_adapter"]
    deferred_canary = client.post(
        f"/api/agy/quarantined-execution-adapters/{deferred_adapter['quarantined_execution_adapter_id']}/sandbox-canary",
        json={"requested_by": "pytest"},
    ).json()["sandboxed_execution_canary"]
    manual_gate = client.get(
        f"/api/agy/sandboxed-execution-canaries/{deferred_canary['sandboxed_execution_canary_id']}/real-executor-arming/preview"
    ).json()["real_executor_arming_gate"]
    assert manual_gate["arming_state"] == "manual_review"
    assert manual_gate["execution_eligibility"]["executed"] is False
    assert all(value is False for value in manual_gate["side_effects"].values())

    missing = client.get("/api/gateway/agy/real-executor-arming-gates/no-such-gate")
    assert missing.status_code == 404
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    text = dashboard.text
    assert "Real Executor Arming Gate" in text
    assert "ONE_AGENT_SANDBOXED_CANARY_TO_REAL_EXECUTOR_ARMING_GATE_OK" in text
    assert "real-executor-arming-gates/latest" in text
    assert "fetchRealExecutorArmingGate" in text
