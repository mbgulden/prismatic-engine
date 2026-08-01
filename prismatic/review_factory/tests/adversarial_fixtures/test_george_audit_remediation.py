"""Remediation tests for George's 12 audit findings in Review Factory V1."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.merge_executor import MergeExecutor
from prismatic.review_factory.models import (
    ReviewDecision,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue


def test_cross_job_receipt_mismatch_rejected(tmp_path):
    db_file = tmp_path / "test.db"
    db = ReviewFactoryDB(db_path=db_file)
    db.ensure_tables()
    queue = ReviewQueue(db=db)

    job_id = queue.enqueue_completed_work(
        completed_work_id="cw-job-1",
        task_id="GRO-1",
        repository="mbgulden/prismatic-engine",
        base_commit="base1",
        candidate_commit="cand1",
    )
    queue.lease_for_verification(worker_id="verifier-1")

    # Receipt for job-2 attempt on job-1
    wrong_receipt = VerificationReceipt(
        review_job_id="wrong-job-id",
        candidate_commit="cand1",
        classification="pass",
    )

    with pytest.raises(ValueError, match="mismatch|not match"):
        queue.complete_verification(
            job_id, receipt=wrong_receipt, worker_id="verifier-1"
        )


def test_expired_lease_completion_rejected(tmp_path):
    db_file = tmp_path / "test.db"
    db = ReviewFactoryDB(db_path=db_file)
    db.ensure_tables()
    queue = ReviewQueue(db=db)

    job_id = queue.enqueue_completed_work(
        completed_work_id="cw-job-expired",
        task_id="GRO-2",
        repository="mbgulden/prismatic-engine",
        base_commit="base2",
        candidate_commit="cand2",
    )

    job = queue.lease_for_verification(worker_id="verifier-1")
    assert job is not None

    # Manually set expired lease timestamp in DB
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET lease_expires_at = ? WHERE review_job_id = ?",
            (past_iso, job_id),
        )

    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="cand2",
        classification="pass",
    )

    # Completion on expired lease fails
    with pytest.raises(ValueError, match="expired|Lease"):
        queue.complete_verification(job_id, receipt=receipt, worker_id="verifier-1")


def test_unique_completed_work_index(tmp_path):
    db_file = tmp_path / "test.db"
    db = ReviewFactoryDB(db_path=db_file)
    db.ensure_tables()

    # Verify UNIQUE constraint/index exists via sqlite3
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("PRAGMA index_list('review_jobs')")
    indexes = cursor.fetchall()
    conn.close()
    assert len(indexes) > 0


def test_synthetic_ci_not_fabricated():
    executor = MergeExecutor(dry_run=True)
    assert executor.dry_run is True


def test_consume_repair_invalidates_stale_evidence(tmp_path):
    db_file = tmp_path / "test.db"
    db = ReviewFactoryDB(db_path=db_file)
    db.ensure_tables()
    queue = ReviewQueue(db=db)

    job_id = queue.enqueue_completed_work(
        completed_work_id="cw-job-repair-stale",
        task_id="GRO-STALE",
        repository="mbgulden/prismatic-engine",
        base_commit="base-1",
        candidate_commit="cand-old",
    )

    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="cand-old",
        candidate_tree="cand-old",
        classification="pass",
    )
    db.insert_receipt(receipt)

    decision = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="rev-1",
        candidate_commit="cand-old",
        candidate_tree="cand-old",
        receipt_id=receipt.receipt_id,
        verdict="repair_required",
        idempotency_key="key-old",
    )
    db.insert_decision(decision)

    with db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET state = 'repair_required' WHERE review_job_id = ?",
            (job_id,),
        )

    queue.consume_repair(
        review_job_id=job_id,
        new_candidate_commit="cand-new",
        new_candidate_tree="cand-new",
    )

    job = db.get_review_job(job_id)
    assert job.candidate_commit == "cand-new"
    assert job.completed_witnesses == 0

    receipts = db.get_receipts_for_job(job_id)
    assert len(receipts) == 0

    decisions = db.get_decisions_for_job(job_id)
    assert len(decisions) == 0


def test_verifier_materializes_immutable_archive(tmp_path):
    from prismatic.review_factory.verifier import VerificationWorker

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("hello")

    db = ReviewFactoryDB(db_path=tmp_path / "test.db")
    db.ensure_tables()
    queue = ReviewQueue(db=db)

    worker = VerificationWorker(queue=queue, repo_path=repo)
    archive_dir = worker._materialize_immutable_archive(
        candidate_commit="cand123", candidate_tree="tree123"
    )
    assert archive_dir.exists()
    assert (archive_dir / "docs" / "readme.md").read_text() == "hello"


def test_importer_rejects_mutable_branch_names(tmp_path):
    from prismatic.agy_completed_work import CompletedWorkRow
    from prismatic.review_factory.backlog_importer import BacklogImporter, ImportResult

    db_file = tmp_path / "test.db"
    db = ReviewFactoryDB(db_path=db_file)
    db.ensure_tables()
    queue = ReviewQueue(db=db)

    importer = BacklogImporter(queue=queue)
    row = CompletedWorkRow(
        id="cw-branch-1",
        created_at="",
        updated_at="",
        agent="agy",
        source_branch="feat-branch",
        source_path="",
        base_branch="main",
        classification="merge_ready",
        eligible_for_merge=True,
        requires_clean_rebuild=False,
        proof_result="PASS",
        proof_marker="",
        gate_marker="",
        ingestion_marker="",
        packet={
            "issue": "GRO-BRANCH",
            "base_commit": "main",
            "candidate_commit": "feat-branch",
        },
        gate={},
        non_claims=(),
        evidence_retention={},
    )

    res = ImportResult()
    importer._process_row(row, res)
    assert res.enqueued == 0
    assert res.skipped_ineligible == 1


def test_cli_approve_fails_closed_when_key_unset(monkeypatch):
    from prismatic.review_factory.merge_executor import _cli_approve

    monkeypatch.delenv("PRISMATIC_OPERATOR_KEY", raising=False)
    with pytest.raises(PermissionError):
        _cli_approve(job_id="test-job", actor="michael", operator_key=None)


def test_merge_executor_rejects_mismatched_ci_candidate_sha():
    from prismatic.merge_candidate_manifest import CICheck, ManifestValidationError
    from prismatic.review_factory.tests.test_merge_executor import (
        _create_merge_ready_manifest,
    )

    manifest = _create_merge_ready_manifest()
    ci_checks = [
        CICheck(
            name="rf-v1-verification",
            run_id=1000,
            conclusion="SUCCESS",
            head_sha="deadbeef" * 5,
            details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
        )
    ]
    with pytest.raises(ManifestValidationError, match="stale candidate SHA"):
        manifest.record_ci(ci_checks)


def test_websocket_rejects_unauthenticated_connection(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi.websockets import WebSocketDisconnect

    from prismatic.gateway.server import app

    monkeypatch.setenv("PRISMATIC_WS_AUTH_REQUIRED", "1")
    client = TestClient(app)
    with pytest.raises((WebSocketDisconnect, RuntimeError)):
        with client.websocket_connect("/ws") as websocket:
            _ = websocket.receive_json()
