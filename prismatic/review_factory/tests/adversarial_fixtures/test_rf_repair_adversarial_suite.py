"""Adversarial test suite enforcing RF-1/RF-2 repair packet security invariants."""

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import ReviewDecision, ReviewVerdict
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.verifier import VerificationWorker


def test_replayed_clean_decision_does_not_increment_witnesses(tmp_path):
    """Replayed decisions by the same reviewer must NOT increment witness count."""
    db_path = tmp_path / "test_witness.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    q = ReviewQueue(db=db)

    job_id = q.enqueue_completed_work(
        completed_work_id="agy-cw-wit-1",
        task_id="GRO-WIT-1",
        repository="mbgulden/prismatic-engine",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        changed_paths=["prismatic/core/router.py"],
    )

    # Complete verification first
    from prismatic.review_factory.models import VerificationReceipt

    receipt = VerificationReceipt(
        receipt_id="rec-wit-1",
        review_job_id=job_id,
        candidate_commit="b" * 40,
        candidate_tree="b" * 40,
    )
    db.insert_receipt(receipt)

    # 1. First decision from reviewer-1
    d1 = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="reviewer-1",
        receipt_id="rec-wit-1",
        verdict=ReviewVerdict.CLEAN.value,
        idempotency_key="key-1",
    )
    q.submit_verdict(job_id, d1)
    j1 = db.get_review_job(job_id)
    assert j1.completed_witnesses == 1

    # 2. Replayed decision from reviewer-1 (same reviewer)
    d1_replay = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="reviewer-1",
        receipt_id="rec-wit-1",
        verdict=ReviewVerdict.CLEAN.value,
        idempotency_key="key-1-replay",
    )
    q.submit_verdict(job_id, d1_replay)
    j2 = db.get_review_job(job_id)
    assert j2.completed_witnesses == 1, (
        "Replayed decision MUST NOT increment completed_witnesses"
    )

    # 3. Second decision from reviewer-2 (different reviewer)
    d2 = ReviewDecision(
        review_job_id=job_id,
        reviewer_id="reviewer-2",
        receipt_id="rec-wit-1",
        verdict=ReviewVerdict.CLEAN.value,
        idempotency_key="key-2",
    )
    q.submit_verdict(job_id, d2)
    j3 = db.get_review_job(job_id)
    assert j3.completed_witnesses == 2, (
        "Second distinct reviewer MUST increment completed_witnesses"
    )


def test_missing_file_in_verifier_fails_closed(tmp_path):
    """Verifier integrity check on missing file must fail closed (passed=False)."""
    worker = VerificationWorker(repo_path=tmp_path)
    res = worker._check_integration_imports("nonexistent_file.py", ["import foo"])
    assert res.passed is False
    assert res.exit_code == 1
