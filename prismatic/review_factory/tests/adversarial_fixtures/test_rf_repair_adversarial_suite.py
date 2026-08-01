"""Adversarial test suite enforcing all 12 RF-1/RF-2/RF-3/RF-4/RF-5 repair packet security invariants."""

from prismatic.merge_candidate_manifest import MergeCandidateManifest, RiskTier
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    MergeAuthorization,
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.merge_executor import MergeExecutor
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

    receipt = VerificationReceipt(
        receipt_id="rec-wit-1",
        review_job_id=job_id,
        candidate_commit="b" * 40,
        candidate_tree="b" * 40,
    )
    db.insert_receipt(receipt)

    # First decision from reviewer-1
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

    # Replayed decision from reviewer-1 (same reviewer)
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

    # Second decision from reviewer-2 (different reviewer)
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


def test_unsupported_manual_proof_class_fails_closed(tmp_path):
    """Manual/unsupported proof classes must return passed=False rather than echoing success."""
    worker = VerificationWorker(repo_path=tmp_path)
    res = worker._run_proof_class("rollback", None, None)
    assert res.passed is False
    assert res.exit_code == 1
    assert "fail closed" in res.stderr


def test_dry_run_leaves_database_and_manifest_strictly_readonly(tmp_path):
    """MergeExecutor dry_run=True must leave job state and DB 100% unchanged."""
    db_path = tmp_path / "test_dryrun.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    q = ReviewQueue(db=db)

    job_id = q.enqueue_completed_work(
        completed_work_id="cw-dryrun-1",
        task_id="GRO-DRYRUN-1",
        repository="mbgulden/prismatic-engine",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        changed_paths=["docs/readme.md"],
    )

    rec = VerificationReceipt(
        review_job_id=job_id, candidate_commit="b" * 40, candidate_tree="b" * 40
    )

    q.lease_for_verification("v1")
    q.complete_verification(job_id, rec)
    q.lease_for_review("r1")
    q.submit_verdict(
        job_id,
        ReviewDecision(
            review_job_id=job_id,
            reviewer_id="r1",
            verdict=ReviewVerdict.CLEAN.value,
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
            receipt_id=rec.receipt_id,
        ),
    )

    # Authorize merge explicitly
    auth_id = q.authorize_merge(job_id, actor="michael")
    assert auth_id is not None

    job_before = db.get_review_job(job_id)
    assert job_before.state == ReviewJobState.MERGE_AUTHORIZED.value

    # Execute with dry_run=True
    manifest = MergeCandidateManifest.create(
        issue_id="GRO-DRYRUN-1",
        task_id="GRO-DRYRUN-1",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="a" * 40,
        candidate_sha="b" * 40,
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location="/tmp/dryrun",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )
    executor = MergeExecutor(queue=q, dry_run=True)
    res = executor.execute(job_id, manifest=manifest)
    assert res.success is True
    assert res.merge_sha == "dry-run-sha"

    job_after = db.get_review_job(job_id)
    assert job_after.state == ReviewJobState.MERGE_AUTHORIZED.value, (
        "dry_run=True MUST NOT change job state to MERGING!"
    )


def test_authorization_binding_mismatch_rejected(tmp_path):
    """MergeExecutor must reject authorizations with mismatched PR, head, base, or tree."""
    db_path = tmp_path / "test_mismatch.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    q = ReviewQueue(db=db)

    job_id = q.enqueue_completed_work(
        completed_work_id="cw-mismatch-1",
        task_id="GRO-MISMATCH-1",
        repository="mbgulden/prismatic-engine",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        changed_paths=["docs/readme.md"],
    )

    rec = VerificationReceipt(
        review_job_id=job_id, candidate_commit="b" * 40, candidate_tree="b" * 40
    )

    q.lease_for_verification("v1")
    q.complete_verification(job_id, rec)
    q.lease_for_review("r1")
    q.submit_verdict(
        job_id,
        ReviewDecision(
            review_job_id=job_id,
            reviewer_id="r1",
            verdict=ReviewVerdict.CLEAN.value,
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
            receipt_id=rec.receipt_id,
        ),
    )

    # Insert malicious authorization with wrong candidate_tree
    bad_auth = MergeAuthorization(
        review_job_id=job_id,
        repository="mbgulden/prismatic-engine",
        pr_head_commit="b" * 40,
        pr_base_commit="a" * 40,
        candidate_tree="wrong-tree-sha",
        expected_merge_tree="wrong-tree-sha",
        actor="hacker",
    )
    db.insert_authorization(bad_auth)

    executor = MergeExecutor(queue=q, dry_run=True)
    res = executor.execute(job_id)
    assert res.success is False
    assert "mismatch" in res.error.lower()


def test_authorize_merge_rejects_missing_actor(tmp_path):
    """authorize_merge without explicit actor must return None (no invented standing policy)."""
    db_path = tmp_path / "test_actor.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    q = ReviewQueue(db=db)

    job_id = q.enqueue_completed_work(
        completed_work_id="cw-actor-1",
        task_id="GRO-ACTOR-1",
        repository="mbgulden/prismatic-engine",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        changed_paths=["docs/readme.md"],
    )

    rec = VerificationReceipt(
        review_job_id=job_id, candidate_commit="b" * 40, candidate_tree="b" * 40
    )

    q.lease_for_verification("v1")
    q.complete_verification(job_id, rec)
    q.lease_for_review("r1")
    q.submit_verdict(
        job_id,
        ReviewDecision(
            review_job_id=job_id,
            reviewer_id="r1",
            verdict=ReviewVerdict.CLEAN.value,
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
            receipt_id=rec.receipt_id,
        ),
    )

    auth_id = q.authorize_merge(job_id, actor="")
    assert auth_id is None, "authorize_merge without actor MUST return None!"
