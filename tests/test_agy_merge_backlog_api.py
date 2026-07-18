from copy import deepcopy

from fastapi.testclient import TestClient

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.completed_work_gate import demo_completed_work_packet
from prismatic.gateway import server


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
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    row = ingest_completed_work(packet(), db_path=db)
    return row


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
