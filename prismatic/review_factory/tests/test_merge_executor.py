"""RF-4 Integration Tests: Merge Executor.

Tests exercise real PE integration by driving a
``MergeCandidateManifest`` through the full promotion lifecycle:
    CANDIDATE → REVIEW_REQUIRED → CLEAN → CI_GREEN → MERGE_ELIGIBLE

Uses dry_run=True since we can't run ``integrate_pipeline_run()``
in a test without a real git repository.
"""

import pytest

from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    PromotionState,
    RiskTier,
    VerificationEvidence,
)
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.merge_executor import MergeExecutor
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.testing import enqueue_with_defaults

# ── Helpers ──────────────────────────────────────────────────────────


def _create_merge_ready_manifest() -> MergeCandidateManifest:
    """Create a manifest promoted through CANDIDATE → CLEAN."""
    manifest = MergeCandidateManifest.create(
        issue_id="GRO-TEST-MERGE",
        task_id="GRO-TEST-MERGE",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="a" * 40,
        candidate_sha="b" * 40,
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location="/tmp/test-merge",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )

    # CANDIDATE → REVIEW_REQUIRED
    evidence = [
        VerificationEvidence(
            proof_class="focused",
            command="pytest tests/ -x -q",
            summary="5 tests passed",
            result="PASS",
            log_path="/tmp/focused.log",
            log_sha256="f" * 64,
        )
    ]
    manifest = manifest.request_review(evidence)

    # REVIEW_REQUIRED → CLEAN
    review = IndependentReview(
        reviewer="antigravity-rf-v1",
        review_id="review-test-1",
        verdict="CLEAN",
        reviewed_sha="b" * 40,
        reviewed_manifest_digest=manifest._digest_without_review(),
        scope_clean=True,
        conflict_free=True,
    )
    manifest = manifest.record_review(review)

    return manifest


def _create_merge_ready_job(queue: ReviewQueue, tier: int = 0) -> str:
    """Create a job and push it through to merge_ready."""
    paths = {
        0: ["docs/readme.md"],
        1: ["prismatic/core/router.py"],
        2: ["prismatic/auth/oauth.py"],
    }

    job_id = enqueue_with_defaults(
        queue,
        completed_work_id=f"agy-cw-merge-{tier}-{id(queue)}",
        task_id="GRO-TEST-MERGE",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        changed_paths=paths.get(tier, ["docs/readme.md"]),
    )

    # Verify
    _ = queue.lease_for_verification("verifier-1")
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="b" * 40,
        candidate_tree="b" * 40,
    )
    queue.complete_verification(job_id, receipt, worker_id="verifier-1")

    # Review (multi-witness for high tiers)
    job_obj = queue.db.get_review_job(job_id)
    needed = job_obj.required_witnesses if job_obj.required_witnesses > 0 else 1
    for witness_n in range(needed):
        leased = queue.lease_for_review(f"reviewer-{witness_n}")
        if leased is not None:
            decision = ReviewDecision(
                review_job_id=job_id,
                reviewer_id=f"reviewer-{witness_n}",
                candidate_commit="b" * 40,
                candidate_tree="b" * 40,
                receipt_id=receipt.receipt_id,
                verdict=ReviewVerdict.CLEAN.value,
            )
            queue.submit_verdict(job_id, decision, reviewer_id=f"reviewer-{witness_n}")

    return job_id


@pytest.fixture
def queue(tmp_path):
    db = ReviewFactoryDB(db_path=tmp_path / "test_merge.db")
    db.ensure_tables()
    q = ReviewQueue(db=db)
    yield q
    q.close()


@pytest.fixture
def executor(queue):
    return MergeExecutor(queue=queue, dry_run=True)


# ── Manifest promotion tests ────────────────────────────────────────


