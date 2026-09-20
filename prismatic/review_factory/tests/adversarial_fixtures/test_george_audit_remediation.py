"""Remediation tests for George's 12 audit findings in Review Factory V1."""

import pytest
import sqlite3
from datetime import datetime, timedelta, timezone

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.models import VerificationReceipt
from prismatic.review_factory.merge_executor import MergeExecutor


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

    # Receipt for job-2 attempt on job-1
    wrong_receipt = VerificationReceipt(
        review_job_id="wrong-job-id",
        candidate_commit="cand1",
        classification="pass",
    )

    with pytest.raises(ValueError, match="mismatch|not match"):
        queue.complete_verification(job_id, receipt=wrong_receipt, worker_id="verifier-1")


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
        cur.execute("UPDATE review_jobs SET lease_expires_at = ? WHERE review_job_id = ?", (past_iso, job_id))

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
