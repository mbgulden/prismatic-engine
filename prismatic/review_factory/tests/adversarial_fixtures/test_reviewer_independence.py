import pytest
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.testing import enqueue_with_defaults
from prismatic.review_factory.models import ReviewDecision, ReviewVerdict, VerificationReceipt
from prismatic.agy_completed_work import AgyCompletedWorkStore, CompletedWorkRow

def _make_test_packet(issue: str, agent: str) -> dict:
    """Create a minimal AGY result packet for ingestion."""
    return {
        "issue_identifier": issue,
        "source_branch": "feature/test",
        "base_branch": "main",
        "agent": agent,
        "repository": "mbgulden/prismatic-engine",
        "changed_files": ["docs/readme.md"],
        "result_summary": "Test completed",
        "source_path": "/tmp/test-source",
        "proof": {
            "COMMAND": "pytest tests/ -x -q",
            "RESULT": "PASS",
            "LOG": "/tmp/test.log",
            "SCOPE": "focused",
            "AD_HOC_OR_CANONICAL": "canonical",
            "NOT_CLAIMING": ["integration"],
            "MARKER": "AGY_TASK_RESULT_PACKET_OK",
        },
    }

def test_reviewer_cannot_lease_own_job(tmp_path):
    """Enforce reviewer independence: a reviewer cannot lease a job they produced."""
    db_path = tmp_path / "test_independence.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    q = ReviewQueue(db=db)

    # Ingest a completed work by agent 'agy' into the completed work store
    store = AgyCompletedWorkStore(db_path=db_path)
    packet = _make_test_packet(issue="GRO-IND-1", agent="agy")
    row = store.ingest(packet)

    # Enqueue in queue
    job_id = enqueue_with_defaults(
        q,
        completed_work_id=row.id,
        task_id="GRO-IND-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
    )

    # Complete verification first so it goes to REVIEW_READY
    q.lease_for_verification("verifier-1")
    receipt = VerificationReceipt(
        receipt_id="rec-ind-1",
        review_job_id=job_id,
        candidate_commit="b" * 40,
        candidate_tree="b" * 40,
    )
    q.complete_verification(job_id, receipt, worker_id="verifier-1")

    # Reviewer 'agy' (producer) should not be able to lease this job
    leased = q.lease_for_review("agy")
    assert leased is None

    # Reviewer 'ned' (independent) should be able to lease it
    leased = q.lease_for_review("ned")
    assert leased is not None
    assert leased.review_job_id == job_id

    q.close()


def test_reviewer_cannot_submit_verdict_on_own_job(tmp_path):
    """Enforce reviewer independence: submit_verdict rejects if reviewer matches producer."""
    db_path = tmp_path / "test_independence.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    q = ReviewQueue(db=db)

    # Ingest a completed work by agent 'agy' into the completed work store
    store = AgyCompletedWorkStore(db_path=db_path)
    packet = _make_test_packet(issue="GRO-IND-2", agent="agy")
    row = store.ingest(packet)

    # Enqueue in queue
    job_id = enqueue_with_defaults(
        q,
        completed_work_id=row.id,
        task_id="GRO-IND-2",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
    )

    # Complete verification
    q.lease_for_verification("verifier-2")
    receipt = VerificationReceipt(
        receipt_id="rec-ind-2",
        review_job_id=job_id,
        candidate_commit="b" * 40,
        candidate_tree="b" * 40,
    )
    q.complete_verification(job_id, receipt, worker_id="verifier-2")

    # Lease to independent 'ned' first, but try to submit verdict as 'agy' (the producer)
    leased = q.lease_for_review("ned")
    assert leased is not None

    decision_own = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="agy",
        candidate_commit="b" * 40,
        candidate_tree="b" * 40,
        receipt_id="rec-ind-2",
        verdict=ReviewVerdict.CLEAN.value,
        idempotency_key="key-ind-own",
    )
    with pytest.raises(ValueError, match="Reviewer identity reuse"):
        q.submit_verdict(job_id, decision_own, reviewer_id="agy")

    q.close()
