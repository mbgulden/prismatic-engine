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
import logging
import os
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
from prismatic.review_factory.events import emit_rf_event
from prismatic.review_factory.policy import PolicyEngine

logger = logging.getLogger(__name__)


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

# Agents the dispatcher can resolve for repair work (mirrors
# prismatic.dispatcher.ASSIGNED_AGENT_KNOWN_AGENTS).
_REPAIR_DISPATCH_AGENTS = {"kai", "fred", "agy", "george"}

# Env override for the repair target agent.
_REPAIR_AGENT_ENV = "RF_REPAIR_AGENT"


def _decision_failure_summary(decision) -> str:
    """One-line summary of a review decision's findings for repair dispatch."""
    try:
        findings = json.loads(decision.findings or "[]")
    except Exception:
        findings = []
    bits = []
    for finding in findings[:5]:
        if isinstance(finding, dict):
            bits.append(
                str(
                    finding.get("message")
                    or finding.get("detail")
                    or finding.get("check")
                    or ""
                )
            )
        else:
            bits.append(str(finding))
    summary = "; ".join(b for b in bits if b)
    return summary[:500] or f"verdict {decision.verdict}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_iso(iso_str: str) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


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

        job_id = self.db.insert_review_job(job)
        emit_rf_event(
            "review_factory.job_enqueued",
            {
                "review_job_id": job_id,
                "task_id": task_id,
                "risk_tier": classification.risk_tier,
                "repository": repository,
            },
        )
        return job_id

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
        worker_id: str,
    ) -> bool:
        """Store a receipt only for the exact active verification lease."""
        job = self.db.get_review_job(review_job_id)
        if job is None:
            raise ValueError(f"Review job {review_job_id} not found")
        if job.state != ReviewJobState.VERIFYING.value:
            raise ValueError(
                f"Job {review_job_id} is not in verifying state: {job.state}"
            )

        receipt_job_id = (getattr(receipt, "review_job_id", "") or "").strip()
        if not receipt_job_id:
            raise ValueError("Review job identity required in receipt")
        if receipt_job_id != review_job_id:
            raise ValueError(
                f"Cross-job receipt mismatch: receipt review_job_id ({receipt_job_id}) does not match target job ({review_job_id})"
            )

        worker = (worker_id or "").strip()
        if not worker:
            raise ValueError("Worker identity required")
        if not job.lease_owner or worker != job.lease_owner:
            raise ValueError(
                f"Worker identity mismatch: lease owner is {job.lease_owner}, got {worker}"
            )
        if not job.lease_expires_at:
            raise ValueError(f"Job {review_job_id} has no verification lease expiry")
        expiry = _parse_iso(job.lease_expires_at)
        if expiry is None or expiry <= _utcnow():
            raise ValueError(
                f"Lease for job {review_job_id} expired at {job.lease_expires_at}"
            )

        candidate_commit = (
            getattr(receipt, "candidate_commit", "")
            or getattr(receipt, "candidate_sha", "")
        ).strip()
        if not candidate_commit:
            raise ValueError("Candidate commit required in receipt")
        if candidate_commit != job.candidate_commit:
            raise ValueError(
                f"Cross-job candidate commit mismatch: receipt candidate ({candidate_commit}) != job candidate ({job.candidate_commit})"
            )

        candidate_tree = (getattr(receipt, "candidate_tree", "") or "").strip()
        if not candidate_tree:
            raise ValueError("Candidate tree required in receipt")
        if candidate_tree != job.candidate_tree:
            raise ValueError(
                f"Cross-job candidate tree mismatch: receipt tree ({candidate_tree}) != job tree ({job.candidate_tree})"
            )

        # All authoritative checks precede the first durable mutation.
        self.db.insert_receipt(receipt)
        updated = self.db.update_review_job_state(
            review_job_id,
            ReviewJobState.REVIEW_READY,
            lease_owner="",
            lease_expires_at="",
        )
        if updated:
            emit_rf_event(
                "review_factory.receipt_issued",
                {
                    "review_job_id": review_job_id,
                    "receipt_id": receipt.receipt_id,
                    "classification": receipt.classification,
                },
            )
            emit_rf_event(
                "review_factory.job_state_changed",
                {
                    "review_job_id": review_job_id,
                    "new_state": ReviewJobState.REVIEW_READY.value,
                },
            )
        return updated

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

        # Enforce reviewer independence: producer cannot review their own job
        if job.completed_work_id and job.completed_work_id.startswith(
            f"{reviewer_id}-"
        ):
            return None

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
        reviewer_id: str,
    ) -> str:
        """Store a verdict only for the exact active review lease."""
        job = self.db.get_review_job(review_job_id)
        if job is None:
            raise ValueError(f"Review job {review_job_id} not found")

        reviewer = (reviewer_id or "").strip()
        decision_reviewer = (getattr(decision, "reviewer_id", "") or "").strip()
        if not reviewer or not decision_reviewer:
            raise ValueError("Reviewer identity required")
        if reviewer != decision_reviewer:
            raise ValueError(
                f"Reviewer identity mismatch: decision reviewer is {decision_reviewer}, got {reviewer}"
            )

        # Check reviewer independence (distinct from producer)
        producer = None
        try:
            from prismatic.agy_completed_work import AgyCompletedWorkStore

            store = AgyCompletedWorkStore(db_path=self.db.db_path)
            completed_work = store.get(job.completed_work_id)
            producer = completed_work.agent
        except Exception:
            pass

        if not producer:
            cw_id = job.completed_work_id.lower().strip()
            for prefix in ("agy", "ned", "jules"):
                if cw_id.startswith(f"{prefix}-") or f"-{prefix}-" in cw_id:
                    producer = prefix
                    break

        if producer and reviewer.lower().strip() == producer.lower().strip():
            raise ValueError(
                f"Reviewer identity reuse: reviewer '{reviewer}' is not independent of producer '{producer}'"
            )

        decision_job_id = (getattr(decision, "review_job_id", "") or "").strip()
        if not decision_job_id:
            raise ValueError("Review job identity required in decision")
        if decision_job_id != review_job_id:
            raise ValueError(
                f"Cross-job decision mismatch: decision review_job_id ({decision_job_id}) does not match target job ({review_job_id})"
            )

        candidate_commit = (
            getattr(decision, "candidate_commit", "")
            or getattr(decision, "candidate_sha", "")
        ).strip()
        if not candidate_commit:
            raise ValueError("Candidate commit required in decision")
        if candidate_commit != job.candidate_commit:
            raise ValueError(
                f"Cross-job candidate commit mismatch: decision candidate ({candidate_commit}) != job candidate ({job.candidate_commit})"
            )

        candidate_tree = (getattr(decision, "candidate_tree", "") or "").strip()
        if not candidate_tree:
            raise ValueError("Candidate tree required in decision")
        if candidate_tree != job.candidate_tree:
            raise ValueError(
                f"Cross-job candidate tree mismatch: decision tree ({candidate_tree}) != job tree ({job.candidate_tree})"
            )

        receipt_id = (getattr(decision, "receipt_id", "") or "").strip()
        if not receipt_id:
            raise ValueError("Receipt identity required in decision")
        matching_receipt = next(
            (
                receipt
                for receipt in self.db.get_receipts_for_job(review_job_id)
                if receipt.receipt_id == receipt_id
            ),
            None,
        )
        if matching_receipt is None:
            raise ValueError(
                f"Receipt {receipt_id} is not bound to review job {review_job_id}"
            )
        if (
            matching_receipt.candidate_commit != candidate_commit
            or matching_receipt.candidate_tree != candidate_tree
        ):
            raise ValueError("Decision candidate does not match bound receipt")

        # Exact/idempotent reviewer retries are no-ops even after the first
        # accepted verdict cleared the lease. They must not add witnesses.
        semantic = (
            decision.review_job_id,
            decision.reviewer_id,
            decision.candidate_commit,
            decision.candidate_tree,
            decision.receipt_id,
            decision.verdict,
            decision.findings,
        )
        for existing in self.db.get_decisions_for_job(review_job_id):
            existing_semantic = (
                existing.review_job_id,
                existing.reviewer_id,
                existing.candidate_commit,
                existing.candidate_tree,
                existing.receipt_id,
                existing.verdict,
                existing.findings,
            )
            if existing.idempotency_key == decision.idempotency_key:
                if existing_semantic != semantic:
                    raise ValueError("Decision idempotency-key collision")
                return job.state
            if (
                existing.reviewer_id == decision.reviewer_id
                and existing.candidate_tree == decision.candidate_tree
                and existing.verdict == decision.verdict
            ):
                return job.state

        if job.state != ReviewJobState.REVIEWING.value:
            raise ValueError(
                f"Job {review_job_id} is not in reviewing state: {job.state}"
            )
        if not job.lease_owner or reviewer != job.lease_owner:
            raise ValueError(
                f"Reviewer identity mismatch: lease owner is {job.lease_owner}, got {reviewer}"
            )
        if not job.lease_expires_at:
            raise ValueError(f"Job {review_job_id} has no review lease expiry")
        expiry = _parse_iso(job.lease_expires_at)
        if expiry is None or expiry <= _utcnow():
            raise ValueError(
                f"Lease for job {review_job_id} expired at {job.lease_expires_at}"
            )

        # All authoritative checks precede the first durable mutation.
        self.db.insert_decision(decision)
        verdict = ReviewVerdict(decision.verdict)

        if verdict == ReviewVerdict.CLEAN:
            new_witnesses = self.db.increment_witnesses(review_job_id)
            if new_witnesses >= job.required_witnesses:
                self.db.update_review_job_state(
                    review_job_id,
                    ReviewJobState.MERGE_READY,
                    lease_owner="",
                    lease_expires_at="",
                )
                return ReviewJobState.MERGE_READY.value
            self.db.update_review_job_state(
                review_job_id,
                ReviewJobState.REVIEW_READY,
                lease_owner="",
                lease_expires_at="",
            )
            return ReviewJobState.REVIEW_READY.value

        if verdict == ReviewVerdict.REPAIR_REQUIRED:
            self.db.update_review_job_state(
                review_job_id,
                ReviewJobState.REPAIR_REQUIRED,
                lease_owner="",
                lease_expires_at="",
            )
            packet = RepairPacket(
                review_job_id=review_job_id,
                candidate_tree=job.candidate_tree,
                findings_json=decision.findings,
                producer_id=decision.reviewer_id,
            )
            self.db.insert_repair_packet(packet)
            # Close the loop: hand the rework to the engine's task intake
            # so a fix is produced and the job can be re-verified.
            self.dispatch_repair_task(
                review_job_id,
                failure_reason=_decision_failure_summary(decision),
            )
            return ReviewJobState.REPAIR_REQUIRED.value

        if verdict == ReviewVerdict.REJECTED:
            self.db.update_review_job_state(
                review_job_id,
                ReviewJobState.REJECTED,
                lease_owner="",
                lease_expires_at="",
            )
            return ReviewJobState.REJECTED.value

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
    ) -> str | None:
        """Create a merge authorization for a merge-ready job.

        For Tier 0/1: actor = "standing-policy: tier-N" (auto).
        For Tier 2/3: actor = human identifier (manual).

        Returns authorization_id, or None if the job isn't merge-ready.
        """
        job = self.db.get_review_job(review_job_id)
        if job is None or job.state != ReviewJobState.MERGE_READY.value:
            return None
        if (
            isinstance(expires_minutes, bool)
            or not isinstance(expires_minutes, int)
            or not 1 <= expires_minutes <= 1440
        ):
            logger.warning(
                "authorize_merge rejected: expires_minutes must be an integer from 1 to 1440"
            )
            return None

        tier = job.risk_tier
        if not actor or not actor.strip():
            logger.warning(
                "authorize_merge rejected: explicit non-whitespace actor identity required"
            )
            return None
        actor = actor.strip()

        if tier <= RiskTier.STANDARD:
            expected_actor = f"standing-policy: tier-{tier}"
            if actor != expected_actor:
                logger.warning(
                    "authorize_merge rejected: tier-%s requires actor %s",
                    tier,
                    expected_actor,
                )
                return None
            scope = MergeScope.TIER_0_AUTO if tier == 0 else MergeScope.TIER_1_AUTO
        else:
            if (
                not actor.startswith("human:")
                or not actor.removeprefix("human:").strip()
            ):
                logger.warning(
                    "authorize_merge rejected: tier-%s requires human:<identity>",
                    tier,
                )
                return None
            scope = (
                MergeScope.TIER_2_EXCEPTION
                if tier == 2
                else MergeScope.TIER_3_EXCEPTION
            )

        now = _utcnow()
        auth = MergeAuthorization(
            review_job_id=review_job_id,
            repository=job.repository,
            pr_number=pr_number,
            pr_head_commit=pr_head_commit or job.candidate_commit,
            pr_base_commit=pr_base_commit or job.base_commit,
            candidate_tree=job.candidate_tree or job.candidate_commit,
            expected_merge_tree=expected_merge_tree
            or job.candidate_tree
            or job.candidate_commit,
            policy_version=job.policy_version,
            actor=actor,
            scope=scope.value,
            expires_at=(now + timedelta(minutes=expires_minutes)).isoformat(),
            idempotency_key=hashlib.sha256(
                f"{review_job_id}-{job.candidate_tree}-{now.isoformat()}".encode()
            ).hexdigest(),
        )

        if not self.db.create_authorization_and_transition(auth, now=now):
            return None
        auth_id = auth.authorization_id
        emit_rf_event(
            "review_factory.authorization_created",
            {
                "review_job_id": review_job_id,
                "authorization_id": auth_id,
                "actor": actor,
                "scope": scope.value,
            },
        )
        emit_rf_event(
            "review_factory.job_state_changed",
            {
                "review_job_id": review_job_id,
                "new_state": ReviewJobState.MERGE_AUTHORIZED.value,
            },
        )
        self.db.insert_audit_entry(
            actor=actor,
            action="authorize_merge",
            review_job_id=review_job_id,
            details={"authorization_id": auth_id, "scope": scope.value},
        )
        return auth_id

    # ── Repair cycle ─────────────────────────────────────────────────

    def consume_repair(
        self,
        review_job_id: str,
        new_candidate_commit: str,
        new_candidate_tree: str = "",
        new_changed_paths: Optional[list[str]] = None,
    ) -> bool:
        """Re-enqueue a repaired candidate after a repair cycle.

        Atomically consumes outstanding repair packets, updates candidate
        commit/tree/paths, invalidates stale evidence, and transitions the
        job back to ``queued`` for re-verification in a single SQL transaction.
        """
        return self.db.consume_repair_and_requeue_job(
            review_job_id=review_job_id,
            new_candidate_commit=new_candidate_commit,
            new_candidate_tree=new_candidate_tree,
            new_changed_paths=new_changed_paths,
        )

    def requeue_repaired_candidate(
        self,
        review_job_id: str,
        new_candidate_commit: str,
        new_candidate_tree: str = "",
        new_changed_paths: Optional[list[str]] = None,
    ) -> bool:
        """Re-queue a repaired candidate for re-verification.

        This is the entry point named in the repair task instructions handed
        to repair agents. It consumes outstanding repair packets, swaps in the
        new candidate, and returns the job to ``queued`` atomically.
        """
        return self.consume_repair(
            review_job_id,
            new_candidate_commit,
            new_candidate_tree=new_candidate_tree,
            new_changed_paths=new_changed_paths,
        )

    # ── Repair dispatch (closes the review → rework loop) ─────────────

    def dispatch_repair_task(
        self,
        review_job_id: str,
        failure_reason: str = "",
        target_agent: str | None = None,
        force: bool = False,
    ) -> Optional[str]:
        """Dispatch a repair task into the engine's multi-channel task intake.

        Wires Review Factory rejections into the existing producer intake
        (``prismatic.ingestion_queue.enqueue_multi_channel_task``) — the same
        durable queue that Telegram / AGY-CLI / Hub-UI / Linear tasks enter,
        drained by ``prismatic.dispatcher`` and visible on the dashboard.

        The enqueued repair task carries the review job id, the Linear task
        id, the verdict receipt, and the repair packet id, so a fix can be
        re-verified. After repairing, the worker re-queues the job via
        ``ReviewQueue().requeue_repaired_candidate(review_job_id,
        new_candidate_commit, new_candidate_tree)``.

        Idempotent per job: when a ``repair_dispatched`` audit entry already
        exists for the job, the recorded intake event id is returned without
        enqueueing a second task — unless ``force=True`` (bounded re-dispatch
        via ``redispatch_stalled_repairs``), which always enqueues a fresh
        task and increments the job's repair-attempt budget.

        Returns the intake event id, or None when the intake is genuinely
        unavailable — in which case a loud ``repair_dispatch_unavailable``
        audit entry is recorded (last-resort fallback, never silent).
        """
        job = self.db.get_review_job(review_job_id)
        if not job:
            return None

        if not force:
            existing = self.db.find_audit_entry(review_job_id, "repair_dispatched")
            if existing:
                try:
                    prior = json.loads(existing.get("details_json") or "{}")
                    if prior.get("intake_event_id"):
                        return str(prior["intake_event_id"])
                except Exception:
                    pass

        context = self._repair_context(review_job_id, job, failure_reason)

        try:
            from prismatic.ingestion_queue import enqueue_multi_channel_task

            agent = (
                target_agent or os.environ.get(_REPAIR_AGENT_ENV, "fred")
            ).strip().lower()
            if agent not in _REPAIR_DISPATCH_AGENTS:
                logger.warning("unknown repair agent %r; falling back to fred", agent)
                agent = "fred"
            row = enqueue_multi_channel_task(
                identifier=f"RF-REPAIR-{review_job_id[:8]}",
                channel="review-factory",
                target_agent=agent,
                title=context["title"],
                affected_paths=list(job.changed_paths),
                depends_on=[job.task_id] if job.task_id else None,
                priority=1,
                raw_payload=context["payload"],
            )
        except Exception as exc:
            self.db.insert_audit_entry(
                actor="review-factory:repair-dispatch",
                action="repair_dispatch_unavailable",
                review_job_id=review_job_id,
                details={
                    "failure_reason": failure_reason,
                    "task_id": job.task_id,
                    "candidate_commit": (job.candidate_commit or "")[:8],
                    "error": str(exc)[:200],
                    "note": "task intake unavailable; operator action required",
                },
            )
            logger.warning(
                "repair dispatch failed for job %s (task %s): %s -- "
                "operator action required",
                review_job_id,
                job.task_id,
                exc,
            )
            return None

        event_id = str(row.get("event_id") or "")
        attempt = self.db.note_repair_dispatch(
            review_job_id, datetime.now(timezone.utc).isoformat()
        )
        self.db.insert_audit_entry(
            actor="review-factory:repair-dispatch",
            action="repair_dispatched",
            review_job_id=review_job_id,
            details={
                "intake_event_id": event_id,
                "identifier": row.get("identifier"),
                "channel": "review-factory",
                "target_agent": agent,
                "task_id": job.task_id,
                "candidate_commit": (job.candidate_commit or "")[:8],
                "failure_reason": failure_reason,
                "repair_packet_id": context["payload"].get("repair_packet_id"),
                "attempt": attempt,
                "forced_redispatch": bool(force),
            },
        )
        logger.info(
            "repair task dispatched for job %s (task %s) as intake event %s (attempt %d)",
            review_job_id,
            job.task_id,
            event_id,
            attempt,
        )
        self._notify_linear_issue(
            job, context, event_id, attempt=attempt,
            failure_reason=failure_reason,
        )
        emit_rf_event(
            "review_factory.repair_dispatched",
            {
                "review_job_id": review_job_id,
                "intake_event_id": event_id,
                "attempt": attempt,
            },
        )
        return event_id

    def _repair_context(
        self, review_job_id: str, job: ReviewJob, failure_reason: str
    ) -> dict:
        """Build the repair title + payload handed to the task intake."""
        decisions = self.db.get_decisions_for_job(review_job_id)
        latest = decisions[-1] if decisions else None
        verdict, reviewer_id, receipt_id = "", "", ""
        findings: list = []
        if latest is not None:
            verdict = latest.verdict or ""
            reviewer_id = latest.reviewer_id or ""
            receipt_id = latest.receipt_id or ""
            try:
                findings = json.loads(latest.findings or "[]")
            except Exception:
                findings = []
        packets = self.db.get_unconsumed_repairs(job.candidate_tree or "")
        packet_id = packets[0].packet_id if packets else ""

        lines = [
            f"Review Factory repair for job {review_job_id} "
            f"(task {job.task_id or 'n/a'}).",
            f"Repository: {job.repository or 'n/a'}",
            f"Rejected candidate: {(job.candidate_commit or '')[:12]} "
            f"(tree {(job.candidate_tree or '')[:12]})",
            f"Verdict: {verdict or 'n/a'} by {reviewer_id or 'n/a'}; "
            f"receipt {receipt_id or 'n/a'}",
            f"Reason: {failure_reason or 'see findings'}",
            "",
            "Findings:",
        ]
        if findings:
            for finding in findings[:20]:
                if isinstance(finding, dict):
                    lines.append(
                        "- [{}] {}: {}".format(
                            finding.get("severity", "?"),
                            finding.get("check") or finding.get("code") or "finding",
                            finding.get("message") or finding.get("detail") or "",
                        ).rstrip()
                    )
                else:
                    lines.append(f"- {finding}")
        else:
            lines.append("- (no structured findings recorded)")
        lines += [
            "",
            "Repair instructions:",
            "1. Fix the findings above in the repository worktree.",
            "2. Push the fix as a new commit (do NOT force-push the rejected candidate).",
            "3. Re-queue the job for re-verification:",
            "   from prismatic.review_factory.queue import ReviewQueue",
            "   ReviewQueue().requeue_repaired_candidate(",
            f"       {review_job_id!r}, new_candidate_commit, new_candidate_tree)",
        ]
        label = job.task_id or job.repository or review_job_id[:8]
        title = f"REPAIR: fix rejected candidate for {label}"
        payload = {
            "kind": "review-factory-repair",
            "review_job_id": review_job_id,
            "task_id": job.task_id,
            "repository": job.repository,
            "base_commit": job.base_commit,
            "candidate_commit": job.candidate_commit,
            "candidate_tree": job.candidate_tree,
            "verdict": verdict,
            "reviewer_id": reviewer_id,
            "receipt_id": receipt_id,
            "failure_reason": failure_reason,
            "findings": findings,
            "repair_packet_id": packet_id,
            "title": title,
            "description": "\n".join(lines),
            "requeue": {
                "method": "ReviewQueue.requeue_repaired_candidate",
                "review_job_id": review_job_id,
            },
        }
        return {"title": title, "payload": payload}

    def _notify_linear_issue(
        self,
        job,
        context: dict,
        intake_event_id: str,
        attempt: int = 1,
        failure_reason: str = "",
    ) -> None:
        """Wire a repair dispatch into Linear — loudly, never silently.

        Delegates to :class:`prismatic.review_factory.linear_hooks.LinearReviewHooks`:
        the linked Linear issue gets a comment, or a new issue is created when
        the job has none. Every outcome (including "Linear unconfigured") is an
        audit entry; this method never raises.
        """
        try:
            from prismatic.review_factory.linear_hooks import LinearReviewHooks

            hooks = LinearReviewHooks(db=self.db)
            hooks.notify_repair_required(
                job,
                failure_reason=failure_reason
                or context["payload"].get("failure_reason", ""),
                intake_event_id=intake_event_id,
                attempt=attempt,
            )
        except Exception as exc:
            logger.warning(
                "linear repair notify failed for %s: %s",
                getattr(job, "review_job_id", "?"),
                exc,
            )

    # ── Janitor ──────────────────────────────────────────────────────

    def force_release_lease(
        self, review_job_id: str, actor: str = "operator", client_ip: str = ""
    ) -> bool:
        """Force-release a stuck lease on a review job."""
        job = self.db.get_review_job(review_job_id)
        if job is None:
            return False

        new_state = job.state
        if job.state == ReviewJobState.VERIFYING.value:
            new_state = ReviewJobState.QUEUED
        elif job.state == ReviewJobState.REVIEWING.value:
            new_state = ReviewJobState.REVIEW_READY
        elif job.state == ReviewJobState.MERGE_READY.value:
            # Merge-stage lease: release via the dedicated path (the generic
            # transition is a same-state no-op and would not clear the lease).
            released = self.db.release_merge_lease(review_job_id, job.lease_owner)
            if released:
                self.db.insert_audit_entry(
                    actor=actor,
                    action="force_release_lease",
                    review_job_id=review_job_id,
                    client_ip=client_ip,
                    details={
                        "previous_state": job.state,
                        "new_state": ReviewJobState.MERGE_READY.value,
                    },
                )
            return released
        else:
            return False

        updated = self.db.update_review_job_state(
            review_job_id,
            new_state,
            lease_owner="",
            lease_expires_at="",
        )
        if updated:
            self.db.insert_audit_entry(
                actor=actor,
                action="force_release_lease",
                review_job_id=review_job_id,
                client_ip=client_ip,
                details={
                    "previous_state": job.state,
                    "new_state": new_state.value,
                    "previous_owner": job.lease_owner,
                },
            )
            emit_rf_event(
                "review_factory.job_state_changed",
                {"review_job_id": review_job_id, "new_state": new_state},
            )
        return updated

    def run_janitor(
        self, actor: str = "operator", client_ip: str = ""
    ) -> dict[str, int]:
        """Run the lease janitor to reset stale leases.

        Returns a dict of counts: {'verifying_reset': N, 'reviewing_reset': M, 'stale_leases_reset': N+M}
        """
        total = self.db.reset_stale_leases()
        if total > 0:
            self.db.insert_audit_entry(
                actor=actor,
                action="run_janitor",
                client_ip=client_ip,
                details={"reset_count": total},
            )
        return {
            "verifying_reset": total,
            "reviewing_reset": 0,
            "total_reset": total,
            "stale_leases_reset": total,
        }

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

    def lease_for_merge(
        self,
        worker_id: str,
        lease_seconds: int = 600,
        tiers: Optional[set] = None,
        exclude_job_ids: Optional[set] = None,
    ) -> Optional[ReviewJob]:
        """Lease one MERGE_READY job for the merge stage (Phase 4).

        The lease does not change job state — the merge stage decides
        (dry-run / live / refuse) and the executor's atomic authorization
        claim guards double execution. ``tiers`` restricts which risk tiers
        may be leased and ``exclude_job_ids`` skips specific jobs, so
        refused jobs stay MERGE_READY without hot-looping. Returns None
        when no matching unleased MERGE_READY job exists.
        """
        return self.db.lease_merge_ready(
            worker_id,
            lease_seconds,
            tiers=tiers,
            exclude_job_ids=exclude_job_ids,
        )

    def release_merge_lease(self, review_job_id: str, worker_id: str) -> bool:
        """Release a merge lease, leaving the job MERGE_READY."""
        return self.db.release_merge_lease(review_job_id, worker_id)

    # ── Bounded repair re-dispatch ───────────────────────────────────

    def redispatch_stalled_repairs(
        self,
        *,
        max_attempts: int = 3,
        stall_timeout_seconds: int = 3600,
        backoff_base_seconds: int = 900,
        now: Optional[datetime] = None,
    ) -> dict:
        """Re-dispatch stalled repairs with bounded retries and backoff.

        For each REPAIR_REQUIRED job:

        - Jobs never dispatched get a first dispatch immediately (they are
          already stalled by definition).
        - Jobs dispatched before attempt tracking existed are backfilled from
          the audit log's most recent ``repair_dispatched`` entry — no
          duplicate dispatch, the backoff clock starts from the original.
        - Otherwise, when ``attempts < max_attempts`` and the job has been
          quiet for ``max(stall_timeout, backoff_base * 2**(attempts-1))``
          since the last dispatch, a fresh repair task is force-dispatched
          (exponential backoff).
        - When ``attempts >= max_attempts``, the job FAILS LOUD: a
          ``repair_redispatch_exhausted`` audit entry plus a Linear notice.
          The job stays REPAIR_REQUIRED (visible, never silently dropped)
          for operator action.
        - When the intake call returns no task id, the attempt is audited
          as ``repair_redispatch_no_task_id`` and reported under
          ``failed`` — never as a successful redispatch.

        Never raises: per-job errors are audit entries, and the summary is
        returned for the caller's log.
        """
        current = now or datetime.now(timezone.utc)
        summary: dict = {
            "redispatched": [],
            "exhausted": [],
            "skipped": 0,
            "failed": [],
        }
        try:
            jobs = self.db.list_review_jobs(state=ReviewJobState.REPAIR_REQUIRED)
        except Exception as exc:
            logger.warning("redispatch scan failed: %s", exc)
            return summary

        for job in jobs:
            job_id = job.review_job_id
            try:
                self._redispatch_one(
                    job, current, max_attempts,
                    stall_timeout_seconds, backoff_base_seconds, summary,
                )
            except Exception as exc:
                logger.warning("redispatch failed for job %s: %s", job_id, exc)
                try:
                    self.db.insert_audit_entry(
                        actor="review-factory:repair-redispatch",
                        action="repair_redispatch_error",
                        review_job_id=job_id,
                        details={"error": str(exc)[:200]},
                    )
                except Exception:
                    pass
        return summary

    @staticmethod
    def _within_seconds(iso_ts: str, current: datetime, window_seconds: int) -> bool:
        """True when an ISO timestamp is within ``window_seconds`` of now."""
        try:
            dt = datetime.fromisoformat((iso_ts or "").strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (current - dt).total_seconds() < window_seconds
        except Exception:
            return False

    def _redispatch_one(
        self, job, current: datetime, max_attempts: int,
        stall_timeout_seconds: int, backoff_base_seconds: int,
        summary: dict,
    ) -> None:
        job_id = job.review_job_id
        attempts = int(getattr(job, "repair_attempts", 0) or 0)
        last_at = (getattr(job, "repair_last_dispatch_at", "") or "").strip()

        if attempts == 0:
            prior = self.db.find_audit_entry(job_id, "repair_dispatched")
            if prior is not None:
                # Dispatched before attempt tracking existed: backfill the
                # budget from history instead of firing a duplicate.
                ts = str(prior.get("timestamp") or "")
                self.db.backfill_repair_dispatch(job_id, 1, ts)
                self.db.insert_audit_entry(
                    actor="review-factory:repair-redispatch",
                    action="repair_dispatch_backfilled",
                    review_job_id=job_id,
                    details={"attempts": 1, "original_dispatch_at": ts},
                )
                summary["skipped"] += 1
                return
            # Never dispatched at all — but don't hammer a dead intake:
            # a recent repair_dispatch_unavailable means back off.
            unavailable = self.db.find_audit_entry(
                job_id, "repair_dispatch_unavailable"
            )
            if unavailable is not None and self._within_seconds(
                str(unavailable.get("timestamp") or ""),
                current,
                stall_timeout_seconds,
            ):
                summary["skipped"] += 1
                return
            # Never dispatched at all: first attempt now.
            event_id = self.dispatch_repair_task(
                job_id,
                failure_reason="repair never dispatched; first attempt",
                force=True,
            )
            if not event_id:
                self._audit_redispatch_no_task_id(
                    job_id, attempt=1, max_attempts=max_attempts
                )
                summary["failed"].append({"job_id": job_id, "attempt": 1})
                return
            summary["redispatched"].append(
                {"job_id": job_id, "attempt": 1, "intake_event_id": event_id}
            )
            return

        if attempts >= max_attempts:
            if self.db.find_audit_entry(job_id, "repair_redispatch_exhausted") is None:
                self.db.insert_audit_entry(
                    actor="review-factory:repair-redispatch",
                    action="repair_redispatch_exhausted",
                    review_job_id=job_id,
                    details={
                        "attempts": attempts,
                        "max_attempts": max_attempts,
                        "note": (
                            "repair redispatch budget exhausted; job remains "
                            "REPAIR_REQUIRED for operator action — never "
                            "silently dropped"
                        ),
                    },
                )
                logger.error(
                    "repair redispatch exhausted for job %s after %d attempts",
                    job_id, attempts,
                )
                try:
                    from prismatic.review_factory.linear_hooks import (
                        LinearReviewHooks,
                    )

                    LinearReviewHooks(db=self.db).notify_repair_exhausted(
                        job, attempts=attempts
                    )
                except Exception as exc:
                    logger.warning(
                        "linear exhaustion hook failed for %s: %s", job_id, exc
                    )
                summary["exhausted"].append(
                    {"job_id": job_id, "attempts": attempts}
                )
            else:
                summary["skipped"] += 1
            return

        wait_seconds = max(
            stall_timeout_seconds, backoff_base_seconds * (2 ** (attempts - 1))
        )
        try:
            last_dt = datetime.fromisoformat(last_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            quiet_seconds = (current - last_dt).total_seconds()
        except Exception:
            quiet_seconds = float("inf")
        if quiet_seconds < wait_seconds:
            summary["skipped"] += 1
            return

        event_id = self.dispatch_repair_task(
            job_id,
            failure_reason=(
                f"repair attempt {attempts} produced no repaired candidate "
                f"within {int(quiet_seconds)}s; redispatching "
                f"(attempt {attempts + 1}/{max_attempts})"
            ),
            force=True,
        )
        if not event_id:
            self._audit_redispatch_no_task_id(
                job_id, attempt=attempts + 1, max_attempts=max_attempts
            )
            summary["failed"].append(
                {"job_id": job_id, "attempt": attempts + 1}
            )
            return
        self.db.insert_audit_entry(
            actor="review-factory:repair-redispatch",
            action="repair_redispatched",
            review_job_id=job_id,
            details={
                "attempt": attempts + 1,
                "max_attempts": max_attempts,
                "quiet_seconds": int(quiet_seconds),
                "intake_event_id": event_id,
            },
        )
        summary["redispatched"].append(
            {
                "job_id": job_id,
                "attempt": attempts + 1,
                "intake_event_id": event_id,
            }
        )

    def _audit_redispatch_no_task_id(
        self, job_id: str, *, attempt: int, max_attempts: int
    ) -> None:
        """Record a redispatch whose intake call returned no task id.

        Never reported as a successful redispatch: the intake produced no
        task, so the job is still stalled and stays REPAIR_REQUIRED for the
        next pass (or exhaustion).
        """
        self.db.insert_audit_entry(
            actor="review-factory:repair-redispatch",
            action="repair_redispatch_no_task_id",
            review_job_id=job_id,
            details={
                "attempt": attempt,
                "max_attempts": max_attempts,
                "note": (
                    "repair task intake returned no task id; NOT counted "
                    "as a successful redispatch"
                ),
            },
        )
        logger.error(
            "repair redispatch for job %s produced no intake task id "
            "(attempt %d/%d)",
            job_id,
            attempt,
            max_attempts,
        )

    def active_reviews(self) -> list[ReviewJob]:
        """List all jobs currently being reviewed (leased)."""
        return self.db.list_review_jobs(state=ReviewJobState.REVIEWING)
