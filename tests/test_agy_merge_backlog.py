from copy import deepcopy

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.agy_executor_runs import (
    PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED,
    PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER,
    build_prompt6_executor_canary_dry_run,
    get_executor_run,
    list_executor_runs,
    record_executor_run,
)
from prismatic.agy_merge_backlog import (
    AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER,
    AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
    AGY_PR_VERIFICATION_GATE_MARKER,
    PROMPT5_APPROVED_REAL_PR_EXECUTOR_MARKER,
    PROMPT5_REAL_PR_APPROVAL_GATE_MARKER,
    build_approved_real_pr_executor_plan,
    build_merge_backlog_item,
    build_real_pr_creation_approval_gate,
    build_real_pr_creation_approved_action,
    execute_approved_real_pr_creation,
    get_merge_backlog_item,
    verify_merge_backlog_item,
)
from prismatic.completed_work_gate import demo_completed_work_packet


def packet(
    *,
    lane="backend-api",
    changed_files=None,
    result="PASS",
    command="python3 -m pytest -q tests/test_agy_merge_backlog.py",
    marker="AGY_PR_VERIFICATION_GATE_OK",
):
    p = deepcopy(demo_completed_work_packet())
    p["issue_identifier"] = "GRO-3837"
    p["source_branch"] = "feature/agy/GRO-3837-clean-pr"
    p["base_branch"] = "origin/main"
    p["verification_lane"] = lane
    p["changed_files"] = changed_files or [
        "prismatic/agy_merge_backlog.py",
        "tests/test_agy_merge_backlog.py",
    ]
    p["proof"].update(
        {
            "result": result,
            "command": command,
            "scope": f"{lane} verification proof",
            "log": "/tmp/fred-agy-clean-pr-verification-gate-verify.log",
            "marker": marker,
            "non_claims": ["auto_merge", "production_deploy"],
        }
    )
    return p


def ingest(db_path, pkt=None, **kwargs):
    return ingest_completed_work(pkt or packet(), db_path=db_path, **kwargs)


def test_merge_ready_row_becomes_open_or_update_pr_dry_run_plan(tmp_path):
    row = ingest(tmp_path / "cw.db")

    item = build_merge_backlog_item(row)

    assert item.recommended_action == "open_or_update_pr"
    assert item.classification == "merge_ready"
    assert item.base_branch == "main"
    assert item.issue_identifier == "GRO-3837"
    assert item.pr_branch.startswith("feature/agy-clean-pr-gro-3837-")
    assert item.verification_gate == "pass"
    assert item.verification_lane == "backend-api"
    assert item.eligible_for_auto_merge is False
    assert item.dry_run is True
    assert AGY_CLEAN_PR_CREATE_UPDATE_MARKER in item.markers
    assert AGY_PR_VERIFICATION_GATE_MARKER in item.markers
    assert AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER in item.markers
    assert "auto_merge: false" in item.pr_body


def test_clean_rebuild_required_blocks_direct_pr(tmp_path):
    row = ingest(tmp_path / "cw.db", dirty_source=True)

    item = build_merge_backlog_item(row)

    assert item.recommended_action == "clean_rebuild_required"
    assert item.verification_gate == "blocked"
    assert item.eligible_for_auto_merge is False
    assert any("clean_rebuild_required" in reason for reason in item.reasons)


def test_failed_verification_maps_to_blocked(tmp_path):
    row = ingest(tmp_path / "cw.db", packet(result="FAIL"))

    item = build_merge_backlog_item(row)

    assert item.recommended_action == "blocked_failed_verification"
    assert item.verification_gate == "blocked"
    assert item.eligible_for_auto_merge is False


def test_manual_review_conflict_stays_manual(tmp_path):
    row = ingest(tmp_path / "cw.db", conflicts=["prismatic/agy_merge_backlog.py"])

    item = build_merge_backlog_item(row)

    assert item.recommended_action == "manual_review_conflict"
    assert item.verification_gate == "manual_review"
    assert item.eligible_for_auto_merge is False


