"""RF Phase 4: merge authority staging (rollout ladder §6 of the master plan).

The daemon's merge step runs through ``MergeStage``. The default is
observe-only: MERGE_READY jobs wait for a human unless merge authority is
explicitly enabled via config/env.

Ladder mapping:

  1-2. observe-only / verify+review — stage disabled (default).
       MERGE_READY jobs are leased and immediately released untouched.
  3.   dry-run executor — ``enabled=True, dry_run=True``. Proves the
       executor's read-only validation chain WITHOUT touching git:
       authorization presence (row exists, unexpired, unconsumed), exact
       binding checks (repository, head/base commits, candidate and
       expected merge trees), and durable-manifest load with
       supplied-manifest digest match. Explicitly NOT exercised: CI-check
       receipts, manifest promotion, and the git merge itself — those live
       in the executor's ``_execute_merge`` and only run on live merges.
       The one-shot authorization is created to prove the chain, then the
       job is stood back down to MERGE_READY (audited) so dry runs are
       repeatable and never strand the job in MERGE_AUTHORIZED.
  4.   bounded live merge — ``enabled=True, dry_run=False``,
       ``live_tiers={0,1}``. Tier 0/1 jobs merge under the standing-policy
       actor through the executor's atomic claim, merge lock, expected-tree
       check, and CAS rollback.
  5.   tier 2/3 — NEVER auto-merged. Fail closed here (and again inside
       ``authorize_merge``): the job stays MERGE_READY for explicit human
       authorization. ``live_tiers`` may not contain 2 or 3 — rejected at
       config time.

Env overrides (all optional; defaults keep the stage inert):

  PRISMATIC_RF_MERGE_AUTHORITY=1      enable the stage
  PRISMATIC_RF_MERGE_DRY_RUN=0        allow live merges (default 1 = dry-run)
  PRISMATIC_RF_MERGE_LIVE_TIERS=0,1   tiers allowed to merge live
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from prismatic.review_factory.models import ReviewJobState

logger = logging.getLogger(__name__)

_STANDING_POLICY_PREFIX = "standing-policy"

# Tiers that may EVER be auto-merged. Tier 2/3 are excluded by policy and
# rejected at config time as well as at decision time (defense in depth).
_AUTO_MERGEABLE_TIERS = frozenset({0, 1})

# Sentinel for the MergeStage novelty_detector kwarg: unset means "build
# the default detector from the shipped policy"; an explicit None disables
# the screen entirely.
_UNSET: Any = object()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class MergeStageConfig:
    """Knobs for the merge stage. Inert by default."""

    enabled: bool = False
    dry_run: bool = True
    live_tiers: frozenset[int] = field(default_factory=frozenset)
    repo_path: Optional[Path] = None
    lease_seconds: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "live_tiers", frozenset(int(t) for t in self.live_tiers)
        )
        forbidden = set(self.live_tiers) - _AUTO_MERGEABLE_TIERS
        if forbidden:
            raise ValueError(
                f"live_tiers may never include tier 2/3 (got {sorted(forbidden)}); "
                "tier 2/3 merges require explicit human authorization"
            )

    @classmethod
    def from_env(cls) -> "MergeStageConfig":
        raw_tiers = os.environ.get("PRISMATIC_RF_MERGE_LIVE_TIERS", "").strip()
        tiers: set[int] = set()
        for part in raw_tiers.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                tiers.add(int(part))
            except ValueError:
                logger.warning("ignoring invalid live tier %r", part)
        return cls(
            enabled=_env_flag("PRISMATIC_RF_MERGE_AUTHORITY", False),
            dry_run=_env_flag("PRISMATIC_RF_MERGE_DRY_RUN", True),
            live_tiers=frozenset(tiers),
        )


@dataclass
class MergeStageResult:
    """Outcome of one merge-stage decision."""

    job_id: str
    action: str  # skipped_disabled | refused_tier | refused_autonomy |
    #            # authorize_failed | dry_run_ok | merged | failed |
    #            # judgment_escalated
    merge_sha: str = ""
    authorization_id: str = ""
    error: str = ""


class MergeStage:
    """Decide and execute the merge step for a MERGE_READY job."""

    def __init__(
        self,
        queue: Any,
        config: Optional[MergeStageConfig] = None,
        linear_hooks: Any = None,
        novelty_detector: Any = _UNSET,
    ) -> None:
        self.queue = queue
        self.config = config or MergeStageConfig()
        self.linear_hooks = linear_hooks
        # The novelty screen is advisory-only (see _novelty_screen). Unset =
        # build the default detector lazily from the shipped novelty policy
        # (disabled -> inert); explicit None = screen off entirely.
        self._novelty_detector: Any = novelty_detector

    @property
    def novelty_detector(self) -> Any:
        """The novelty detector for the monitor-only screen, built lazily.

        Fail-closed: if the detector cannot be constructed the screen is
        skipped (never a quiet pass, never a halt).
        """
        if self._novelty_detector is _UNSET:
            try:
                from prismatic.review_factory.novelty import NoveltyDetector

                self._novelty_detector = NoveltyDetector()
            except Exception as exc:
                logger.warning("novelty detector unavailable: %s", exc)
                self._novelty_detector = None
        return self._novelty_detector

    # ── entry point ──────────────────────────────────────────────

    def process(self, job: Any) -> MergeStageResult:
        """Run the staged merge decision for one leased MERGE_READY job."""
        job_id = job.review_job_id
        tier = int(getattr(job, "risk_tier", 1) or 0)

        # Tier 2/3: NEVER auto-merge. Fail closed before anything else.
        if tier not in _AUTO_MERGEABLE_TIERS:
            self._audit_once(
                job_id,
                "auto_merge_refused_tier",
                {
                    "risk_tier": tier,
                    "note": (
                        "tier 2/3 jobs never auto-merge; "
                        "explicit human authorization required"
                    ),
                },
            )
            logger.info("auto-merge refused for tier-%s job %s", tier, job_id)
            return MergeStageResult(job_id=job_id, action="refused_tier")

        if not self.config.enabled:
            return MergeStageResult(job_id=job_id, action="skipped_disabled")

        if tier not in self.config.live_tiers:
            self._audit_once(
                job_id,
                "auto_merge_tier_not_enabled",
                {
                    "risk_tier": tier,
                    "live_tiers": sorted(self.config.live_tiers),
                    "note": "tier not enabled for live auto-merge",
                },
            )
            return MergeStageResult(job_id=job_id, action="refused_tier")

        if not self.config.dry_run and not self.config.repo_path:
            self._audit(
                job_id,
                "merge_stage_failed",
                {"error": "live merge requested without repo_path; refusing"},
            )
            return MergeStageResult(
                job_id=job_id,
                action="failed",
                error="live merge requested without repo_path; refusing",
            )

        actor = f"{_STANDING_POLICY_PREFIX}: tier-{tier}"

        # Monitor-only novelty screen: trips are logged to the novelty
        # audit trail and the merge-stage audit; the verdict is advisory
        # only — the pipeline is NEVER halted and nothing is quarantined
        # by this path. Inert while the novelty policy is disabled.
        self._novelty_screen(job, job_id)

        # L2 judgment layer (Jev validation-loop plan section 8): Jev judges
        # only what the deterministic floor cannot decide. CLEAN-only (jobs
        # reach MERGE_READY only on a deterministic CLEAN; evaluate() asserts
        # it), behind the default-off CallSiteGate("review_judgment") and the
        # tiering rule (tier-0 trivial diffs skip). Fail-open: any judgment
        # failure leaves the deterministic verdict standing. PAUSE on CLEAN
        # escalates via apply_jev_advice and the merge is never authorized.
        if self._judgment_holds_for_human(job, job_id, tier):
            return MergeStageResult(job_id=job_id, action="judgment_escalated")

        # Earned-autonomy tier consult (Phase 3 wiring): the phase-2 policy
        # engine decides whether this tier/change-class may auto-merge.
        # Fail-closed: any consult failure refuses. The consult runs in
        # dry-run mode too — dry_run_ok means "would have merged under
        # autonomy".
        autonomy_allowed, autonomy_reason = self._autonomy_consult(job)
        if not autonomy_allowed:
            self._audit(
                job_id,
                "auto_merge_refused_autonomy",
                {
                    "risk_tier": tier,
                    "reason": autonomy_reason,
                    "note": (
                        "earned-autonomy policy engine refused the merge; "
                        "no authorization was created"
                    ),
                },
            )
            logger.info(
                "auto-merge refused by autonomy consult for job %s: %s",
                job_id,
                autonomy_reason,
            )
            return MergeStageResult(
                job_id=job_id,
                action="refused_autonomy",
                error=autonomy_reason,
            )

        auth_id = self.queue.authorize_merge(job_id, actor=actor)
        if not auth_id:
            # authorize_merge re-validates the standing-policy actor and the
            # tier<=1 rule; a None here is a loud refusal, not a quiet skip.
            self._audit(
                job_id,
                "merge_authorize_failed",
                {"actor": actor, "risk_tier": tier},
            )
            logger.error("merge authorization failed for job %s", job_id)
            return MergeStageResult(
                job_id=job_id,
                action="authorize_failed",
                error="authorize_merge refused",
            )

        from prismatic.review_factory.merge_executor import MergeExecutor

        executor = MergeExecutor(
            queue=self.queue,
            dry_run=self.config.dry_run,
            repo_path=self.config.repo_path,
        )
        try:
            result = executor.execute(job_id)
        except Exception as exc:
            logger.error("merge executor raised for job %s: %s", job_id, exc)
            self._audit(
                job_id,
                "merge_stage_failed",
                {"authorization_id": auth_id, "error": str(exc)[:300]},
            )
            return MergeStageResult(
                job_id=job_id,
                action="failed",
                authorization_id=auth_id,
                error=str(exc)[:300],
            )

        if self.config.dry_run:
            # Dry runs never consume the authorization and never mutate git.
            # Stand the job back down to MERGE_READY so the next dry run or
            # live run starts clean -- without this the job would strand in
            # MERGE_AUTHORIZED (no other graph edge leads back).
            stood_down = self._stand_down_after_dry_run(job_id, auth_id)
            if not result.success:
                self._audit(
                    job_id,
                    "merge_stage_failed",
                    {
                        "authorization_id": auth_id,
                        "error": result.error[:300],
                        "dry_run": True,
                        "stood_down_to_merge_ready": stood_down,
                    },
                )
                logger.error(
                    "dry-run merge failed for job %s: %s",
                    job_id,
                    result.error,
                )
                return MergeStageResult(
                    job_id=job_id,
                    action="failed",
                    authorization_id=auth_id,
                    error=result.error[:300],
                )
            self._audit(
                job_id,
                "merge_dry_run_ok",
                {
                    "authorization_id": auth_id,
                    "stood_down_to_merge_ready": stood_down,
                    "note": (
                        "dry-run passed the executor's read-only validation "
                        "chain (authorization presence/bindings, durable "
                        "manifest digest match); CI-check receipts, "
                        "promotion, and the git merge are NOT exercised "
                        "by dry runs; no git mutation performed"
                    ),
                },
            )
            return MergeStageResult(
                job_id=job_id,
                action="dry_run_ok",
                authorization_id=auth_id,
                merge_sha=result.merge_sha,
            )

        if not result.success:
            self._audit(
                job_id,
                "merge_stage_failed",
                {
                    "authorization_id": auth_id,
                    "error": result.error[:300],
                },
            )
            logger.error("merge failed for job %s: %s", job_id, result.error)
            return MergeStageResult(
                job_id=job_id,
                action="failed",
                authorization_id=auth_id,
                error=result.error[:300],
            )

        self._audit(
            job_id,
            "auto_merge_completed",
            {
                "authorization_id": auth_id,
                "merge_sha": result.merge_sha,
                "actor": actor,
                "risk_tier": tier,
            },
        )
        logger.info("auto-merged job %s as %s", job_id, result.merge_sha)
        if self.linear_hooks is not None:
            try:
                fresh = self.queue.db.get_review_job(job_id)
                self.linear_hooks.notify_merged(
                    fresh or job, merge_sha=result.merge_sha
                )
            except Exception as exc:
                logger.warning("linear merged hook failed for %s: %s", job_id, exc)
        return MergeStageResult(
            job_id=job_id,
            action="merged",
            authorization_id=auth_id,
            merge_sha=result.merge_sha,
        )

    # ── monitor-only novelty screen ─────────────────────────────────

    @staticmethod
    def _novelty_input_for_job(job: Any, job_id: str) -> Any:
        """Build the detector's pure-data input for one merge candidate.

        Every novelty input is data the caller supplies, never a judgment
        the stage makes. Unavailable inputs stay at their defaults
        (None/empty = unavailable, never a trip): the precedent similarity
        search is a later chunk and Jev does not exist yet. Optional
        ``novelty_*`` attributes on the job let tests and future callers
        supply real evidence.
        """
        from prismatic.review_factory.novelty import NoveltyInput

        def _get(name: str, default: Any) -> Any:
            return getattr(job, name, default)

        def _seq(name: str) -> tuple:
            value = _get(name, ())
            if value is None:
                return ()
            if isinstance(value, (str, bytes)):
                return (value,)
            try:
                return tuple(value)
            except TypeError:
                return ()

        def _fset(name: str) -> frozenset:
            try:
                return frozenset(_seq(name))
            except TypeError:
                return frozenset()

        tier = _get("risk_tier", 0)
        try:
            tier = int(tier or 0)
        except (TypeError, ValueError):
            tier = 0
        change_shape = _get("novelty_change_shape", None)
        if change_shape is None:
            change_shape = {"risk_tier": tier}
        return NoveltyInput(
            candidate_id=job_id,
            jev_confidences=_get("novelty_jev_confidences", None),
            precedent_matches=_get("novelty_precedent_matches", None),
            change_shape=change_shape,
            error_classes=_seq("novelty_error_classes"),
            known_error_classes=_fset("novelty_known_error_classes"),
            event_types=_seq("novelty_event_types"),
            seen_event_types=_fset("novelty_seen_event_types"),
            input_schema_hash=_get("novelty_input_schema_hash", None),
            expected_schema_hash=_get("novelty_expected_schema_hash", None),
        )

    def _novelty_screen(self, job: Any, job_id: str) -> None:
        """Run the monitor-only novelty screen for a merge candidate.

        The shipped novelty policy is disabled, so this is inert until the
        rollout ladder arms the detector (the Phase 0 -> 1 exit criteria
        require it). When armed in monitor-only mode, trips are logged to
        the novelty audit trail AND the merge-stage audit; the verdict is
        advisory only — the pipeline is NEVER halted and nothing is
        quarantined by this path. Enforcing mode is a separate
        phase-advancement step consumed by the quarantine-routing path,
        which is deliberately NOT wired here.
        """
        detector = self.novelty_detector
        if detector is None:
            return
        try:
            result = detector.evaluate(self._novelty_input_for_job(job, job_id))
        except Exception as exc:
            logger.warning("novelty screen failed for %s: %s", job_id, exc)
            return
        self._audit(
            job_id,
            "merge_novelty_screen",
            {
                "novelty_state": result.state,
                "policy_version": result.policy_version,
                "mode": result.mode,
                "tripped_inputs": [t.input for t in result.trips],
                "quarantined": result.quarantined,
                "pipeline_halted": result.pipeline_halted,
                "note": (
                    "advisory only: the monitor-only screen never halts "
                    "the pipeline or quarantines"
                ),
            },
        )

    # ── L2 judgment screen ─────────────────────────────────────────

    def _judgment_holds_for_human(self, job: Any, job_id: str, tier: int) -> bool:
        """Run the L2 Jev judgment screen on a merge-ready job.

        Returns True only when Jev escalated CLEAN -> human review is
        required (the caller must not authorize the merge). Everything else
        -- gate closed, tier-0 skip, null judgment, CLEAR, any failure --
        returns False: fail-open toward the deterministic result, since Jev
        can only escalate.

        Guarded like the #547 wiring: a judgment failure must never break
        the hot path.

        Note: on ESCALATE the job stays MERGE_READY (the human-wait state)
        with merge authorization withheld -- that IS the pause-for-human the
        plan's L3 describes. MERGE_READY has no ->QUARANTINED edge in the
        state graph and quarantine_routing.py is explicitly shadow-only, so
        no state transition is attempted here.
        """
        try:
            from prismatic.jev.gates import CallSiteGate, apply_jev_advice
            from prismatic.review_factory.judge import (
                JUDGMENT_CALL_SITE,
                artifact_from_review_job,
                build_judge,
                judgment_advice,
                maybe_evaluate,
            )
        except Exception as exc:
            logger.warning("judgment layer unavailable for %s: %s", job_id, exc)
            return False
        try:
            if not CallSiteGate(JUDGMENT_CALL_SITE).allow():
                return False
            artifact = artifact_from_review_job(job)
            judgment = maybe_evaluate(
                build_judge(),
                artifact,
                "CLEAN",  # MERGE_READY is only reachable on deterministic CLEAN
                tier=tier,
                novelty_flagged=bool(artifact.get("novelty_flagged", False)),
                first_time_author=bool(artifact.get("first_time_author", False)),
            )
            if judgment.skipped is not None:
                self._audit(
                    job_id,
                    "merge_stage_judgment_skipped",
                    {
                        "judgment_skipped": judgment.skipped,
                        "risk_tier": tier,
                        "explicit_non_claims": list(judgment.explicit_non_claims),
                    },
                )
                return False
            self._audit(job_id, "merge_stage_judgment", judgment.to_audit_dict())
            # The judge speaks {CLEAR, PAUSE}; the enforcer speaks
            # {CLEAN, REPAIR, REJECT, ESCALATE} -- judgment_advice bridges
            # the two vocabularies (PAUSE -> ESCALATE advice).
            final = apply_jev_advice("CLEAN", judgment_advice(judgment))
            if final == "ESCALATE":
                self._audit(
                    job_id,
                    "merge_stage_judgment_escalated",
                    {
                        "decision": judgment.decision,
                        "confidence": judgment.confidence,
                        "trace_id": judgment.trace_id,
                        "note": (
                            "Jev escalated a deterministic-CLEAN job; merge "
                            "authorization withheld for human review"
                        ),
                    },
                )
                logger.info(
                    "judgment escalated job %s (trace %s); merge withheld",
                    job_id,
                    judgment.trace_id,
                )
                return True
            return False
        except Exception as exc:
            logger.warning(
                "judgment screen failed for %s (deterministic stands): %s",
                job_id,
                exc,
            )
            return False

    # ── earned-autonomy consult (lazy phase-2 boundary) ────────────

    def _autonomy_consult(self, job: Any, judgment: Any = None) -> tuple[bool, str]:
        """Consult the earned-autonomy tier engine for one merge candidate.

        Returns (allowed, reason). Fail-closed: the merge is refused
        whenever the phase-2 module is absent, the trust tier cannot be
        read, or any part of the consult raises. While phases 1/2 are
        unmerged every consult refuses with "autonomy_module_absent" —
        the stage stays green and inert on the phase-1/2 boundary.
        """
        try:
            from prismatic.review_factory import autonomy
        except Exception:
            logger.debug("earned-autonomy autonomy module absent; merge withheld")
            return False, "autonomy_module_absent"
        # The current earned tier comes from the phase-1 trust ledger (lazy
        # boundary). On ANY failure treat the tier as 0: tier 0 never
        # auto-merges, so the consult fails closed through can_auto_merge's
        # own rules.
        try:
            from prismatic.review_factory import trust

            ledger = trust.TrustLedger()
            tier = int(ledger.tier_status().get("current_tier", 0))
        except Exception as exc:
            logger.debug("trust tier lookup failed (%s); treating tier as 0", exc)
            tier = 0
        try:
            from prismatic.review_factory.merge_executor import classify_change_class

            brake = autonomy.brake_status()
            brake_engaged = bool(brake.get("engaged", True))
            decision = autonomy.can_auto_merge(
                tier=tier,
                change_class=classify_change_class(job),
                deterministic_verdict=getattr(job, "deterministic_verdict", "UNKNOWN"),
                judgment=judgment,
                brake_engaged=brake_engaged,
            )
            return bool(decision.allowed), str(decision.reason)
        except Exception as exc:
            logger.warning("earned-autonomy consult failed: %s", exc)
            return False, "autonomy_consult_error"

    # ── audit helpers ────────────────────────────────────────────

    def _stand_down_after_dry_run(self, job_id: str, auth_id: str) -> bool:
        """Return a dry-run job from MERGE_AUTHORIZED to MERGE_READY.

        The dry-run authorization proved the validation chain but must never
        be usable for a real merge afterwards: it is marked consumed (the
        one-shot guard would otherwise block every later run), and the job
        is moved back to MERGE_READY (the only graph edge back -- without
        it the job would strand in MERGE_AUTHORIZED). The audit trail
        records the full sequence.
        """
        try:
            moved = self.queue.db.update_review_job_state(
                job_id, ReviewJobState.MERGE_READY
            )
            if not moved:
                logger.warning(
                    "dry-run stand-down did not move job %s (auth %s)",
                    job_id,
                    auth_id,
                )
                return False
            try:
                self.queue.db.consume_authorization(auth_id)
            except Exception as exc:
                logger.warning("dry-run auth consume failed for %s: %s", auth_id, exc)
            return True
        except Exception as exc:
            logger.warning("dry-run stand-down failed for job %s: %s", job_id, exc)
            return False

    def _audit(self, job_id: str, action: str, details: dict) -> None:
        try:
            self.queue.db.insert_audit_entry(
                actor="review-factory:merge-stage",
                action=action,
                review_job_id=job_id,
                details=details,
            )
        except Exception as exc:
            logger.warning("merge stage audit write failed: %s", exc)

    def _audit_once(self, job_id: str, action: str, details: dict) -> None:
        try:
            if self.queue.db.find_audit_entry(job_id, action) is not None:
                return
        except Exception:
            pass
        self._audit(job_id, action, details)
