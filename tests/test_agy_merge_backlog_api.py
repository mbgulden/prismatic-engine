from copy import deepcopy
from pathlib import Path

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


def seed(monkeypatch, tmp_path):
    db = tmp_path / "completed_work.db"
    executor_runs = tmp_path / "executor-runs.json"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(executor_runs))
    row = ingest_completed_work(packet(), db_path=db)
    return row


def test_completed_work_api_ingests_text_and_lists_bridge_payload(
    monkeypatch, tmp_path
):
    db = tmp_path / "completed_work.db"
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


def test_dashboard_renders_one_agent_completed_work_bridge_marker():
    html = Path("prismatic/gateway/templates/dashboard.html").read_text(
        encoding="utf-8"
    )
    assert "ONE_AGENT_COMPLETED_WORK_TO_VERIFIED_PR_DRY_RUN_OK" in html
    assert "verified-pr-dry-run/latest" in html
    assert "Verified PR Dry Run" in html


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
