"""Prismatic Gateway Verification Worker Daemon.

Runs the Review Factory deterministic pipeline in a background thread inside
the Gateway server:

    QUEUED --lease_for_verification--> VERIFYING --RF-2 verify--> REVIEW_READY
    REVIEW_READY --lease_for_review--> REVIEWING --RF-3 heuristic--> verdict
        CLEAN (witnesses met) --> MERGE_READY   (no merge authority in this phase)
        REPAIR_REQUIRED       --> repair dispatch attempted; when no automated
                                  repair dispatcher is wired, an explicit
                                  repair_dispatch_unavailable audit entry is
                                  recorded so the job never sits silently
        REJECTED              --> terminal, visible on the dashboard

No LLMs, no network model calls: this loop is entirely deterministic. The
optional LLM deep-review stage (Phase 3) slots in as an advisory, default-off
step between the heuristic review and submit_verdict; per standing policy it
can never downgrade a deterministic result.

The daemon also runs the stale-lease janitor on a schedule.

Job intake is wired at closeout: ``AgyCompletedWorkStore.ingest()`` enqueues
eligible completed work into the Review Factory (best-effort, never blocking
ingestion).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    PromotionState,
)
from prismatic.review_factory.models import (
    ReviewJob,
    ReviewJobState,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.reviewer import ReviewerCapability
from prismatic.review_factory.verifier import VerificationWorker

logger = logging.getLogger(__name__)

# Factory risk tier (0-3) -> manifest risk tier (A/B/C).
#   0 deterministic-only -> A (focused proofs only)
#   1 standard           -> B (focused + canonical + package)
#   2 sensitive / 3 prod -> C (full proof set)
_FACTORY_TIER_TO_MANIFEST_TIER = {0: "A", 1: "B", 2: "C", 3: "C"}

_KNOWN_PRODUCERS = ("agy", "ned", "jules")

_DASHBOARD_PATH_PREFIX = "prismatic/gateway/dashboard"


def _resolve_repo_path(explicit: str | Path | None = None) -> Path:
    """Resolve the git checkout the verifier materializes candidates from."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("PRISMATIC_REPO_PATH")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _producer_for_job(job: ReviewJob) -> str:
    """Derive the producer identity, mirroring queue.submit_verdict."""
    prefix = (job.completed_work_id or "").split("-")[0]
    if prefix in _KNOWN_PRODUCERS:
        return prefix
    return "agy"


def _manifest_for_job(job: ReviewJob) -> MergeCandidateManifest:
    """Build the immutable-verification manifest from a queued job (G7)."""
    changed = sorted({p for p in (job.changed_paths or []) if isinstance(p, str) and p})
    manifest_tier = _FACTORY_TIER_TO_MANIFEST_TIER.get(int(job.risk_tier or 0), "B")
    dashboard_change = any(
        p == _DASHBOARD_PATH_PREFIX or p.startswith(_DASHBOARD_PATH_PREFIX + "/")
        for p in changed
    )
    task_sha = (job.result_packet_sha256 or "").strip().lower()
    if len(task_sha) != 64 or any(c not in "0123456789abcdef" for c in task_sha):
        # Deterministic stand-in so manifest creation never depends on packet hygiene.
        task_sha = hashlib.sha256(
            (job.result_packet_path or job.review_job_id).encode("utf-8")
        ).hexdigest()
    return MergeCandidateManifest.create(
        issue_id=job.task_id or job.review_job_id,
        task_id=job.task_id or job.review_job_id,
        task_file_sha256=task_sha,
        repository=job.repository or "mbgulden/prismatic-engine",
        target="main",
        base_sha=job.base_commit,
        candidate_sha=job.candidate_commit,
        changed_paths=changed,
        producer=_producer_for_job(job),
        preserved_candidate_location=job.result_packet_path or "",
        risk_tier=manifest_tier,
        dashboard_change=dashboard_change,
        required_ci_checks=["rf-v1-verification"],
    )


