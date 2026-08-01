"""RF-3 Integration Tests: Reviewer Capability.

Tests exercise real PE integration by:
1. Feeding real ``PRReviewResult`` objects to ``PreliminaryReviewAdapter``
2. Verifying verdict mapping (APPROVE→CLEAN, REQUEST_CHANGES→REPAIR_REQUIRED)
3. Constructing ``IndependentReview`` and calling ``manifest.record_review()``
4. Verifying the manifest advances from REVIEW_REQUIRED → CLEAN
"""

import json

import pytest

from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    PromotionState,
    RiskTier,
    VerificationEvidence,
)
from prismatic.review import (
    APPROVE,
    NEEDS_DISCUSSION,
    REQUEST_CHANGES,
    PRReviewResult,
)
from prismatic.review.pr_reviewer import InlineComment
from prismatic.review_factory.models import ReviewVerdict
from prismatic.review_factory.reviewer import (
    PreliminaryReviewAdapter,
    ReviewerCapability,
    StubReviewerCapability,
    VERDICT_MAP,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_pr_result(
    verdict: str = APPROVE,
    comments: list[InlineComment] | None = None,
) -> PRReviewResult:
    """Create a real PRReviewResult (from prismatic.review)."""
    return PRReviewResult(
        verdict=verdict,
        summary=f"Review result: {verdict}",
        inline_comments=comments or [],
        metadata={
            "reviewer": "test",
            "findings_count": len(comments or []),
            "critical_count": 0,
            "high_count": 0,
            "warning_count": 0,
        },
    )


def _make_review_required_manifest(
    producer: str = "agy",
) -> MergeCandidateManifest:
    """Create a manifest in REVIEW_REQUIRED state.

    First creates CANDIDATE, then calls request_review() with evidence.
    """
    manifest = MergeCandidateManifest.create(
        issue_id="GRO-TEST-REVIEW",
        task_id="GRO-TEST-REVIEW",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="a" * 40,
        candidate_sha="b" * 40,
        changed_paths=["docs/readme.md"],
        producer=producer,
        preserved_candidate_location="/tmp/test-review",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )

    # Advance to REVIEW_REQUIRED with focused evidence
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
    return manifest.request_review(evidence)


# ── Adapter tests ────────────────────────────────────────────────────


class TestPreliminaryReviewAdapter:
    """Test that the adapter maps PRReviewResult verdicts correctly."""

    def test_approve_maps_to_clean(self):
        pr_result = _make_pr_result(verdict=APPROVE)
        verdict = PreliminaryReviewAdapter.map_verdict(pr_result)
        assert verdict == ReviewVerdict.CLEAN

    def test_request_changes_maps_to_repair(self):
        pr_result = _make_pr_result(verdict=REQUEST_CHANGES)
        verdict = PreliminaryReviewAdapter.map_verdict(pr_result)
        assert verdict == ReviewVerdict.REPAIR_REQUIRED

    def test_needs_discussion_maps_to_rejected(self):
        pr_result = _make_pr_result(verdict=NEEDS_DISCUSSION)
        verdict = PreliminaryReviewAdapter.map_verdict(pr_result)
        assert verdict == ReviewVerdict.REJECTED

    def test_findings_mapped_from_inline_comments(self):
        comments = [
            InlineComment(
                path="prismatic/core/router.py",
                line=42,
                body="Function too long",
            ),
        ]
        pr_result = _make_pr_result(
            verdict=REQUEST_CHANGES, comments=comments
        )
        findings = PreliminaryReviewAdapter.map_findings(pr_result)
        assert len(findings) == 1
        assert findings[0].path == "prismatic/core/router.py"
        assert findings[0].line == 42
        assert "Function too long" in findings[0].invariant


# ── StubReviewerCapability tests ─────────────────────────────────────


class TestStubReviewer:
    """Test that StubReviewerCapability advances the manifest correctly."""

    def test_clean_verdict_advances_manifest(self):
        """CLEAN verdict: manifest advances REVIEW_REQUIRED → CLEAN."""
        manifest = _make_review_required_manifest(producer="agy")
        assert manifest.state == PromotionState.REVIEW_REQUIRED

        from prismatic.review_factory.models import ReviewJob, VerificationReceipt

        job = ReviewJob(
            review_job_id="test-job-1",
            completed_work_id="agy-cw-test",
            task_id="GRO-TEST",
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
        )
        receipt = VerificationReceipt(
            review_job_id="test-job-1",
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
        )

        stub = StubReviewerCapability(
            verdict=ReviewVerdict.CLEAN,
            reviewer_id="stub-reviewer",
        )
        decision, updated, repair = stub.review(job, manifest, receipt)

        # Manifest advanced to CLEAN
        assert updated.state == PromotionState.CLEAN
        assert updated.independent_review is not None
        assert updated.independent_review.reviewer == "stub-reviewer"
        assert updated.independent_review.verdict == "CLEAN"

        # Decision is correct
        assert decision.verdict == ReviewVerdict.CLEAN.value
        assert repair is None

    def test_repair_verdict_creates_repair_packet(self):
        """REPAIR_REQUIRED verdict: produces RepairPacket, no manifest advance."""
        manifest = _make_review_required_manifest(producer="agy")

        from prismatic.review_factory.models import (
            Finding,
            ReviewJob,
            VerificationReceipt,
        )

        job = ReviewJob(
            review_job_id="test-job-2",
            completed_work_id="agy-cw-test-2",
            task_id="GRO-TEST-2",
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
        )
        receipt = VerificationReceipt(
            review_job_id="test-job-2",
            candidate_commit="b" * 40,
            candidate_tree="b" * 40,
        )

        findings = [
            Finding(
                severity="warning",
                path="prismatic/core/router.py",
                line=42,
                invariant="Function too long",
                reproduction_command="",
            )
        ]
        stub = StubReviewerCapability(
            verdict=ReviewVerdict.REPAIR_REQUIRED,
            findings=findings,
            reviewer_id="stub-reviewer",
        )
        decision, updated, repair = stub.review(job, manifest, receipt)

        # Manifest NOT advanced (still REVIEW_REQUIRED)
        assert updated.state == PromotionState.REVIEW_REQUIRED

        # Decision and repair packet
        assert decision.verdict == ReviewVerdict.REPAIR_REQUIRED.value
        assert repair is not None
        assert repair.resolution_attempt_n == 1


# ── Idempotency tests ────────────────────────────────────────────────


class TestIdempotencyKey:
    """Verify idempotency key computation is stable."""

    def test_same_inputs_same_key(self):
        key1 = ReviewerCapability._compute_idempotency_key(
            "reviewer-1", "tree-abc", "clean"
        )
        key2 = ReviewerCapability._compute_idempotency_key(
            "reviewer-1", "tree-abc", "clean"
        )
        assert key1 == key2
        assert len(key1) == 64  # SHA-256

    def test_different_verdict_different_key(self):
        key1 = ReviewerCapability._compute_idempotency_key(
            "reviewer-1", "tree-abc", "clean"
        )
        key2 = ReviewerCapability._compute_idempotency_key(
            "reviewer-1", "tree-abc", "repair_required"
        )
        assert key1 != key2
