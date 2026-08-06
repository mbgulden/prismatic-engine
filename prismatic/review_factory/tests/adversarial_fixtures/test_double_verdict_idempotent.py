"""Adversarial: Submitting the same verdict twice is a no-op.

The idempotency_key ensures that duplicate decisions don't corrupt
the state machine.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.testing import enqueue_with_defaults


@pytest.fixture
def queue(tmp_path):
    db = ReviewFactoryDB(db_path=tmp_path / "test_double.db")
    db.ensure_tables()
    q = ReviewQueue(db=db)
    yield q
    q.close()


def test_double_verdict_idempotent(queue):
    """Submitting the exact same verdict twice is a no-op."""
    job_id = enqueue_with_defaults(
        queue,
        completed_work_id="agy-cw-double",
        task_id="GRO-DOUBLE",
        base_commit="aaaa",
        candidate_commit="bbbb",
        changed_paths=["prismatic/core/router.py"],
    )

    job = queue.lease_for_verification("v1")
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="bbbb",
        candidate_tree="bbbb",
    )
    queue.complete_verification(job_id, receipt, worker_id="v1")

    job = queue.lease_for_review("reviewer-1")
    decision = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="reviewer-1",
        candidate_commit="bbbb",
        candidate_tree="bbbb",
        receipt_id=receipt.receipt_id,
        verdict=ReviewVerdict.CLEAN.value,
    )

    # Submit once
    state1 = queue.submit_verdict(job_id, decision, reviewer_id="reviewer-1")
    assert state1 == ReviewJobState.MERGE_READY.value

    # Submit again with SAME idempotency key — should be a no-op
    state2 = queue.submit_verdict(job_id, decision, reviewer_id="reviewer-1")
    assert state2 == ReviewJobState.MERGE_READY.value

    # Job state should still be merge_ready
    job = queue.db.get_review_job(job_id)
    assert job.state == ReviewJobState.MERGE_READY.value

    # Only one decision should exist
    decisions = queue.db.get_decisions_for_job(job_id)
    assert len(decisions) == 1
