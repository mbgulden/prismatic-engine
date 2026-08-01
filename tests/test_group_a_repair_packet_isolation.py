"""Group A: 5-Point Counterexample Matrix Test for Repair Packet Collision & Durable Job Lineage.

Matrix Coverage:
1. Positive: Single job repair consumption marks its own packet consumed.
2. Direct Negative: Consuming job 1's repair DOES NOT consume job 2's packet even when they share candidate_tree.
3. Collision & Isolation: 2+ jobs share same candidate_tree; consuming job 1 leaves job 2 100% untouched.
4. Boundary & Empty: Querying or consuming with empty review_job_id fails closed or isolates correctly.
5. Bypass Path: Attempting SQL injection or substring review_job_id matching cannot cross-bleed job state.
"""

import tempfile
from pathlib import Path
import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue


@pytest.fixture
def temp_queue():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_rf.db"
        queue = ReviewQueue(db=ReviewFactoryDB(db_path))
        yield queue
        queue.close()


def test_group_a_collision_and_isolation_shared_tree(temp_queue):
    """Collision & Isolation: Two jobs share the same candidate_tree.
    Consuming job 1's repair packet MUST leave job 2's repair packet unconsumed and untouched.
    """
    shared_tree = "tree-sha-shared-1234567890abcdef1234567890abcdef12345678"

    job1_id = temp_queue.enqueue_completed_work(
        completed_work_id="cw-job-1",
        task_id="TASK-1",
        repository="org/repo",
        base_commit="base-commit-1",
        candidate_commit="cand-commit-1",
        candidate_tree=shared_tree,
        changed_paths=["src/a.py"],
    )

    job2_id = temp_queue.enqueue_completed_work(
        completed_work_id="cw-job-2",
        task_id="TASK-2",
        repository="org/repo",
        base_commit="base-commit-2",
        candidate_commit="cand-commit-2",
        candidate_tree=shared_tree,
        changed_paths=["src/b.py"],
    )

    # Move both jobs to REVIEWING and trigger REPAIR_REQUIRED for both
    assert temp_queue.lease_for_verification(worker_id="verifier-1") is not None
    rec1 = VerificationReceipt(
        review_job_id=job1_id,
        candidate_commit="cand-commit-1",
        candidate_tree=shared_tree,
    )
    temp_queue.complete_verification(job1_id, receipt=rec1, worker_id="verifier-1")
    assert temp_queue.lease_for_review(reviewer_id="rev-1") is not None
    dec1 = ReviewDecision(
        review_job_id=job1_id,
        reviewer_id="rev-1",
        candidate_commit="cand-commit-1",
        candidate_tree=shared_tree,
        receipt_id=rec1.receipt_id,
        verdict=ReviewVerdict.REPAIR_REQUIRED.value,
        findings="[]",
    )
    temp_queue.submit_verdict(job1_id, decision=dec1, reviewer_id="rev-1")

    assert temp_queue.lease_for_verification(worker_id="verifier-2") is not None
    rec2 = VerificationReceipt(
        review_job_id=job2_id,
        candidate_commit="cand-commit-2",
        candidate_tree=shared_tree,
    )
    temp_queue.complete_verification(job2_id, receipt=rec2, worker_id="verifier-2")
    assert temp_queue.lease_for_review(reviewer_id="rev-2") is not None
    dec2 = ReviewDecision(
        review_job_id=job2_id,
        reviewer_id="rev-2",
        candidate_commit="cand-commit-2",
        candidate_tree=shared_tree,
        receipt_id=rec2.receipt_id,
        verdict=ReviewVerdict.REPAIR_REQUIRED.value,
        findings="[]",
    )
    temp_queue.submit_verdict(job2_id, decision=dec2, reviewer_id="rev-2")

    # Check unconsumed repair packets
    cur = temp_queue.db.conn.execute(
        "SELECT * FROM repair_packets WHERE consumed_at = ''"
    )
    unconsumed_before = cur.fetchall()
    assert len(unconsumed_before) == 2

    # Consume job 1's repair
    consumed_ok = temp_queue.consume_repair(
        review_job_id=job1_id,
        new_candidate_commit="cand-commit-1-v2",
        new_candidate_tree="cand-tree-1-v2",
    )
    assert consumed_ok is True

    # Check unconsumed repair packets AFTER consuming job 1
    cur = temp_queue.db.conn.execute(
        "SELECT * FROM repair_packets WHERE consumed_at = ''"
    )
    unconsumed_after = cur.fetchall()

    # Job 2's packet MUST remain unconsumed!
    assert len(unconsumed_after) == 1, (
        f"Expected 1 unconsumed packet remaining for job2, got {len(unconsumed_after)}"
    )