def test_verification_gate_handles_dashboard_backend_docs_research_lanes(tmp_path):
    cases = [
        (
            "backend-api",
            ["prismatic/agy_merge_backlog.py"],
            "python3 -m pytest -q tests/test_agy_merge_backlog.py",
            "pass",
        ),
        (
            "dashboard-ui",
            ["prismatic/gateway/templates/dashboard.html"],
            "node --check /tmp/hermes-dashboard-inline-agy-merge-backlog.js && curl /dashboard",
            "pass",
        ),
        (
            "docs",
            ["docs/agy.md"],
            "python3 - <<'PY'\nprint('/tmp/doc-proof')\nPY",
            "pass",
        ),
        (
            "research",
            ["scripts/reports/agy.md"],
            "python3 - <<'PY'\nprint('/tmp/research-proof')\nPY",
            "pass",
        ),
        (
            "mixed",
            ["prismatic/agy_merge_backlog.py", "docs/agy.md"],
            "python3 -m pytest -q tests/test_agy_merge_backlog.py",
            "manual_review",
        ),
        (
            "unknown",
            ["weird/file.bin"],
            "python3 -m pytest -q tests/test_agy_merge_backlog.py",
            "manual_review",
        ),
    ]
    for lane, files, command, expected in cases:
        row = ingest(
            tmp_path / f"{lane}.db",
            packet(lane=lane, changed_files=files, command=command),
        )
        item = build_merge_backlog_item(row)
        assert item.verification_lane == lane
        assert item.verification_gate == expected
        assert item.eligible_for_auto_merge is False


def test_dashboard_lane_blocks_without_js_dashboard_proof(tmp_path):
    row = ingest(
        tmp_path / "cw.db",
        packet(
            lane="dashboard-ui",
            changed_files=["prismatic/gateway/templates/dashboard.html"],
            command="python3 -m pytest -q tests/test_agy_merge_backlog.py",
        ),
    )

    item = build_merge_backlog_item(row)

    assert item.verification_gate == "blocked"
    assert any("node --check" in reason for reason in item.reasons)


def test_get_and_verify_helpers_use_persisted_rows(tmp_path):
    row = ingest(tmp_path / "cw.db")

    item = get_merge_backlog_item(row.id, db_path=tmp_path / "cw.db")
    verified = verify_merge_backlog_item(row.id, db_path=tmp_path / "cw.db")

    assert item.completed_work_id == row.id
    assert verified["verification_gate"] == "pass"
    assert verified["eligible_for_auto_merge"] is False
    assert verified["non_claims"]["auto_merge"] is False


def test_prompt5_pr_candidate_lifecycle_is_metadata_only(monkeypatch, tmp_path):
    from prismatic.agy_merge_backlog import (
        PROMPT5_PR_CANDIDATE_LIFECYCLE_MARKER,
        build_pr_candidate_lifecycle,
    )

    db = tmp_path / "candidate.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    row = ingest(db, packet(lane="backend-api"))
    candidate = build_pr_candidate_lifecycle(
        row.id,
        requested_by="kai-test",
        action="stage_pr_candidate",
    )

    assert candidate["marker"] == PROMPT5_PR_CANDIDATE_LIFECYCLE_MARKER
    assert candidate["status"] == "ok"
    assert candidate["lifecycle_state"] == "candidate_metadata_ready"
    assert candidate["operator_action_required"] is True
    assert candidate["candidate"]["eligible_for_auto_merge"] is False
    assert candidate["side_effects"]["candidate_metadata_created"] is True
    assert candidate["side_effects"]["github_pr_created"] is False
    assert candidate["side_effects"]["auto_merge"] is False
    assert candidate["side_effects"]["production_deploy"] is False
    assert candidate["non_claims"]["real_github_pr_created"] is False


def test_prompt53_operator_pr_creation_dry_run_is_side_effect_free(
    monkeypatch, tmp_path
):
    from prismatic.agy_merge_backlog import (
        PROMPT5_OPERATOR_PR_DRY_RUN_MARKER,
        build_operator_pr_creation_dry_run,
    )

    db = tmp_path / "candidate.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    row = ingest(db, packet(lane="backend-api"))

    plan = build_operator_pr_creation_dry_run(
        row.id, requested_by="kai-test", action="operator_pr_creation_dry_run"
    )

    assert plan["status"] == "ok"
    assert plan["marker"] == PROMPT5_OPERATOR_PR_DRY_RUN_MARKER
    assert plan["operator_approved_action"] is True
    assert plan["dry_run_only"] is True
    assert plan["branch_plan"]["executed"] is False
    assert plan["github_pr_plan"]["created"] is False
    assert plan["github_pr_plan"]["create_command"].startswith("DRY_RUN_ONLY:")
    assert plan["verification_gate_selection"]["gate"] == "backend_api_focused"
    assert plan["verification_gate_selection"]["required_before_real_pr"] is True
    assert plan["linear_writeback"]["enabled"] is True
    assert plan["linear_writeback"]["posted"] is False
    assert plan["linear_writeback"]["dry_run_payload_only"] is True
    assert plan["side_effects"]["git_branch_created"] is False
    assert plan["side_effects"]["github_pr_created"] is False
    assert plan["side_effects"]["auto_merge"] is False
    assert plan["side_effects"]["production_deploy"] is False
    assert plan["side_effects"]["linear_comment_posted"] is False
    assert plan["non_claims"]["real_github_pr_created"] is False
    assert "PROMPT5_OPERATOR_PR_DRY_RUN_OK" in plan["linear_writeback"]["body"]