class TestManifestPromotion:
    """Verify we can drive a manifest through the full lifecycle."""

    def test_full_manifest_promotion(self):
        """CANDIDATE → REVIEW_REQUIRED → CLEAN → CI_GREEN → MERGE_ELIGIBLE."""
        manifest = _create_merge_ready_manifest()
        assert manifest.state == PromotionState.CLEAN

        # CLEAN → CI_GREEN
        ci_checks = [
            CICheck(
                name="rf-v1-verification",
                run_id=1000,
                conclusion="SUCCESS",
                head_sha="b" * 40,
                details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
            )
        ]
        manifest = manifest.record_ci(ci_checks)
        assert manifest.state == PromotionState.CI_GREEN

        # CI_GREEN → MERGE_ELIGIBLE
        manifest = manifest.mark_merge_eligible()
        assert manifest.state == PromotionState.MERGE_ELIGIBLE

        # Get factory bindings
        bindings = manifest.factory_bindings()
        assert "base_sha" in bindings
        assert "candidate_sha" in bindings
        assert "manifest_digest" in bindings
        assert "evidence_digest" in bindings

    def test_merge_eligible_requires_ci_green(self):
        """Cannot mark_merge_eligible from CLEAN (must be CI_GREEN)."""
        manifest = _create_merge_ready_manifest()
        assert manifest.state == PromotionState.CLEAN

        from prismatic.merge_candidate_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError):
            manifest.mark_merge_eligible()


# ── Dry-run merge tests ─────────────────────────────────────────────


class TestMergeExecution:
    """Test merge execution flow (dry-run)."""

    def test_dry_run_merge_succeeds(self, queue, executor):
        """Dry-run merge for a Tier 0 job produces a MergeResult.

        RF-R2: caller-supplied manifest is no longer honored by the executor
        when a durable result packet exists.  The executor loads the
        durable manifest and compares digests; mismatches are rejected.
        This test now exercises the durable path: it does NOT pass
        ``manifest=`` and lets the executor load from ``result_packet_path``.
        """
        job_id = _create_merge_ready_job(queue, tier=0)

        # Authorize explicitly
        auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-0")
        assert auth_id is not None

        result = executor.execute(job_id)
        assert result.success
        assert result.merge_sha == "dry-run-sha"

    def test_no_auth_fails(self, queue, executor):
        """Merge without authorization fails.

        RF-R2: durable manifest binding — see test_dry_run_merge_succeeds.
        """
        job_id = _create_merge_ready_job(queue, tier=0)
        # Don't authorize

        result = executor.execute(job_id)
        assert not result.success
        assert "authorization" in result.error.lower()

    def test_non_dry_run_rejects_synthetic_ci_before_claim(self, queue, tmp_path):
        """Synthetic GitHub-shaped checks cannot authorize a non-dry merge."""
        from unittest.mock import patch

        from prismatic.core.merge_factory import MergeFactoryStore

        mf_store = MergeFactoryStore(db_path=tmp_path / "test_mf.db")
        executor = MergeExecutor(queue=queue, dry_run=False, mf_store=mf_store)

        job_id = _create_merge_ready_job(queue, tier=0)
        auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-0")
        assert auth_id is not None

        manifest = _create_merge_ready_manifest()
        manifest = manifest.record_ci(
            [
                CICheck(
                    name="rf-v1-verification",
                    run_id=1000,
                    conclusion="SUCCESS",
                    head_sha="b" * 40,
                    details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
                )
            ]
        ).mark_merge_eligible()

        with patch(
            "prismatic.review_factory.merge_executor.integrate_pipeline_run"
        ) as integration:
            # RF-R2: don't pass manifest=; executor loads from durable path.
            result = executor.execute(job_id)

        assert result.success is False
        # RF-R2: the durable manifest has no CI checks recorded (the test
        # only adds CI to the in-memory manifest it used to pass).  Both
        # "lacks provider-neutral receipt provenance" and "Required CI
        # check missing or not green" are valid R2 rejections.
        assert any(
            needle in (result.error or "")
            for needle in (
                "lacks provider-neutral receipt provenance",
                "Required CI check",
            )
        )
        integration.assert_not_called()
        row = queue.db.get_review_job(job_id)
        assert row is not None
        assert row.state == ReviewJobState.MERGE_AUTHORIZED.value
        decision_hist = mf_store.get_decision_history("GRO-TEST-MERGE")
        assert decision_hist == []
        consumed_at = queue.db.conn.execute(
            "SELECT consumed_at FROM merge_authorizations WHERE authorization_id = ?",
            (auth_id,),
        ).fetchone()[0]
        assert consumed_at == ""
