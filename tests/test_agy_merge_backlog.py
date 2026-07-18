from copy import deepcopy

from prismatic.agy_completed_work import ingest_completed_work
from prismatic.agy_merge_backlog import (
    AGY_CLEAN_PR_AND_VERIFICATION_GATE_MARKER,
    AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
    AGY_PR_VERIFICATION_GATE_MARKER,
    build_merge_backlog_item,
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
