"""RF-1: Review job queue, intake, and state machine.

Wires accepted completed-work rows from ``agy_completed_work`` into
the ``review_jobs`` queue exactly once.  Provides lease management
for verifiers and reviewers.

This module does NOT execute workers or merges — it only manages
the queue state and intake pipeline.

Usage
-----
    from prismatic.review_factory.queue import ReviewQueue

    queue = ReviewQueue()  # uses default DB path

    # Enqueue a completed-work result
    job_id = queue.enqueue_completed_work(
        completed_work_id="agy-cw-abc123",
        task_id="GRO-4188",
        repository="mbgulden/prismatic-engine",
        base_commit="21be7812...",
        candidate_commit="c09761ed...",
        changed_paths=["prismatic/review/hooks.py"],
        result_packet_path="/path/to/packet.json",
    )

    # Lease a job for verification
    job = queue.lease_for_verification(worker_id="verifier-1")

    # Complete verification
    queue.complete_verification(job.review_job_id, receipt_id="...")

    # Lease for review
    job = queue.lease_for_review(reviewer_id="agy-v1.0")

    # Submit review verdict
    queue.submit_verdict(job.review_job_id, verdict="clean")
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    MergeAuthorization,
    MergeScope,
    RepairPacket,
    ReviewDecision,
    ReviewJob,
    ReviewJobState,
    ReviewVerdict,
    RiskTier,
    VerificationReceipt,
)
from prismatic.review_factory.policy import PolicyEngine


# ─────────────────────────────────────────────────────────────────────
# Lease duration config (from OKF §3)
# ─────────────────────────────────────────────────────────────────────

_LEASE_DURATIONS = {
    RiskTier.DETERMINISTIC_ONLY: timedelta(minutes=2),
    RiskTier.STANDARD: timedelta(minutes=15),
    RiskTier.SENSITIVE: timedelta(minutes=30),
    RiskTier.PRODUCTION: timedelta(minutes=0),  # Tier 3 doesn't enter queue
}

_REVIEWER_CAP = 3  # max concurrent read-only reviewers


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _lease_expiry(tier: int) -> str:
    """Compute lease expiry timestamp for a given risk tier."""
    duration = _LEASE_DURATIONS.get(tier, timedelta(minutes=15))
    if duration.total_seconds() == 0:
        return ""  # Tier 3 — no lease
    return (_utcnow() + duration).isoformat()


# ─────────────────────────────────────────────────────────────────────
# Queue
# ─────────────────────────────────────────────────────────────────────


class ReviewQueue:
    """RF-1: Review job queue and state machine.

    Manages the lifecycle of review jobs from intake through
    merge-ready.  All state transitions are idempotent and
    go through the DB layer.
    """

    def __init__(
        self,
        db: Optional[ReviewFactoryDB] = None,
        policy: Optional[PolicyEngine] = None,
    ):
        self.db = db or ReviewFactoryDB()
        self.db.ensure_tables()
        self.policy = policy or PolicyEngine.builtin_default()

    def close(self) -> None:
        self.db.close()

    # ── Intake ───────────────────────────────────────────────────────

    def enqueue_completed_work(
        self,
        completed_work_id: str,
        task_id: str,
        repository: str,
        base_commit: str,
        base_tree: str = "",
        candidate_commit: str = "",
        candidate_tree: str = "",
        changed_paths: Optional[list[str]] = None,
        result_packet_path: str = "",
        result_packet_sha256: str = "",
    ) -> str:
        """Enqueue a completed-work result into the review factory.

        Classifies the candidate by risk tier, sets witness requirements,
        and returns the ``review_job_id``.

        Idempotent: if a job already exists for this
        ``completed_work_id``, returns the existing job ID.
        """
        # Idempotency check via SQL index query
        existing = self.db.get_job_by_completed_work_id(completed_work_id)
        if existing:
            return existing.review_job_id

        # Classify risk tier
        paths = changed_paths or []
        classification = self.policy.classify(paths)

        # Tier 3 doesn't enter the reviewer queue (human-only)
        if classification.risk_tier >= RiskTier.PRODUCTION:
            # Still create the job for tracking, but it won't be leased
            pass

        job = ReviewJob(
            completed_work_id=completed_work_id,
            task_id=task_id,
            repository=repository,
            base_commit=base_commit,
            base_tree=base_tree or base_commit,
            candidate_commit=candidate_commit,
            candidate_tree=candidate_tree or candidate_commit,
            result_packet_path=result_packet_path,
            result_packet_sha256=result_packet_sha256,
            changed_paths_json=json.dumps(paths),
            risk_tier=classification.risk_tier,
            policy_version=classification.policy_version,
            required_witnesses=classification.required_witnesses,
            state=ReviewJobState.QUEUED.value,
        )

        return self.db.insert_review_job(job)

    # ── Verification lease (RF-2 uses this) ──────────────────────────

    def lease_for_verification(self, worker_id: str) -> Optional[ReviewJob]:
        """Lease the oldest queued job for verification.

        Returns the job if one was leased, None if the queue is empty.
        Sets state to ``verifying`` with an appropriate lease expiry.
        """
        jobs = self.db.list_review_jobs(state=ReviewJobState.QUEUED, limit=1)
        if not jobs:
            return None

        job = jobs[0]
        expiry = _lease_expiry(job.risk_tier)
        success = self.db.update_review_job_state(
            job.review_job_id,
            ReviewJobState.VERIFYING,
            lease_owner=worker_id,
            lease_expires_at=expiry,
        )
        if not success:
            return None  # race condition — someone else leased it

        job.state = ReviewJobState.VERIFYING.value
        job.lease_owner = worker_id
        job.lease_expires_at = expiry
        return job

    def complete_verification(
        self,
        review_job_id: str,
        receipt: VerificationReceipt,
    ) -> bool:
        """Mark verification as complete and store the receipt.

        Transitions the job to ``review_ready``.
        """
        self.db.insert_receipt(receipt)
        return self.db.update_review_job_state(
            review_job_id,
            ReviewJobState.REVIEW_READY,
            lease_owner="",
            lease_expires_at="",
        )

    # ── Review lease (RF-3 uses this) ────────────────────────────────

    def lease_for_review(self, reviewer_id: str) -> Optional[ReviewJob]:
        """Lease the oldest review-ready job for review.

        Enforces the concurrent reviewer cap (default: 3).
        Returns None if no jobs are available or the pool is full.
        """
        # Check reviewer pool capacity
        reviewing = self.db.list_review_jobs(
            state=ReviewJobState.REVIEWING, limit=_REVIEWER_CAP + 1
        )
        if len(reviewing) >= _REVIEWER_CAP:
            return None  # pool exhausted

        jobs = self.db.list_review_jobs(state=ReviewJobState.REVIEW_READY, limit=1)
        if not jobs:
            return None

        job = jobs[0]

        # Tier 3 is human-only — don't auto-lease
        if job.risk_tier >= RiskTier.PRODUCTION:
            return None

        expiry = _lease_expiry(job.risk_tier)
        success = self.db.update_review_job_state(
            job.review_job_id,
            ReviewJobState.REVIEWING,
            lease_owner=reviewer_id,
            lease_expires_at=expiry,
        )
        if not success:
            return None

        job.state = ReviewJobState.REVIEWING.value
        job.lease_owner = reviewer_id
        job.lease_expires_at = expiry
        return job

    def submit_verdict(
        self,
        review_job_id: str,
        decision: ReviewDecision,
    ) -> str:
        """Submit a review verdict and transition the job state.

        - ``clean`` → check witnesses, then ``merge_ready``
        - ``repair_required`` → ``repair_required`` + create repair packet
        - ``rejected`` → ``rejected``

        Returns the new state value.
        """
        # Store the decision (idempotent via idempotency_key)
        self.db.insert_decision(decision)

        job = self.db.get_review_job(review_job_id)
        if job is None:
            raise ValueError(f"Review job {review_job_id} not found")

        verdict = ReviewVerdict(decision.verdict)

        if verdict == ReviewVerdict.CLEAN:
            # Check witness requirements
            new_witnesses = self.db.increment_witnesses(review_job_id)
            if new_witnesses >= job.required_witnesses:
                # All witnesses complete → merge_ready
                self.db.update_review_job_state(
                    review_job_id,
                    ReviewJobState.MERGE_READY,
                    lease_owner="",
                    lease_expires_at="",
                )
                return ReviewJobState.MERGE_READY.value
            else:
                # Need more witnesses → back to review_ready
                self.db.update_review_job_state(
                    review_job_id,
                    ReviewJobState.REVIEW_READY,
                    lease_owner="",
                    lease_expires_at="",
                )
                return ReviewJobState.REVIEW_READY.value

        elif verdict == ReviewVerdict.REPAIR_REQUIRED:
            self.db.update_review_job_state(
                review_job_id,
                ReviewJobState.REPAIR_REQUIRED,
                lease_owner="",
                lease_expires_at="",
            )
            # Create a repair packet for the producer
            packet = RepairPacket(
                candidate_tree=job.candidate_tree,
                findings_json=decision.findings,
                producer_id=decision.reviewer_id,
            )
            self.db.insert_repair_packet(packet)
            return ReviewJobState.REPAIR_REQUIRED.value

        elif verdict == ReviewVerdict.REJECTED:
            self.db.update_review_job_state(
                review_job_id,
                ReviewJobState.REJECTED,
                lease_owner="",
                lease_expires_at="",
            )
            return ReviewJobState.REJECTED.value

        else:
            raise ValueError(f"Unknown verdict: {verdict}")

    # ── Merge authorization (RF-4 uses this) ─────────────────────────

    def authorize_merge(
        self,
        review_job_id: str,
        pr_number: int = 0,
        pr_head_commit: str = "",
        pr_base_commit: str = "",
        expected_merge_tree: str = "",
        actor: str = "",
        expires_minutes: int = 60,
    ) -> Optional[str]:
        """Create a merge authorization for a merge-ready job.

        For Tier 0/1: actor = "standing-policy: tier-N" (auto).
        For Tier 2/3: actor = human identifier (manual).

        Returns authorization_id, or None if the job isn't merge-ready.
        """
        job = self.db.get_review_job(review_job_id)
        if job is None or job.state != ReviewJobState.MERGE_READY.value:
            return None

        tier = job.risk_tier
        if tier <= RiskTier.STANDARD:
            scope = MergeScope.TIER_0_AUTO if tier == 0 else MergeScope.TIER_1_AUTO
            actor = actor or f"standing-policy: tier-{tier}"
        else:
            scope = (
                MergeScope.TIER_2_EXCEPTION
                if tier == 2
                else MergeScope.TIER_3_EXCEPTION
            )
            if not actor:
                return None  # Tier 2/3 requires explicit actor

        auth = MergeAuthorization(
            review_job_id=review_job_id,
            repository=job.repository,
            pr_number=pr_number,
            pr_head_commit=pr_head_commit or job.candidate_commit,
            pr_base_commit=pr_base_commit or job.base_commit,
            candidate_tree=job.candidate_tree,
            expected_merge_tree=expected_merge_tree,
            actor=actor,
            scope=scope.value,
            expires_at=(_utcnow() + timedelta(minutes=expires_minutes)).isoformat(),
            idempotency_key=hashlib.sha256(
                f"{review_job_id}-{job.candidate_tree}-{_utcnow_iso()}".encode()
            ).hexdigest(),
        )

        auth_id = self.db.insert_authorization(auth)

        # Transition to merge_authorized
        self.db.update_review_job_state(
            review_job_id,
            ReviewJobState.MERGE_AUTHORIZED,
            lease_owner="",
            lease_expires_at="",
        )
        return auth_id

    # ── Auto-authorization for Tier 0/1 ──────────────────────────────

    def auto_authorize_if_eligible(self, review_job_id: str) -> Optional[str]:
        """Automatically authorize merge for Tier 0/1 jobs.

        Called after a job reaches merge_ready.  For Tier 0/1, this
        creates a standing-policy authorization immediately.  For
        Tier 2+, returns None (requires manual authorization).
        """
        job = self.db.get_review_job(review_job_id)
        if job is None or job.state != ReviewJobState.MERGE_READY.value:
            return None

        if job.risk_tier > RiskTier.STANDARD:
            return None  # Tier 2+ needs explicit authorization

        return self.authorize_merge(review_job_id)

    # ── Repair cycle ─────────────────────────────────────────────────

    def consume_repair(
        self,
        review_job_id: str,
        new_candidate_commit: str,
        new_candidate_tree: str = "",
        new_changed_paths: Optional[list[str]] = None,
    ) -> bool:
        """Re-enqueue a repaired candidate after a repair cycle.

        Consumes outstanding repair packets and transitions the job
        back to ``queued`` for re-verification.
        """
        job = self.db.get_review_job(review_job_id)
        if job is None or job.state != ReviewJobState.REPAIR_REQUIRED.value:
            return False

        # Consume repair packets
        packets = self.db.get_unconsumed_repairs(job.candidate_tree)
        for packet in packets:
            packet.consume()
            packet.increment_attempt()
            # Update in DB (we'd need an update method — for now, mark consumed)

        # Update the job with new candidate info
        # Note: we need to update candidate fields + re-classify
        # For now, transition back to queued
        return self.db.update_review_job_state(
            review_job_id,
            ReviewJobState.QUEUED,
            lease_owner="",
            lease_expires_at="",
        )

    # ── Janitor ──────────────────────────────────────────────────────

    def run_janitor(self) -> dict[str, int]:
        """Run the lease janitor to reset stale leases.

        Returns a dict of counts: {'verifying_reset': N, 'reviewing_reset': M}
        """
        total = self.db.reset_stale_leases()
        return {"stale_leases_reset": total}

    # ── Query helpers ────────────────────────────────────────────────

    def queue_depth(self) -> dict[str, int]:
        """Return counts of jobs by state."""
        return self.db.queue_stats()

    def pending_reviews(self) -> list[ReviewJob]:
        """List all jobs waiting for review."""
        return self.db.list_review_jobs(state=ReviewJobState.REVIEW_READY)

    def pending_merges(self) -> list[ReviewJob]:
        """List all jobs waiting for merge authorization."""
        return self.db.list_review_jobs(state=ReviewJobState.MERGE_READY)

    def active_reviews(self) -> list[ReviewJob]:
        """List all jobs currently being reviewed (leased)."""
        return self.db.list_review_jobs(state=ReviewJobState.REVIEWING)
