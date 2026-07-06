from pathlib import Path

from prismatic.execution_proof import (
    LinearSyncEvidence,
    ProofStatus,
    WorkerRunEvidence,
    assess_execution_proof,
)


def test_assess_execution_proof_passes_with_artifact_and_linear_evidence(tmp_path: Path) -> None:
    artifact = tmp_path / "GRO-3475-result.md"
    artifact.write_text("proof", encoding="utf-8")

    run = WorkerRunEvidence(
        issue_id="GRO-3475",
        lane="agent:ned",
        run_id="run-1",
        started_at="2026-07-06T20:00:00Z",
        completed_at="2026-07-06T20:02:00Z",
        exit_code=0,
        result_artifacts=(artifact.name,),
    )
    linear = LinearSyncEvidence(
        issue_id="GRO-3475",
        final_state="In Review",
        labels=("agent:ned",),
        final_comment_id="comment-1",
        final_comment_body="GRO-3475 completed with artifact GRO-3475-result.md",
    )

    report = assess_execution_proof(run, linear, artifact_root=tmp_path)

    assert report.status is ProofStatus.PASS
    assert report.failures == ()
    assert report.warnings == ()
    assert report.trace["artifact_count"] == 1
    assert report.to_dict()["status"] == "pass"


def test_missing_artifact_fails_even_when_path_is_recorded(tmp_path: Path) -> None:
    run = WorkerRunEvidence(
        issue_id="GRO-3475",
        lane="agent:ned",
        run_id="run-1",
        started_at="2026-07-06T20:00:00Z",
        completed_at="2026-07-06T20:02:00Z",
        exit_code=0,
        result_artifacts=("missing.md",),
    )
    linear = LinearSyncEvidence(
        issue_id="GRO-3475",
        final_state="In Review",
        labels=("agent:ned",),
        final_comment_id="comment-1",
        final_comment_body="GRO-3475 completed with artifact missing.md",
    )

    report = assess_execution_proof(run, linear, artifact_root=tmp_path)

    assert report.status is ProofStatus.FAIL
    assert "result artifact path recorded but missing on disk" in report.failures


def test_identity_mismatch_fails() -> None:
    run = WorkerRunEvidence(
        issue_id="GRO-3475",
        lane="agent:ned",
        run_id="run-1",
        started_at="2026-07-06T20:00:00Z",
        completed_at="2026-07-06T20:02:00Z",
        exit_code=0,
        result_artifacts=(__file__,),
    )
    linear = LinearSyncEvidence(
        issue_id="GRO-9999",
        final_state="In Review",
        labels=("agent:ned",),
        final_comment_id="comment-1",
        final_comment_body="GRO-9999 completed with artifact test_execution_proof.py",
    )

    report = assess_execution_proof(run, linear)

    assert report.status is ProofStatus.FAIL
    assert "issue identity mismatch between worker run and Linear sync" in report.failures


def test_missing_terminal_state_or_done_label_warns(tmp_path: Path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_text("{}", encoding="utf-8")
    run = WorkerRunEvidence(
        issue_id="GRO-3475",
        lane="agent:ned",
        run_id="run-1",
        started_at="2026-07-06T20:00:00Z",
        completed_at="2026-07-06T20:02:00Z",
        exit_code=0,
        result_artifacts=(str(artifact),),
    )
    linear = LinearSyncEvidence(
        issue_id="GRO-3475",
        final_state="Backlog",
        labels=("agent:ned",),
        final_comment_id="comment-1",
        final_comment_body=f"GRO-3475 completed with artifact {artifact}",
    )

    report = assess_execution_proof(run, linear)

    assert report.status is ProofStatus.WARN
    assert "Linear state/labels are not terminal for completed work" in report.warnings


def test_mapping_adapters_accept_common_dispatcher_aliases(tmp_path: Path) -> None:
    artifact = tmp_path / "out.txt"
    artifact.write_text("ok", encoding="utf-8")

    run = WorkerRunEvidence.from_mapping(
        {
            "identifier": "GRO-3475",
            "agent_label": "agent:ned",
            "id": "run-1",
            "launched_at": "2026-07-06T20:00:00Z",
            "finished_at": "2026-07-06T20:02:00Z",
            "status": "completed",
            "artifacts": [str(artifact)],
        }
    )
    linear = LinearSyncEvidence.from_mapping(
        {
            "identifier": "GRO-3475",
            "final_state": "Done",
            "labels": ["agent:done"],
            "final_comment_id": "comment-1",
            "final_comment_body": f"GRO-3475 completed with artifact {artifact.name}",
        }
    )

    report = assess_execution_proof(run, linear)

    assert report.status is ProofStatus.PASS
    assert report.trace["run_id"] == "run-1"


def test_missing_comment_body_link_is_warning_not_failure(tmp_path: Path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("ok", encoding="utf-8")
    run = WorkerRunEvidence(
        issue_id="GRO-3475",
        lane="agent:ned",
        run_id="run-1",
        started_at="2026-07-06T20:00:00Z",
        completed_at="2026-07-06T20:02:00Z",
        exit_code=0,
        result_artifacts=(str(artifact),),
    )
    linear = LinearSyncEvidence(
        issue_id="GRO-3475",
        final_state="In Review",
        labels=("agent:ned",),
        final_comment_id="comment-1",
        final_comment_body="completed",
    )

    report = assess_execution_proof(run, linear)

    assert report.status is ProofStatus.WARN
    assert "Linear comment body does not link issue and artifact evidence" in report.warnings
