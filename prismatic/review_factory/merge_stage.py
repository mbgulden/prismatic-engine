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
    action: str  # skipped_disabled | refused_tier | authorize_failed |
    #            # dry_run_ok | merged | failed
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
    ) -> None:
        self.queue = queue
        self.config = config or MergeStageConfig()
        self.linear_hooks = linear_hooks

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