class VerificationWorkerDaemon:
    """Background daemon that runs the Review Factory deterministic pipeline.

    One instance runs inside the Gateway server (see server.py). It leases
    queued jobs, runs immutable verification (RF-2), then the heuristic
    review (RF-3), and submits verdicts. Clean work with enough witnesses
    advances to MERGE_READY; nothing in this phase executes a merge.

    Jobs that fail infrastructure stages ``max_consecutive_failures`` times in
    a row (default 3) are moved to the terminal QUARANTINED state with an
    audit entry instead of being re-leased forever. The failure count is
    persisted on the job row, so a daemon restart cannot wipe poison memory.
    """

    def __init__(
        self,
        poll_interval_seconds: float = 3.0,
        worker_id: str = "gateway-verifier",
        reviewer_id: str = "rf-heuristic-reviewer",
        repo_path: str | Path | None = None,
        janitor_interval_seconds: float = 300.0,
        max_consecutive_failures: int = 3,
    ):
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_id = worker_id
        self.reviewer_id = reviewer_id
        self.repo_path = _resolve_repo_path(repo_path)
        self.janitor_interval_seconds = janitor_interval_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self.queue = ReviewQueue()
        self.worker = VerificationWorker(repo_path=self.repo_path)
        self.reviewer = ReviewerCapability(reviewer_id=self.reviewer_id)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._last_janitor: Optional[datetime] = None

    def start(self) -> None:
        """Start the background thread (idempotent)."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="verification-worker-daemon",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background thread to stop and wait for it."""
        with self._lock:
            self._running = False
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def status(self) -> dict:
        """Return daemon status for health/readiness probes."""
        with self._lock:
            running = self._running
        return {
            "running": running,
            "worker_id": self.worker_id,
            "reviewer_id": self.reviewer_id,
            "repo_path": str(self.repo_path),
            "consecutive_failures": dict(self._failures),
            "last_janitor_at": (
                self._last_janitor.isoformat() if self._last_janitor else None
            ),
        }

    # ── Main loop ────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.info(
            "verification worker daemon started (repo=%s, reviewer=%s)",
            self.repo_path,
            self.reviewer_id,
        )
        while self._running:
            try:
                did_work = self._pump_once()
                self._maybe_run_janitor()
            except Exception as exc:  # never let the loop die
                logger.warning("verification daemon pump error: %s", exc)
            time.sleep(0 if did_work else self.poll_interval_seconds)

    def _pump_once(self) -> bool:
        """Lease and process one job. Returns True when work was attempted."""
        job = self.queue.lease_for_verification(self.worker_id)
        if job is not None:
            if self._is_poisoned(job):
                # Poison: quarantine instead of re-leasing forever.
                self._quarantine_job(
                    job, stage="lease", failures=job.consecutive_failures or 0
                )
                return False
            self._run_verification_stage(job)
            return True
        job = self.queue.lease_for_review(self.reviewer_id)
        if job is not None:
            if self._is_poisoned(job):
                self._quarantine_job(
                    job, stage="lease", failures=job.consecutive_failures or 0
                )
                return False
            self._run_review_stage(job)
            return True
        return False

    # ── Verification stage (RF-2) ────────────────────────────────────

    def _run_verification_stage(self, job: ReviewJob) -> None:
        job_id = job.review_job_id
        try:
            manifest = _manifest_for_job(job)
        except Exception as exc:
            logger.error("cannot build manifest for job %s: %s", job_id, exc)
            self._record_failure(job, stage="manifest")
            return
        try:
            receipt, updated_manifest = self.worker.verify(job, manifest)
            if updated_manifest.state is not PromotionState.REVIEW_REQUIRED:
                # Verification checks failed: the work needs rework, not a
                # review. The receipt records the failure; route to repair.
                self._route_verification_failure(job, receipt)
                return
            self.queue.complete_verification(job_id, receipt, self.worker_id)
            # Persist the REVIEW_REQUIRED manifest for the review stage (and
            # Phase 4's merge executor). If this write fails, the review
            # stage's requeue fallback self-heals by re-verifying.
            if not self.queue.db.update_job_manifest(
                job_id, updated_manifest.canonical_json()
            ):
                logger.error(
                    "manifest persist failed for job %s; will requeue at review",
                    job_id,
                )
            self._clear_failures(job_id)
            logger.info(
                "verified job %s -> review_ready (receipt %s)",
                job_id,
                receipt.receipt_id,
            )
        except Exception as exc:
            # Infrastructure failure (e.g. candidate not materializable).
            # Release the lease so the job is visible and retryable; the
            # consecutive-failure guard bounds poison-job churn.
            logger.error("verification failed for job %s: %s", job_id, exc)
            self._record_failure(job, stage="verify")

    def _route_verification_failure(
        self, job: ReviewJob, receipt: VerificationReceipt
    ) -> None:
        """Send work that failed verification checks to the repair flow."""
        job_id = job.review_job_id
        self.queue.db.insert_receipt(receipt)
        moved = self.queue.db.update_review_job_state(
            job_id,
            ReviewJobState.REPAIR_REQUIRED,
            lease_owner="",
            lease_expires_at="",
        )
        repair_dispatched = None
        if moved:
            repair_dispatched = self.queue.dispatch_repair_task(
                job_id,
                failure_reason=(
                    f"verification checks failed (receipt {receipt.receipt_id}, "
                    f"classification {receipt.classification})"
                ),
            )
        self.queue.db.insert_audit_entry(
            actor=f"daemon:{self.worker_id}",
            action="verification_failed",
            review_job_id=job_id,
            details={
                "receipt_id": receipt.receipt_id,
                "classification": receipt.classification,
                "moved_to_repair": bool(moved),
                "repair_dispatched": bool(repair_dispatched),
            },
        )
        self._clear_failures(job_id)
        logger.info("verification failed for job %s -> repair_required", job_id)

    # ── Review stage (RF-3) ──────────────────────────────────────────

    def _run_review_stage(self, job: ReviewJob) -> None:
        job_id = job.review_job_id
        try:
            receipts = self.queue.db.get_receipts_for_job(job_id)
            if not receipts:
                logger.error(
                    "no verification receipt for job %s; releasing review lease",
                    job_id,
                )
                self._record_failure(job, stage="receipt")
                return
            receipt = receipts[-1]
            manifest = self._load_review_manifest(job)
            if manifest is None:
                # No usable persisted REVIEW_REQUIRED manifest (e.g. job
                # verified before manifest persistence existed). Send it back
                # for re-verification rather than reviewing blind.
                self._requeue_for_reverification(
                    job, "persisted REVIEW_REQUIRED manifest missing or invalid"
                )
                return
            pr_url = self._pr_url_for_job(job)
            decision, updated_manifest, _repair = self.reviewer.review(
                job, manifest, receipt, pr_url=pr_url
            )
            new_state = self.queue.submit_verdict(job_id, decision, self.reviewer_id)
            # Persist the CLEAN manifest once the job is merge-ready so the
            # manifest digest chain continues into Phase 4 (the merge executor
            # binds the CLEAN manifest from the DB). While more witnesses are
            # outstanding the job returns to REVIEW_READY and the
            # REVIEW_REQUIRED manifest stays in place, so the next reviewer
            # can still record theirs (record_review only accepts
            # REVIEW_REQUIRED manifests).
            if new_state == ReviewJobState.MERGE_READY.value and (
                getattr(updated_manifest, "state", None) is PromotionState.CLEAN
            ):
                if not self.queue.db.update_job_manifest(
                    job_id, updated_manifest.canonical_json()
                ):
                    logger.error("clean manifest persist failed for job %s", job_id)
            self._clear_failures(job_id)
            logger.info(
                "reviewed job %s -> %s (verdict %s)",
                job_id,
                new_state,
                decision.verdict,
            )
            if new_state == ReviewJobState.REPAIR_REQUIRED.value:
                self.queue.dispatch_repair_task(
                    job_id,
                    failure_reason=f"heuristic review verdict={decision.verdict}",
                )
        except Exception as exc:
            logger.error("review failed for job %s: %s", job_id, exc)
            self._record_failure(job, stage="review")

    @staticmethod
    def _load_review_manifest(job: ReviewJob) -> Optional[MergeCandidateManifest]:
        """Load the persisted REVIEW_REQUIRED manifest for a job.

        Returns None when the stored manifest is missing, unparseable, in
        the wrong state, or bound to different SHAs than the job.
        """
        if not job.manifest_json:
            return None
        try:
            manifest = MergeCandidateManifest.from_json(job.manifest_json)
        except Exception as exc:
            logger.warning(
                "unparseable manifest for job %s: %s", job.review_job_id, exc
            )
            return None
        if manifest.state is not PromotionState.REVIEW_REQUIRED:
            logger.warning(
                "manifest for job %s in state %s, expected REVIEW_REQUIRED",
                job.review_job_id,
                manifest.state,
            )
            return None
        if (
            manifest.candidate_sha != (job.candidate_commit or job.candidate_tree)
            or manifest.base_sha != job.base_commit
        ):
            logger.warning(
                "manifest SHAs do not match job %s", job.review_job_id
            )
            return None
        return manifest

    def _requeue_for_reverification(self, job: ReviewJob, reason: str) -> None:
        """Send a REVIEW_READY job back to QUEUED for re-verification."""
        job_id = job.review_job_id
        self.queue.force_release_lease(job_id, actor=self.reviewer_id)
        moved = self.queue.db.update_review_job_state(
            job_id,
            ReviewJobState.QUEUED,
            lease_owner="",
            lease_expires_at="",
        )
        self.queue.db.insert_audit_entry(
            actor=f"daemon:{self.reviewer_id}",
            action="requeue_for_reverification",
            review_job_id=job_id,
            details={"reason": reason, "moved": bool(moved)},
        )
        self._clear_failures(job_id)
        logger.info("requeued job %s for re-verification: %s", job_id, reason)

    def _pr_url_for_job(self, job: ReviewJob) -> Optional[str]:
        """Best-effort lookup of the PR URL from the completed-work packet."""
        try:
            from prismatic.agy_completed_work import AgyCompletedWorkStore

            row = AgyCompletedWorkStore().get(job.completed_work_id)
            pr_url = (row.packet or {}).get("pr_url")
            if pr_url:
                return str(pr_url)
        except Exception as exc:
            logger.debug("pr_url lookup failed for job %s: %s", job.review_job_id, exc)
        return None

    # ── Janitor ──────────────────────────────────────────────────────

    def _maybe_run_janitor(self) -> None:
        now = datetime.now(timezone.utc)
        if self._last_janitor is not None and (now - self._last_janitor) < timedelta(
            seconds=self.janitor_interval_seconds
        ):
            return
        try:
            counts = self.queue.run_janitor(actor="verification-daemon")
            if counts.get("total_reset"):
                logger.info(
                    "janitor reset %s stale lease(s)", counts["total_reset"]
                )
        except Exception as exc:
            logger.warning("janitor run failed: %s", exc)
        finally:
            self._last_janitor = now

    # ── Failure tracking ─────────────────────────────────────────────
    # Consecutive-failure counts are persisted on the job row
    # (review_jobs.consecutive_failures), not just in memory, so a daemon
    # restart cannot wipe poison memory. After ``max_consecutive_failures``
    # (default 3) consecutive infrastructure failures the job is moved to the
    # terminal QUARANTINED state with an audit entry -- never re-leased
    # forever.

    def _record_failure(self, job: ReviewJob, *, stage: str) -> None:
        job_id = job.review_job_id
        count = self.queue.db.increment_job_failures(job_id)
        self._failures[job_id] = count
        logger.warning(
            "job %s failure %d/%d at stage %s",
            job_id,
            count,
            self.max_consecutive_failures,
            stage,
        )
        if count >= self.max_consecutive_failures:
            self._quarantine_job(job, stage=stage, failures=count)
            return
        try:
            self.queue.force_release_lease(job_id, actor=self.worker_id)
        except Exception as exc:
            logger.warning("lease release failed for job %s: %s", job_id, exc)

    def _clear_failures(self, job_id: str) -> None:
        self._failures.pop(job_id, None)
        try:
            self.queue.db.reset_job_failures(job_id)
        except Exception as exc:
            logger.warning("failure-count reset failed for job %s: %s", job_id, exc)

    def _is_poisoned(self, job: ReviewJob) -> bool:
        return (job.consecutive_failures or 0) >= self.max_consecutive_failures

    def _quarantine_job(self, job: ReviewJob, *, stage: str, failures: int) -> None:
        """Move a poison job to the terminal QUARANTINED state.

        Quarantine is visible (audit entry ``job_quarantined``) and terminal:
        the job leaves the lease/retry loop and waits for operator attention.
        """
        job_id = job.review_job_id
        moved = self.queue.db.update_review_job_state(
            job_id,
            ReviewJobState.QUARANTINED,
            lease_owner="",
            lease_expires_at="",
        )
        self.queue.db.insert_audit_entry(
            actor=f"daemon:{self.worker_id}",
            action="job_quarantined",
            review_job_id=job_id,
            details={
                "stage": stage,
                "consecutive_failures": failures,
                "max_consecutive_failures": self.max_consecutive_failures,
                "moved": bool(moved),
            },
        )
        self._failures.pop(job_id, None)
        logger.error(
            "job %s quarantined after %d consecutive failures at stage %s",
            job_id,
            failures,
            stage,
        )


# Module-level singleton for the Gateway server lifecycle.
_daemon: Optional[VerificationWorkerDaemon] = None
_daemon_lock = threading.Lock()


def instance() -> VerificationWorkerDaemon:
    """Return the process-wide daemon singleton."""
    global _daemon
    with _daemon_lock:
        if _daemon is None:
            _daemon = VerificationWorkerDaemon()
        return _daemon


# ── Module-level lifecycle shims ─────────────────────────────────────
# server.py imports these names (the pre-#454 daemon API). They delegate to
# the process-wide singleton above so the gateway can boot.


def start_verification_daemon() -> VerificationWorkerDaemon:
    """Start the singleton verification daemon (idempotent)."""
    daemon = instance()
    daemon.start()
    return daemon


def stop_verification_daemon() -> None:
    """Stop the singleton verification daemon."""
    instance().stop()
