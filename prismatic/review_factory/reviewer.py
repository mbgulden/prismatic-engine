"""RF-3: Reviewer Capability — Deep PE Integration.

Wraps the existing ``pr_reviewer_impl.RealPRReviewer`` and
``pipeline.PipelineOrchestrator`` to produce ``ReviewDecision`` objects
and drive the ``MergeCandidateManifest`` from ``REVIEW_REQUIRED`` →
``CLEAN`` via ``manifest.record_review(IndependentReview(...))``.

This module does NOT replace or duplicate ``pr_reviewer.py``.
It instantiates and calls it, mapping its output into factory decisions.

Usage
-----
    from prismatic.review_factory.reviewer import ReviewerCapability

    reviewer = ReviewerCapability()
    decision, updated_manifest = reviewer.review(job, manifest, receipt)
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from prismatic.merge_candidate_manifest import (
    IndependentReview,
    MergeCandidateManifest,
)
from prismatic.review import (
    APPROVE,
    NEEDS_DISCUSSION,
    REQUEST_CHANGES,
    ACTION_ADVANCE,
    ACTION_HOLD,
    ACTION_REWORK,
    ACTION_GIVE_UP,
    PRReviewResult,
    PipelineOrchestrator,
    RealPRReviewer,
)
from prismatic.review_factory.models import (
    Finding,
    RepairPacket,
    ReviewDecision,
    ReviewJob,
    ReviewVerdict,
    VerificationReceipt,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Verdict mapping: PRReviewResult.verdict → ReviewVerdict
# ─────────────────────────────────────────────────────────────────────

VERDICT_MAP: dict[str, ReviewVerdict] = {
    APPROVE: ReviewVerdict.CLEAN,
    REQUEST_CHANGES: ReviewVerdict.REPAIR_REQUIRED,
    NEEDS_DISCUSSION: ReviewVerdict.REJECTED,
}

ACTION_TO_VERDICT: dict[str, ReviewVerdict] = {
    ACTION_ADVANCE: ReviewVerdict.CLEAN,
    ACTION_REWORK: ReviewVerdict.REPAIR_REQUIRED,
    ACTION_HOLD: ReviewVerdict.REJECTED,
    ACTION_GIVE_UP: ReviewVerdict.REJECTED,
}


# ─────────────────────────────────────────────────────────────────────
# PreliminaryReviewAdapter — wraps RealPRReviewer
# ─────────────────────────────────────────────────────────────────────


class PreliminaryReviewAdapter:
    """Adapts ``RealPRReviewer.review_pr()`` output to factory types.

    DO NOT REPLACE ``pr_reviewer.py`` — this adapter calls it.
    """

    def __init__(
        self,
        timeout_seconds: int = 30,
        registry: Any = None,
    ):
        self._reviewer = RealPRReviewer(
            timeout_seconds=timeout_seconds,
            registry=registry,
        )

    def review(self, pr_url: str) -> PRReviewResult:
        """Run the heuristic review and return the raw PRReviewResult.

        Callers should inspect ``.verdict`` and ``.inline_comments``
        to build factory ``ReviewDecision`` and ``Finding`` objects.
        """
        return self._reviewer.review_pr(pr_url)

    @staticmethod
    def map_verdict(pr_result: PRReviewResult) -> ReviewVerdict:
        """Map PRReviewResult.verdict to factory ReviewVerdict."""
        return VERDICT_MAP.get(pr_result.verdict, ReviewVerdict.REJECTED)

    @staticmethod
    def map_findings(pr_result: PRReviewResult) -> list[Finding]:
        """Map PRReviewResult.inline_comments to factory Finding objects."""
        findings: list[Finding] = []
        for comment in pr_result.inline_comments:
            # Derive severity from metadata if available
            severity = "warning"
            metadata = pr_result.metadata or {}
            if metadata.get("critical_count", 0) > 0:
                severity = "critical"
            elif metadata.get("high_count", 0) > 0:
                severity = "high"

            findings.append(
                Finding(
                    severity=severity,
                    path=comment.path,
                    line=comment.line,
                    invariant=comment.body,
                    reproduction_command="",
                )
            )
        return findings


# ─────────────────────────────────────────────────────────────────────
# ReviewerCapability — the full reviewer with pipeline orchestration
# ─────────────────────────────────────────────────────────────────────


class ReviewerCapability:
    """RF-3: Full review capability with pipeline orchestration.

    1. Runs ``PreliminaryReviewAdapter`` (wraps ``RealPRReviewer``)
    2. Feeds the result to ``PipelineOrchestrator.process()``
    3. Maps the ``PipelineDecision`` to factory ``ReviewDecision``
    4. If ``action == "advance"``, calls ``manifest.record_review()``
       to advance the manifest to ``CLEAN``
    5. If ``action == "rework"``, builds a ``RepairPacket``
    """

    def __init__(
        self,
        adapter: Optional[PreliminaryReviewAdapter] = None,
        orchestrator: Optional[PipelineOrchestrator] = None,
        reviewer_id: str = "antigravity-rf-v1",
    ):
        self._adapter = adapter or PreliminaryReviewAdapter()
        self._orchestrator = orchestrator or PipelineOrchestrator()
        self._reviewer_id = reviewer_id

    def review(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
        receipt: VerificationReceipt,
        pr_url: Optional[str] = None,
    ) -> tuple[ReviewDecision, MergeCandidateManifest, Optional[RepairPacket]]:
        """Run the full review pipeline.

        Args:
            job: The leased review job (in ``reviewing`` state).
            manifest: The manifest (in ``REVIEW_REQUIRED`` state).
            receipt: The verification receipt from RF-2.
            pr_url: GitHub PR URL. If None, derived from job metadata.

        Returns:
            (decision, updated_manifest, repair_packet_or_None)
        """
        # Validate 5-point composite lineage
        if receipt.review_job_id and receipt.review_job_id != job.review_job_id:
            raise ValueError(
                f"Cross-job receipt mismatch: receipt review_job_id ({receipt.review_job_id}) != job review_job_id ({job.review_job_id})"
            )
        if receipt.candidate_commit and job.candidate_commit and receipt.candidate_commit != job.candidate_commit:
            raise ValueError(
                f"Cross-candidate receipt commit mismatch: receipt commit ({receipt.candidate_commit}) != job commit ({job.candidate_commit})"
            )
        if receipt.candidate_tree and job.candidate_tree and receipt.candidate_tree != job.candidate_tree:
            raise ValueError(
                f"Cross-candidate receipt tree mismatch: receipt tree ({receipt.candidate_tree}) != job tree ({job.candidate_tree})"
            )
        if manifest.candidate_sha and job.candidate_commit and manifest.candidate_sha != job.candidate_commit:
            raise ValueError(
                f"Cross-candidate manifest SHA mismatch: manifest SHA ({manifest.candidate_sha}) != job commit ({job.candidate_commit})"
            )

        if pr_url is None:
            pr_url = self._derive_pr_url(job)

        # Step 1: Run the heuristic review via the adapter
        pr_result = self._adapter.review(pr_url)

        # Step 2: Feed to PipelineOrchestrator for action decision
        pipeline_decision = self._orchestrator.process(
            identifier=job.task_id,
            pr_url=pr_url,
            result=pr_result,
        )

        # Step 3: Map to factory verdict
        factory_verdict = ACTION_TO_VERDICT.get(
            pipeline_decision.action, ReviewVerdict.REJECTED
        )
        findings = PreliminaryReviewAdapter.map_findings(pr_result)

        # Step 4: Compute idempotency key
        idempotency_key = self._compute_idempotency_key(
            self._reviewer_id,
            job.candidate_tree or job.candidate_commit,
            factory_verdict.value,
        )

        # Step 5: Build ReviewDecision
        decision = ReviewDecision(
            review_job_id=job.review_job_id,
            reviewer_id=self._reviewer_id,
            candidate_commit=job.candidate_commit,
            candidate_tree=job.candidate_tree or job.candidate_commit,
            receipt_id=receipt.receipt_id,
            verdict=factory_verdict.value,
            idempotency_key=idempotency_key,
            findings=json.dumps([f.to_dict() for f in findings] if findings else []),
        )

        # Step 6: Advance the manifest if clean
        updated_manifest = manifest
        repair_packet = None

        if factory_verdict == ReviewVerdict.CLEAN:
            # Record review on the manifest: REVIEW_REQUIRED → CLEAN
            independent_review = IndependentReview(
                reviewer=self._reviewer_id,
                review_id=decision.decision_id,
                verdict="CLEAN",
                reviewed_sha=manifest.candidate_sha,
                reviewed_manifest_digest=manifest._digest_without_review(),
                scope_clean=True,
                conflict_free=True,
            )
            updated_manifest = manifest.record_review(independent_review)
            logger.info("Manifest advanced to CLEAN for job %s", job.review_job_id)

        elif factory_verdict == ReviewVerdict.REPAIR_REQUIRED:
            # Build repair packet using PipelineOrchestrator's rework payload
            rework = pipeline_decision.rework_payload
            repair_packet = RepairPacket(
                candidate_tree=job.candidate_tree or job.candidate_commit,
                producer_id=job.completed_work_id,
                findings_json=json.dumps([f.to_dict() for f in findings]),
                resolution_attempt_n=(rework.rework_attempt if rework else 1),
            )
            logger.info(
                "Repair required for job %s (attempt %s)",
                job.review_job_id,
                repair_packet.resolution_attempt_n,
            )

        return decision, updated_manifest, repair_packet

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _derive_pr_url(job: ReviewJob) -> str:
        """Derive valid GitHub PR URL from job metadata."""
        repo = job.repository or "mbgulden/prismatic-engine"
        import re

        m = re.search(r"(\d+)", str(job.task_id))
        pr_num = m.group(1) if m else job.task_id
        return f"https://github.com/{repo}/pull/{pr_num}"

    @staticmethod
    def _compute_idempotency_key(
        reviewer_id: str,
        candidate_tree: str,
        verdict: str,
    ) -> str:
        """sha256(reviewer_id + candidate_tree + verdict)"""
        raw = f"{reviewer_id}:{candidate_tree}:{verdict}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Stub reviewer for testing — deterministic, no network calls
# ─────────────────────────────────────────────────────────────────────


class StubReviewerCapability:
    """Test double that returns a configurable verdict without network."""

    def __init__(
        self,
        verdict: ReviewVerdict = ReviewVerdict.CLEAN,
        findings: Optional[list[Finding]] = None,
        reviewer_id: str = "stub-reviewer",
    ):
        self._verdict = verdict
        self._findings = findings or []
        self._reviewer_id = reviewer_id

    def review(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
        receipt: VerificationReceipt,
        pr_url: Optional[str] = None,
    ) -> tuple[ReviewDecision, MergeCandidateManifest, Optional[RepairPacket]]:
        """Return a deterministic verdict."""
        idempotency_key = ReviewerCapability._compute_idempotency_key(
            self._reviewer_id,
            job.candidate_tree or job.candidate_commit,
            self._verdict.value,
        )

        decision = ReviewDecision(
            review_job_id=job.review_job_id,
            reviewer_id=self._reviewer_id,
            candidate_commit=job.candidate_commit,
            candidate_tree=job.candidate_tree or job.candidate_commit,
            receipt_id=receipt.receipt_id,
            verdict=self._verdict.value,
            idempotency_key=idempotency_key,
            findings=json.dumps([f.to_dict() for f in self._findings]),
        )

        updated_manifest = manifest
        repair_packet = None

        if self._verdict == ReviewVerdict.CLEAN:
            independent_review = IndependentReview(
                reviewer=self._reviewer_id,
                review_id=decision.decision_id,
                verdict="CLEAN",
                reviewed_sha=manifest.candidate_sha,
                reviewed_manifest_digest=manifest._digest_without_review(),
                scope_clean=True,
                conflict_free=True,
            )
            updated_manifest = manifest.record_review(independent_review)

        elif self._verdict == ReviewVerdict.REPAIR_REQUIRED:
            repair_packet = RepairPacket(
                candidate_tree=job.candidate_tree or job.candidate_commit,
                producer_id=job.completed_work_id,
                findings_json=json.dumps([f.to_dict() for f in self._findings]),
                resolution_attempt_n=1,
            )

        return decision, updated_manifest, repair_packet
