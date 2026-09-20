"""Prismatic Gateway Verification Worker Daemon.

Runs the Review Factory deterministic pipeline in a background thread inside
the Gateway server:

    QUEUED --lease_for_verification--> VERIFYING --RF-2 verify--> REVIEW_READY
    REVIEW_READY --lease_for_review--> REVIEWING --RF-3 heuristic--> verdict
        CLEAN (witnesses met) --> MERGE_READY   (merge stage: Phase 4)
        REPAIR_REQUIRED       --> repair dispatch attempted; when no automated
                                  repair dispatcher is wired, an explicit
                                  repair_dispatch_unavailable audit entry is
                                  recorded so the job never sits silently
        REJECTED              --> terminal, visible on the dashboard

Phase 4 merge staging: MERGE_READY jobs are processed by an optional
``MergeStage`` (default None = observe-only, jobs wait for a human). When
configured, tier 0/1 jobs can dry-run or live-merge under the standing-policy
actor; tier 2/3 NEVER auto-merge (fail closed). Stalled repairs are
re-dispatched with bounded retries and exponential backoff; exhausted jobs
fail loudly via audit + Linear and stay visible.

The deterministic loop below makes no LLM or network model calls. The optional
LLM deep-review stage (Phase 3) is an ACTIVE, default-off enrichment step that
runs after the deterministic verdict: structured findings become concrete
repair work orders dispatched through the normal repair flow, and
high-severity findings on a deterministic-CLEAN job escalate to a human
(never auto-merge). Per standing policy the LLM can never downgrade a
deterministic result, and every failure mode falls back to the deterministic
verdict unchanged.

The daemon also runs the stale-lease janitor on a schedule.

Job intake is wired at closeout: ``AgyCompletedWorkStore.ingest()`` enqueues
eligible completed work into the Review Factory (best-effort, never blocking
ingestion).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    PromotionState,
)
from prismatic.review_factory.llm_deep_review import (
    LLMDeepReviewAdapter,
    LLMFinding,
    LLMReviewConfig,
    LLMStageOutcome,
    compile_repair_packet,
    escalate_to_human,
    render_work_order_text,
    rereview_budget_remaining,
    resolve_llm_outcome,
    summarize_for_human,
)
from prismatic.review_factory.models import (
    ReviewJob,
    ReviewJobState,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.reviewer import ReviewerCapability
from prismatic.review_factory.verifier import VerificationWorker

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismatic.review_factory.merge_stage import MergeStage

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
        merge_stage: Optional["MergeStage"] = None,
        redispatch_interval_seconds: float = 900.0,
        redispatch_max_attempts: int = 3,
    ):
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_id = worker_id
        self.reviewer_id = reviewer_id
        self.repo_path = _resolve_repo_path(repo_path)
        self.janitor_interval_seconds = janitor_interval_seconds
        self.max_consecutive_failures = max_consecutive_failures
        # Phase 4: merge authority staging. None (the default) keeps the
        # daemon observe-only — MERGE_READY jobs are never leased.
        self.merge_stage = merge_stage
        self.redispatch_interval_seconds = redispatch_interval_seconds
        self.redispatch_max_attempts = redispatch_max_attempts
        self.queue = ReviewQueue()
        self.worker = VerificationWorker(repo_path=self.repo_path)
        self.reviewer = ReviewerCapability(reviewer_id=self.reviewer_id)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._last_janitor: Optional[datetime] = None
        self._last_redispatch: Optional[datetime] = None
        # Jobs refused by authorize_merge are never re-leased for merge by
        # this daemon instance; they stay MERGE_READY for a human. (The
        # state machine has no forced transition out of MERGE_READY, so
        # they can sit there indefinitely without hot-looping.)
        self._merge_authorize_refused: set[str] = set()
        self._llm_status_cache: dict = {}
        self._llm_status_cache_at: Optional[datetime] = None

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
            "merge_stage_enabled": self.merge_stage is not None
            and self.merge_stage.config.enabled,
            "last_redispatch_at": (
                self._last_redispatch.isoformat()
                if self._last_redispatch
                else None
            ),
            "llm_review": self._llm_stage_status(),
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
                self._maybe_redispatch_repairs()
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
        if self._merge_leasing_allowed():
            job = self.queue.lease_for_merge(
                self.worker_id,
                tiers=set(self.merge_stage.config.live_tiers),
                exclude_job_ids=self._merge_authorize_refused,
            )
            if job is not None:
                self._run_merge_stage(job)
                return True
        return False

    def _merge_leasing_allowed(self) -> bool:
        """True only when the merge stage may lease MERGE_READY jobs.

        Merge authority is inert by default: no leasing at all when the
        stage is unconfigured, when merge authority is disabled, or when
        no tiers are enabled in PRISMATIC_RF_MERGE_LIVE_TIERS. MERGE_READY
        jobs then simply wait for a human instead of being leased and
        released on every pump (hot loop).
        """
        stage = self.merge_stage
        return (
            stage is not None
            and bool(stage.config.enabled)
            and bool(stage.config.live_tiers)
        )

    # ── Merge stage (RF-4, Phase 4) ──────────────────────────────────

    def _run_merge_stage(self, job: ReviewJob) -> None:
        """Run one merge-stage decision; always release the lease after."""
        job_id = job.review_job_id
        try:
            result = self.merge_stage.process(job)
            logger.info(
                "merge stage for job %s -> %s", job_id, result.action
            )
            if result.action == "authorize_failed":
                # authorize_merge refused this job deterministically; never
                # re-lease it for merge (it stays MERGE_READY, visible).
                self._merge_authorize_refused.add(job_id)
        except Exception as exc:
            logger.error("merge stage error for job %s: %s", job_id, exc)
        finally:
            try:
                self.queue.release_merge_lease(job_id, self.worker_id)
            except Exception as exc:
                logger.warning(
                    "merge lease release failed for job %s: %s", job_id, exc
                )

    # ── Repair re-dispatch (bounded retries) ─────────────────────────

    def _maybe_redispatch_repairs(self) -> None:
        now = datetime.now(timezone.utc)
        if self._last_redispatch is not None and (
            now - self._last_redispatch
        ) < timedelta(seconds=self.redispatch_interval_seconds):
            return
        try:
            summary = self.queue.redispatch_stalled_repairs(
                max_attempts=self.redispatch_max_attempts,
            )
            if (
                summary.get("redispatched")
                or summary.get("exhausted")
                or summary.get("failed")
            ):
                logger.info("repair redispatch pass: %s", summary)
        except Exception as exc:
            logger.warning("repair redispatch pass failed: %s", exc)
        finally:
            self._last_redispatch = now

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
            # RF-3 (optional): ACTIVE LLM deep-review stage. Fail-closed: the
            # deterministic verdict above always stands; the LLM only enriches
            # (repair work orders, human escalation). Never raises.
            llm_extra_context = None
            llm_force_dispatch = False
            try:
                llm_outcome, new_state = self._run_llm_stage(job, decision, new_state)
                if llm_outcome is not None and llm_outcome.action in (
                    "packet",
                    "rereview_repair",
                ):
                    llm_extra_context = {
                        "llm_work_order_text": llm_outcome.work_order_text,
                        "llm_work_order": llm_outcome.packet,
                    }
                    llm_force_dispatch = llm_outcome.force_dispatch
            except Exception as exc:  # the LLM stage must never break review
                logger.warning("llm stage error for job %s: %s", job_id, exc)
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
                    extra_context=llm_extra_context,
                    force=llm_force_dispatch,
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

    # ── Optional LLM deep-review stage (RF-3) ────────────────────────
    # ACTIVE enrichment, never advisory-only. Fail-closed: every skip, error,
    # timeout, or schema-invalid output leaves the deterministic verdict
    # untouched, and the LLM can never downgrade REPAIR_REQUIRED/REJECTED.

    _LLM_STATUS_TTL_SECONDS = 120.0
    _LLM_DIFF_MAX_CHARS = 500_000

    def _llm_stage_status(self) -> dict:
        """Informative, never-nagging LLM stage state for the dashboard.

        One of: not configured | active | skipped. The health check is cached
        briefly so readiness probes never block on the network.
        """
        now = datetime.now(timezone.utc)
        if (
            self._llm_status_cache
            and self._llm_status_cache_at is not None
            and (now - self._llm_status_cache_at).total_seconds()
            < self._LLM_STATUS_TTL_SECONDS
        ):
            return dict(self._llm_status_cache)
        try:
            config = LLMReviewConfig.from_env()
            if not config.enabled:
                status = {
                    "state": "not configured",
                    "detail": (
                        "PRISMATIC_REVIEW_LLM not set and dashboard toggle off; "
                        "deterministic review runs normally"
                    ),
                }
            elif not config.model_full and not config.model_bounded:
                status = {
                    "state": "not configured",
                    "detail": "enabled but no model configured",
                }
            else:
                ok, reason = LLMDeepReviewAdapter(config).gates_pass()
                if ok:
                    status = {
                        "state": "active",
                        "detail": (
                            "Ollama reachable; a configured model is available"
                        ),
                        "endpoint": config.endpoint,
                        "model_full": config.model_full or None,
                        "model_bounded": config.model_bounded or None,
                    }
                else:
                    status = {"state": "skipped", "detail": reason}
        except Exception as exc:
            status = {"state": "skipped", "detail": f"status check failed: {exc}"}
        self._llm_status_cache = status
        self._llm_status_cache_at = now
        return dict(status)

    def _run_llm_stage(
        self, job: ReviewJob, decision, new_state: str
    ) -> tuple[LLMStageOutcome | None, str]:
        """Run the optional LLM deep-review stage.

        Returns (outcome_or_None, effective_state). The effective state may
        differ from ``new_state`` when the LLM escalates a deterministic-CLEAN
        job (held for human review) or when a re-review finds still-broken
        findings on a deterministic-CLEAN candidate. Never raises.
        """
        job_id = job.review_job_id
        config = LLMReviewConfig.from_env()
        if not config.enabled:
            return None, new_state
        db = self.queue.db
        tree = (job.candidate_tree or "").strip()

        adapter = LLMDeepReviewAdapter(config)
        ok, reason = adapter.gates_pass()
        if not ok:
            self._audit_llm_once(
                db, job_id, tree, "llm_review_skipped", {"reason": reason}
            )
            logger.info("llm stage skipped for job %s: %s", job_id, reason)
            return None, new_state

        first = db.find_audit_entry(job_id, "llm_review_completed")
        first_tree = self._audit_tree(first)
        if first is not None and first_tree == tree:
            return None, new_state  # this candidate already deep-reviewed
        if first is not None and first_tree and first_tree != tree:
            return self._run_llm_rereview(
                job, decision, new_state, adapter, config, first
            )
        return self._run_llm_first_review(job, decision, new_state, adapter)

    def _run_llm_first_review(
        self, job: ReviewJob, decision, new_state: str, adapter: LLMDeepReviewAdapter
    ) -> tuple[LLMStageOutcome | None, str]:
        job_id = job.review_job_id
        db = self.queue.db
        tree = (job.candidate_tree or "").strip()
        det_verdict = str(getattr(decision, "verdict", "") or "")
        diff_text = self._llm_candidate_diff(job)
        if not diff_text:
            self._audit_llm_once(
                db,
                job_id,
                tree,
                "llm_review_skipped",
                {"reason": "candidate diff unavailable"},
            )
            return None, new_state
        try:
            det_findings = json.loads(getattr(decision, "findings", "") or "[]")
        except Exception:
            det_findings = []
        llm = adapter.review(
            job,
            diff_text=diff_text,
            deterministic_verdict=det_verdict,
            deterministic_findings=det_findings if isinstance(det_findings, list) else [],
        )
        if llm is None:
            self._audit_llm_once(
                db,
                job_id,
                tree,
                "llm_review_skipped",
                {
                    "reason": (
                        "model call failed, timed out, or output failed "
                        "schema validation; deterministic verdict stands"
                    )
                },
            )
            return None, new_state
        self._audit_llm_once(
            db,
            job_id,
            tree,
            "llm_review_completed",
            {
                "model": llm.model,
                "verdict": llm.verdict,
                "confidence": llm.confidence,
                "rationale": llm.rationale,
                "diff_chars": len(diff_text),
                "findings": [f.to_dict() for f in llm.findings],
            },
        )
        action = resolve_llm_outcome(det_verdict, llm)
        if action == "packet":
            # Deterministic REPAIR_REQUIRED/REJECTED stands (never downgraded,
            # even if the LLM said "clean"): compile findings into a concrete
            # repair work order. The caller dispatches it for REPAIR_REQUIRED;
            # for terminal REJECTED the packet is audited for the operator.
            packet = compile_repair_packet(
                job_id=job_id,
                candidate_tree=tree,
                candidate_commit=job.candidate_commit or "",
                model=llm.model,
                deterministic_verdict=det_verdict,
                findings=llm.findings,
            )
            self._audit_llm_once(
                db,
                job_id,
                tree,
                "llm_packet_ready",
                {
                    "model": llm.model,
                    "work_items": len(packet["work_items"]),
                    "dispatched": new_state == ReviewJobState.REPAIR_REQUIRED.value,
                },
            )
            outcome = LLMStageOutcome(
                action="packet",
                summary=str(packet.get("summary") or ""),
                packet=packet,
                work_order_text=render_work_order_text(packet),
            )
            return outcome, new_state
        if action == "escalate":
            summary = summarize_for_human(
                job_id=job_id,
                task_id=job.task_id or "",
                candidate_commit=job.candidate_commit or "",
                model=llm.model,
                deterministic_verdict=det_verdict,
                findings=llm.findings,
                reason=(
                    "The deterministic CLEAN verdict stands (never downgraded); "
                    "the job is held for human review instead of proceeding."
                ),
            )
            evidence = {
                "candidate_tree": tree,
                "candidate_commit": (job.candidate_commit or "")[:12],
                "deterministic_verdict": det_verdict,
                "model": llm.model,
                "findings": [f.to_dict() for f in llm.findings][:10],
            }
            escalate_to_human(db, job, summary=summary, evidence=evidence)
            db.update_review_job_state(
                job_id, ReviewJobState.REPAIR_REQUIRED, lease_owner="", lease_expires_at=""
            )
            logger.warning(
                "llm escalation held job %s for human review (never auto-merge)",
                job_id,
            )
            outcome = LLMStageOutcome(
                action="escalate",
                summary=summary,
                detail="held for human review",
                evidence=evidence,
            )
            return outcome, ReviewJobState.REPAIR_REQUIRED.value
        # advisory: deterministic CLEAN, no high-severity findings — recorded
        # in the audit trail only.
        return (
            LLMStageOutcome(
                action="advisory",
                summary=f"LLM advisory findings recorded ({len(llm.findings)}).",
            ),
            new_state,
        )

    def _run_llm_rereview(
        self,
        job: ReviewJob,
        decision,
        new_state: str,
        adapter: LLMDeepReviewAdapter,
        config: LLMReviewConfig,
        first_entry: dict,
    ) -> tuple[LLMStageOutcome | None, str]:
        """Re-review a repaired candidate against the original LLM findings.

        Bounded: at most ``config.max_rereviews`` LLM re-reviews per job, then
        the job goes to a human.
        """
        job_id = job.review_job_id
        db = self.queue.db
        tree = (job.candidate_tree or "").strip()
        det_verdict = str(getattr(decision, "verdict", "") or "")
        try:
            orig_details = json.loads(first_entry.get("details_json") or "{}")
        except Exception:
            orig_details = {}
        original_findings = orig_details.get("findings") or []
        orig_tree = self._audit_tree(first_entry)

        remaining = rereview_budget_remaining(db, job_id, config.max_rereviews)
        if remaining <= 0:
            summary = summarize_for_human(
                job_id=job_id,
                task_id=job.task_id or "",
                candidate_commit=job.candidate_commit or "",
                model=str(orig_details.get("model") or ""),
                deterministic_verdict=det_verdict,
                findings=[],
                reason=(
                    f"LLM re-review budget exhausted ({config.max_rereviews} "
                    "re-reviews already done for this job). Routing to a human "
                    "instead of looping forever."
                ),
            )
            escalate_to_human(
                db,
                job,
                summary=summary,
                evidence={
                    "reason": "rereview_budget_exhausted",
                    "original_findings": original_findings[:10],
                },
            )
            db.update_review_job_state(
                job_id, ReviewJobState.REPAIR_REQUIRED, lease_owner="", lease_expires_at=""
            )
            self._audit_llm_once(
                db,
                job_id,
                tree,
                "llm_rereview_exhausted",
                {"max_rereviews": config.max_rereviews},
            )
            logger.warning(
                "llm re-review budget exhausted for job %s; routed to human", job_id
            )
            return (
                LLMStageOutcome(
                    action="rereview_escalate",
                    summary=summary,
                    detail="re-review budget exhausted; routed to human",
                ),
                ReviewJobState.REPAIR_REQUIRED.value,
            )

        new_diff = self._llm_candidate_diff(job)
        if not new_diff:
            self._audit_llm_once(
                db,
                job_id,
                tree,
                "llm_review_skipped",
                {"reason": "re-review diff unavailable"},
            )
            return None, new_state
        llm2 = adapter.rereview(
            job,
            new_diff_text=new_diff,
            original_findings=original_findings
            if isinstance(original_findings, list)
            else [],
            original_tree=orig_tree,
        )
        if llm2 is None:
            self._audit_llm_once(
                db,
                job_id,
                tree,
                "llm_review_skipped",
                {"reason": "re-review model call failed or output invalid"},
            )
            return None, new_state
        self._audit_llm_once(
            db,
            job_id,
            tree,
            "llm_rereview_completed",
            {
                "model": llm2.model,
                "verdict": llm2.verdict,
                "confidence": llm2.confidence,
                "rationale": llm2.rationale,
                "original_tree": orig_tree,
                "reassessments": [
                    {
                        "finding_index": r.finding_index,
                        "status": r.status,
                        "why": r.why,
                    }
                    for r in llm2.reassessments
                ],
            },
        )
        still_broken = [r for r in llm2.reassessments if r.status == "still_broken"]
        if not still_broken:
            # Every original finding is fixed — the fresh deterministic
            # verdict on the new candidate stands.
            return (
                LLMStageOutcome(
                    action="rereview_fixed",
                    summary=(
                        f"LLM re-review: all {len(llm2.reassessments)} original "
                        f"finding(s) fixed ({llm2.model})."
                    ),
                ),
                new_state,
            )
        # Still broken: compile a fresh work order against the new candidate
        # and dispatch a new repair task for it.
        idx_to_finding = {
            i: f for i, f in enumerate(original_findings) if isinstance(f, dict)
        }
        findings_for_packet: list[LLMFinding] = []
        for r in still_broken:
            raw = idx_to_finding.get(r.finding_index, {})
            findings_for_packet.append(
                LLMFinding(
                    severity=str(raw.get("severity") or "warning"),
                    file=str(raw.get("file") or ""),
                    lines=str(raw.get("lines") or "0"),
                    category=str(raw.get("category") or "correctness"),
                    explanation=str(raw.get("explanation") or r.why),
                )
            )
        packet = compile_repair_packet(
            job_id=job_id,
            candidate_tree=tree,
            candidate_commit=job.candidate_commit or "",
            model=llm2.model,
            deterministic_verdict=det_verdict,
            findings=findings_for_packet,
            reassessments=llm2.reassessments,
        )
        self._audit_llm_once(
            db,
            job_id,
            tree,
            "llm_packet_ready",
            {
                "model": llm2.model,
                "work_items": len(packet["work_items"]),
                "rereview": True,
            },
        )
        outcome = LLMStageOutcome(
            action="rereview_repair",
            summary=str(packet.get("summary") or ""),
            packet=packet,
            work_order_text=render_work_order_text(packet),
            force_dispatch=True,
        )
        effective = new_state
        if det_verdict.strip().lower() == "clean":
            # Deterministic CLEAN, but the LLM says original findings are
            # still broken — hold for repair, never auto-merge.
            db.update_review_job_state(
                job_id, ReviewJobState.REPAIR_REQUIRED, lease_owner="", lease_expires_at=""
            )
            effective = ReviewJobState.REPAIR_REQUIRED.value
            logger.warning(
                "llm re-review found still-broken findings on job %s; held for repair",
                job_id,
            )
        return outcome, effective

    def _llm_candidate_diff(self, job: ReviewJob) -> str:
        """Best-effort ``git diff base candidate`` for the LLM prompt.

        Returns "" when the diff cannot be produced; the stage is skipped.
        """
        base = (job.base_commit or "").strip()
        cand = (job.candidate_commit or job.candidate_tree or "").strip()
        if not base or not cand or not self.repo_path:
            return ""
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.repo_path), "diff", "--no-color", base, cand, "--"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:
            logger.warning("llm diff failed for job %s: %s", job.review_job_id, exc)
            return ""
        if proc.returncode != 0:
            logger.warning(
                "llm git diff rc=%s for job %s", proc.returncode, job.review_job_id
            )
            return ""
        diff = proc.stdout or ""
        if len(diff) > self._LLM_DIFF_MAX_CHARS:
            diff = (
                diff[: self._LLM_DIFF_MAX_CHARS]
                + f"\n[... diff capped at {self._LLM_DIFF_MAX_CHARS} chars ...]"
            )
        return diff

    @staticmethod
    def _audit_tree(entry: dict | None) -> str:
        if not entry:
            return ""
        try:
            return str(
                json.loads(entry.get("details_json") or "{}").get("candidate_tree")
                or ""
            )
        except Exception:
            return ""

    def _audit_llm_once(
        self, db, job_id: str, tree: str, action: str, details: dict
    ) -> None:
        """Audit an LLM stage event once per (job, action, candidate tree)."""
        existing = db.find_audit_entry(job_id, action)
        if existing is not None and self._audit_tree(existing) == tree:
            return
        db.insert_audit_entry(
            actor="review-factory:llm-review",
            action=action,
            review_job_id=job_id,
            details={**details, "candidate_tree": tree},
        )

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
