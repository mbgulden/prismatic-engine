"""Adversarial identity, lease, candidate, receipt, and isolation fences."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue


COMMIT = "a" * 40
TREE = "b" * 40


@pytest.fixture
def queue(tmp_path):
    db = ReviewFactoryDB(tmp_path / "review.db")
    db.ensure_tables()
    value = ReviewQueue(db=db)
    yield value
    value.close()


def _enqueue(queue: ReviewQueue, suffix: str) -> str:
    return queue.enqueue_completed_work(
        completed_work_id=f"cw-{suffix}",
        task_id=f"TASK-{suffix}",
        repository="org/repo",
        base_commit="c" * 40,
        base_tree="d" * 40,
        candidate_commit=COMMIT,
        candidate_tree=TREE,
    )


def _receipt(job_id: str, **overrides: str) -> VerificationReceipt:
    values = {
        "review_job_id": job_id,
        "candidate_commit": COMMIT,
        "candidate_tree": TREE,
        "classification": "targeted",
    }
    values.update(overrides)
    return VerificationReceipt(**values)


def _job_snapshot(queue: ReviewQueue, job_id: str) -> tuple:
    job = queue.db.get_review_job(job_id)
    assert job is not None
    return (
        job.state,
        job.lease_owner,
        job.lease_expires_at,
        job.completed_witnesses,
        len(queue.db.get_receipts_for_job(job_id)),
        len(queue.db.get_decisions_for_job(job_id)),
    )


@pytest.mark.parametrize("worker_id", ["", "   ", "wrong-worker"])
def test_complete_verification_rejects_bad_worker_without_mutation(queue, worker_id):
    job_id = _enqueue(queue, worker_id or "empty")
    queue.lease_for_verification("worker-1")
    before = _job_snapshot(queue, job_id)

    with pytest.raises(ValueError, match="Worker identity"):
        queue.complete_verification(job_id, _receipt(job_id), worker_id=worker_id)

    assert _job_snapshot(queue, job_id) == before


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"review_job_id": "other-job"}, "Cross-job receipt"),
        ({"candidate_commit": "e" * 40}, "candidate commit mismatch"),
        ({"candidate_tree": "f" * 40}, "candidate tree mismatch"),
        ({"candidate_commit": ""}, "Candidate commit required"),
        ({"candidate_tree": ""}, "Candidate tree required"),
    ],
)
def test_complete_verification_rejects_bad_lineage_without_mutation(
    queue, overrides, message
):
    job_id = _enqueue(queue, message[:8])
    queue.lease_for_verification("worker-1")
    before = _job_snapshot(queue, job_id)

    with pytest.raises(ValueError, match=message):
        queue.complete_verification(
            job_id, _receipt(job_id, **overrides), worker_id="worker-1"
        )

    assert _job_snapshot(queue, job_id) == before


def test_complete_verification_rejects_expired_lease_without_mutation(queue):
    job_id = _enqueue(queue, "expired")
    queue.lease_for_verification("worker-1")
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with queue.db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET lease_expires_at = ? WHERE review_job_id = ?",
            (expired, job_id),
        )
    before = _job_snapshot(queue, job_id)

    with pytest.raises(ValueError, match="expired"):
        queue.complete_verification(job_id, _receipt(job_id), worker_id="worker-1")

    assert _job_snapshot(queue, job_id) == before


def test_verdict_fences_and_exact_retry_are_idempotent(queue):
    job_id = _enqueue(queue, "verdict")
    queue.lease_for_verification("worker-1")
    receipt = _receipt(job_id)
    queue.complete_verification(job_id, receipt, worker_id="worker-1")
    queue.lease_for_review("reviewer-1")

    decision = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="reviewer-1",
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        receipt_id=receipt.receipt_id,
        verdict=ReviewVerdict.CLEAN.value,
    )
    before = _job_snapshot(queue, job_id)
    invalid = [
        ("", decision, "Reviewer identity required"),
        ("wrong-reviewer", decision, "Reviewer identity mismatch"),
        (
            "reviewer-1",
            ReviewDecision(
                review_job_id=job_id,
                reviewer_id="reviewer-1",
                candidate_commit=COMMIT,
                candidate_tree="f" * 40,
                receipt_id=receipt.receipt_id,
                verdict=ReviewVerdict.CLEAN.value,
            ),
            "candidate tree mismatch",
        ),
        (
            "reviewer-1",
            ReviewDecision(
                review_job_id=job_id,
                reviewer_id="reviewer-1",
                candidate_commit=COMMIT,
                candidate_tree=TREE,
                receipt_id="missing-receipt",
                verdict=ReviewVerdict.CLEAN.value,
            ),
            "not bound",
        ),
    ]
    for reviewer_id, bad_decision, message in invalid:
        with pytest.raises(ValueError, match=message):
            queue.submit_verdict(job_id, bad_decision, reviewer_id=reviewer_id)
        assert _job_snapshot(queue, job_id) == before

    state = queue.submit_verdict(job_id, decision, reviewer_id="reviewer-1")
    assert state == ReviewJobState.MERGE_READY.value
    after_first = _job_snapshot(queue, job_id)
    assert after_first[-2:] == (1, 1)

    # Exact retries remain no-ops after the accepted verdict clears the lease.
    assert (
        queue.submit_verdict(job_id, decision, reviewer_id="reviewer-1")
        == ReviewJobState.MERGE_READY.value
    )
    assert _job_snapshot(queue, job_id) == after_first


def test_verdict_rejects_expired_lease_without_mutation(queue):
    job_id = _enqueue(queue, "review-expired")
    queue.lease_for_verification("worker-1")
    receipt = _receipt(job_id)
    queue.complete_verification(job_id, receipt, worker_id="worker-1")
    queue.lease_for_review("reviewer-1")
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with queue.db.transaction() as cur:
        cur.execute(
            "UPDATE review_jobs SET lease_expires_at = ? WHERE review_job_id = ?",
            (expired, job_id),
        )
    decision = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="reviewer-1",
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        receipt_id=receipt.receipt_id,
        verdict=ReviewVerdict.CLEAN.value,
    )
    before = _job_snapshot(queue, job_id)

    with pytest.raises(ValueError, match="expired"):
        queue.submit_verdict(job_id, decision, reviewer_id="reviewer-1")

    assert _job_snapshot(queue, job_id) == before