def test_prompt54_real_pr_creation_approval_gate_requires_explicit_token(
    monkeypatch, tmp_path
):
    db = tmp_path / "approval.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    row = ingest(db, packet(lane="backend-api"))

    blocked = build_real_pr_creation_approval_gate(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token="wrong-token",
    )
    assert blocked["status"] == "blocked"
    assert blocked["approval_record"]["approved"] is False
    assert blocked["approval_record"]["approval_token_matched"] is False
    assert blocked["policy_gate"]["checks"]["approval_token_matches"] is False
    assert blocked["real_pr_creation_action"]["exposed"] is False
    assert blocked["side_effects"]["github_pr_created"] is False

    approved = build_real_pr_creation_approval_gate(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
        approval_note="explicit approval test",
    )
    assert approved["status"] == "ok"
    assert approved["marker"] == PROMPT5_REAL_PR_APPROVAL_GATE_MARKER
    assert approved["approval_record"]["approved"] is True
    assert approved["approval_record"]["approval_token_matched"] is True
    assert approved["policy_gate"]["status"] == "pass"
    assert approved["policy_gate"]["scope_confirmed"] is True
    assert approved["policy_gate"]["proof_confirmed"] is True
    assert approved["policy_gate"]["requires_separate_approved_action"] is True
    assert approved["real_pr_creation_action"]["exposed"] is True
    assert (
        approved["real_pr_creation_action"]["requires_final_operator_trigger"] is True
    )
    assert approved["real_pr_creation_action"]["executed"] is False
    assert approved["real_pr_creation_action"]["github_pr_created"] is False
    assert approved["side_effects"]["git_branch_created"] is False
    assert approved["side_effects"]["github_pr_created"] is False
    assert approved["side_effects"]["linear_comment_posted"] is False
    assert approved["non_claims"]["real_github_pr_created"] is False

    action = build_real_pr_creation_approved_action(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
        approval_id=approved["approval_record"]["approval_id"],
    )
    assert action["status"] == "ok"
    assert action["marker"] == PROMPT5_REAL_PR_APPROVAL_GATE_MARKER
    assert action["real_pr_creation_action"]["status"] == "ready_for_future_executor"
    assert action["real_pr_creation_action"]["approval_id_matched"] is True
    assert action["real_pr_creation_action"]["execution_implemented"] is False
    assert action["real_pr_creation_action"]["executed"] is False
    assert action["side_effects"]["github_pr_created"] is False


def test_prompt55_approved_real_pr_executor_guards_modes(monkeypatch, tmp_path):
    db = tmp_path / "executor.db"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db))
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_PR_EXECUTOR", raising=False)
    row = ingest(db, packet(lane="backend-api"))

    missing = build_approved_real_pr_executor_plan(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
        final_operator_trigger=True,
        execute=False,
        executor_mode="dry_run",
    )
    assert missing["status"] == "blocked"
    assert "approval_id_matched" in missing["policy_gate"]["blocked_reasons"]
    assert missing["commands_rendered"] is True
    assert missing["commands_executed"] is False
    assert missing["side_effects"]["real_github_pr_created"] is False

    gate = build_real_pr_creation_approval_gate(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
    )
    approval_id = gate["approval_record"]["approval_id"]

    wrong = build_approved_real_pr_executor_plan(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token="wrong-token",
        approval_id=approval_id,
        final_operator_trigger=True,
        execute=False,
        executor_mode="dry_run",
    )
    assert wrong["status"] == "blocked"
    assert wrong["side_effects"]["github_pr_created"] is False

    dry = build_approved_real_pr_executor_plan(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
        approval_id=approval_id,
        final_operator_trigger=True,
        execute=False,
        executor_mode="dry_run",
    )
    assert dry["status"] == "ok"
    assert dry["marker"] == PROMPT5_APPROVED_REAL_PR_EXECUTOR_MARKER
    assert dry["executor_mode"] == "dry_run"
    assert dry["commands_rendered"] is True
    assert dry["commands_executed"] is False
    assert any(
        command.startswith("git switch -C")
        for command in dry["executor_plan"]["commands"]
    )
    assert any(
        "gh pr create" in command for command in dry["executor_plan"]["commands"]
    )
    assert dry["executor_result"]["real_github_pr_created"] is False
    assert dry["side_effects"]["git_branch_created"] is False
    assert dry["side_effects"]["github_pr_created"] is False
    assert dry["side_effects"]["auto_merge"] is False
    assert dry["side_effects"]["production_deploy"] is False

    mocked = execute_approved_real_pr_creation(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
        approval_id=approval_id,
        final_operator_trigger=True,
        execute=True,
        executor_mode="mocked",
    )
    assert mocked["status"] == "ok"
    assert mocked["commands_executed"] is True
    assert mocked["executor_backend"] == "mock"
    assert mocked["executor_result"]["mocked"] is True
    assert mocked["executor_result"]["github_pr_created"] is True
    assert mocked["executor_result"]["real_github_pr_created"] is False
    assert mocked["side_effects"]["mock_github_pr_created"] is True
    assert mocked["side_effects"]["real_github_pr_created"] is False

    real_blocked = execute_approved_real_pr_creation(
        row.id,
        requested_by="kai-test",
        approved_by="operator",
        approval_token=f"APPROVE_REAL_PR:{row.id}",
        approval_id=approval_id,
        final_operator_trigger=True,
        execute=True,
        executor_mode="real",
        allow_real_side_effects=True,
        command_runner=lambda command: (_ for _ in ()).throw(AssertionError(command)),
    )
    assert real_blocked["status"] == "blocked"
    assert "real_executor_env_enabled" in real_blocked["policy_gate"]["blocked_reasons"]
    assert real_blocked["commands_executed"] is False
    assert real_blocked["side_effects"]["real_github_pr_created"] is False
    assert real_blocked["non_claims"]["auto_merge_enabled"] is False
    assert real_blocked["non_claims"]["production_deployed"] is False


def test_prompt6_executor_run_records_persist_and_are_readable(tmp_path):
    payload = {
        "status": "ok",
        "marker": PROMPT5_APPROVED_REAL_PR_EXECUTOR_MARKER,
        "completed_work_id": "cw-prompt6",
        "requested_by": "test",
        "executor_mode": "dry_run",
        "commands_rendered": True,
        "commands_executed": False,
        "executor_plan": {"execute_requested": False, "allow_real_side_effects": False},
        "executor_result": {
            "real_github_pr_created": False,
            "git_branch_created": False,
        },
        "side_effects": {
            "real_github_pr_created": False,
            "git_branch_created": False,
        },
        "approval_gate": {"approval_record": {"approval_id": "approval-prompt6"}},
        "policy_gate": {"blocked_reasons": []},
    }
    state = tmp_path / "executor-runs.json"

    run = record_executor_run(payload, requested_by="test", state_path=state)

    assert run["marker"] == PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER
    assert run["completed_work_id"] == "cw-prompt6"
    assert run["commands_rendered"] is True
    assert run["commands_executed"] is False
    assert run["real_github_pr_created"] is False
    assert run["git_branch_created"] is False
    listed = list_executor_runs(state_path=state)
    assert listed["count"] == 1
    assert listed["runs"][0]["run_id"] == run["run_id"]
    detail = get_executor_run(run["run_id"], state_path=state)
    assert detail["run"]["approval_id"] == "approval-prompt6"


def test_prompt6_canary_dry_run_records_safe_executor_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(tmp_path / "cw.db"))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_PR_EXECUTOR", raising=False)

    canary = build_prompt6_executor_canary_dry_run(
        requested_by="test",
        state_path=tmp_path / "runs.json",
    )

    assert canary["status"] == "ok"
    assert canary["marker"] == PROMPT6_EXECUTOR_AUDIT_CANARY_MARKER
    assert canary["executor_mode"] == "dry_run"
    assert canary["commands_rendered"] is True
    assert canary["commands_executed"] is False
    assert canary["real_github_pr_created"] is False
    assert canary["git_branch_created"] is False
    assert canary["auto_merge_enabled"] is False
    assert canary["production_deployed"] is False
    assert canary["AGY_dispatch"] is False
    assert canary["executor_result"]["executor_plan"]["execute_requested"] is False
    assert (
        canary["executor_result"]["executor_plan"]["allow_real_side_effects"] is False
    )
    listed = list_executor_runs(state_path=tmp_path / "runs.json")
    assert listed["runs"][0]["run_id"] == canary["run_id"]


def test_prompt6_real_mode_canary_blocks_without_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(tmp_path / "cw.db"))
    monkeypatch.setenv("PRISMATIC_AGY_EXECUTOR_RUNS_STATE", str(tmp_path / "runs.json"))
    monkeypatch.delenv("PRISMATIC_ALLOW_REAL_PR_EXECUTOR", raising=False)

    blocked = build_prompt6_executor_canary_dry_run(
        requested_by="test",
        executor_mode="real",
        execute=True,
        allow_real_side_effects=True,
        state_path=tmp_path / "runs.json",
    )

    assert blocked["status"] == "blocked"
    assert blocked["marker"] == PROMPT6_EXECUTOR_AUDIT_CANARY_BLOCKED
    assert blocked["executor_mode"] == "real"
    assert blocked["commands_rendered"] is True
    assert blocked["commands_executed"] is False
    assert blocked["real_github_pr_created"] is False
    assert blocked["git_branch_created"] is False
    assert "real_executor_env_enabled" in blocked["blocked_reasons"]
