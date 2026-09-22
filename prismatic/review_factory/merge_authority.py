"""Auto-merge authority gate (RF-4 completion, Phase-1+ machinery).

HARD-DISABLED until Michael explicitly advances the rollout ladder.
``request_merge()`` refuses every request while the policy's ``enabled``
is false: no gate beyond the master switch runs, the swarmlock merge mutex
is never acquired, and the merge executor is never called.

When a future policy version enables it (his word), the authority is the
merge path's pause button:

    master switch -> deterministic gates -> tier check -> rate limit
      -> Jev second opinion (pause only, fail-closed, never overrides gates)
      -> swarmlock merge mutex -> executor -> audit signal

Every decision — allowed or refused — emits one audit signal as a JSONL row.
No signal, no action. The decision path is a pure function of its inputs:
callers pass CI/review state in; this module makes no network calls to GitHub.

The existing ``MergeExecutor`` keeps its own authorization checks; this gate
sits in front of it, never instead of it.

Jev is not built yet: every signal carries ``jev_score: null`` and the
authority never consults it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _HAS_YAML = False

SPEC_DIR = Path(__file__).resolve().parent / "spec"
DEFAULT_POLICY_FILE = SPEC_DIR / "auto_merge_policy_v1.yaml"
DEFAULT_AUDIT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/auto-merge-decisions.jsonl")
)

DECISION_ALLOWED = "allowed"
DECISION_REFUSED = "refused"


class AutoPolicyConfigError(Exception):
    """Raised when the auto-merge policy config cannot be loaded.

    Fail-closed: the authority refuses every request rather than guessing
    from defaults.
    """


# ─────────────────────────────────────────────────────────────────────
# Config models
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RateLimits:
    """Rolling-window caps on authorized merges."""

    max_per_hour: int = 1
    max_per_day: int = 3

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RateLimits":
        return cls(
            max_per_hour=int(data.get("max_per_hour", 1)),
            max_per_day=int(data.get("max_per_day", 3)),
        )


@dataclass(frozen=True)
class LockConfig:
    """swarmlock merge-mutex configuration."""

    resource: str = "prismatic/merge-mutex"
    holder_prefix: str = "merge-authority"
    ttl_seconds: float = 300.0
    backend: str = "file"
    lock_dir: str = "~/.prismatic/locks"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LockConfig":
        return cls(
            resource=str(data.get("resource", "prismatic/merge-mutex")),
            holder_prefix=str(data.get("holder_prefix", "merge-authority")),
            ttl_seconds=float(data.get("ttl_seconds", 300)),
            backend=str(data.get("backend", "file")),
            lock_dir=str(data.get("lock_dir", "~/.prismatic/locks")),
        )


@dataclass(frozen=True)
class AutoMergePolicy:
    """Versioned deterministic auto-merge policy.

    ``enabled: false`` (the default when the key is absent) means every
    request is refused. The policy must say ``enabled: true`` explicitly —
    there is no way to be accidentally on.
    """

    version: str
    enabled: bool
    gates: tuple[str, ...]
    verdicts_mergeable: frozenset[str]
    max_tier: int
    rate_limits: RateLimits
    lock: LockConfig
    jev_enabled: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoMergePolicy":
        if not isinstance(data, dict):
            raise AutoPolicyConfigError("policy root must be a mapping")
        return cls(
            version=str(data.get("version", "unknown")),
            enabled=bool(data.get("enabled", False)),
            gates=tuple(str(g.get("id")) for g in data.get("gates", [])),
            verdicts_mergeable=frozenset(
                str(v) for v in data.get("verdicts_mergeable", [])
            ),
            max_tier=int(data.get("max_tier", 0)),
            rate_limits=RateLimits.from_dict(data.get("rate_limits", {}) or {}),
            lock=LockConfig.from_dict(data.get("lock", {}) or {}),
            jev_enabled=bool((data.get("jev", {}) or {}).get("enabled", False)),
        )


def load_auto_policy(path: Optional[Path | str] = None) -> AutoMergePolicy:
    """Load the versioned auto-merge policy, fail-closed.

    A missing file yields a disabled policy (refuse everything), not an
    error — but a malformed file raises ``AutoPolicyConfigError`` so the
    authority never runs on a half-read config.
    """
    path = Path(path) if path is not None else DEFAULT_POLICY_FILE
    if not path.exists():
        return AutoMergePolicy.from_dict({"version": "missing", "enabled": False})
    if not _HAS_YAML:
        raise AutoPolicyConfigError("PyYAML is required to load the auto-merge policy")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise AutoPolicyConfigError(f"cannot parse policy file {path}: {exc}") from exc
    return AutoMergePolicy.from_dict(data)


# ─────────────────────────────────────────────────────────────────────
# Gate evaluation (pure, no network)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateResult:
    """One deterministic gate's pass/fail outcome."""

    gate_id: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class MergeInput:
    """The PR/review state the authority evaluates. Callers pass this in."""

    job_id: str
    repository: str
    head_sha: str
    tier: int
    ci_green_self_hosted: bool = False
    ruff_clean: bool = False
    verdict: str = "REJECT"
    no_merge_conflicts: bool = False
    branch_protection_satisfied: bool = False


def evaluate_gates(policy: AutoMergePolicy, pr: MergeInput) -> list[GateResult]:
    """Evaluate the policy's deterministic gates against the PR state.

    Pure function: no network, no side effects. Unknown gate ids fail
    closed — a gate the evaluator doesn't understand cannot pass.
    """
    results: list[GateResult] = []
    for gate_id in policy.gates:
        if gate_id == "ci_green_self_hosted":
            results.append(
                GateResult(gate_id, pr.ci_green_self_hosted, "CI on self-hosted runner")
            )
        elif gate_id == "ruff_clean":
            results.append(GateResult(gate_id, pr.ruff_clean, "ruff clean"))
        elif gate_id == "verdict_not_reject":
            ok = pr.verdict in policy.verdicts_mergeable
            results.append(GateResult(gate_id, ok, f"verdict={pr.verdict}"))
        elif gate_id == "no_merge_conflicts":
            results.append(
                GateResult(gate_id, pr.no_merge_conflicts, "mergeable, no conflicts")
            )
        elif gate_id == "branch_protection_satisfied":
            results.append(
                GateResult(gate_id, pr.branch_protection_satisfied, "protection checks")
            )
        else:
            # Unknown gate: fail closed. The policy must not invent passes.
            results.append(GateResult(gate_id, False, "unknown gate id — fail closed"))
    return results


# ─────────────────────────────────────────────────────────────────────
# Rate limiter (rolling windows over the decision log)
# ─────────────────────────────────────────────────────────────────────


class RateLimiter:
    """Rolling-window caps counted from the authority's own decision log.

    Only ``allowed`` decisions consume budget. The log is the source of
    truth, so a restarted process inherits the real recent history.
    """

    def __init__(self, limits: RateLimits, log_path: Path | str):
        self.limits = limits
        self.log_path = Path(log_path)

    def _allowed_timestamps(self, now: float) -> list[float]:
        stamps: list[float] = []
        if not self.log_path.exists():
            return stamps
        try:
            with open(self.log_path, encoding="utf-8") as fh:
                rows = fh.readlines()
        except OSError:
            return stamps
        for row in rows:
            try:
                data = json.loads(row)
            except json.JSONDecodeError:
                continue  # corrupt line: skip, never crash the gate
            if data.get("decision") != DECISION_ALLOWED:
                continue
            ts = data.get("ts")
            if isinstance(ts, (int, float)) and ts <= now:
                stamps.append(float(ts))
        return stamps

    def would_exceed(self, now: Optional[float] = None) -> Optional[str]:
        """Return a refusal reason if a new allow would exceed a cap, else None."""
        now = time.time() if now is None else now
        stamps = self._allowed_timestamps(now)
        hour = sum(1 for ts in stamps if now - ts < 3600)
        day = sum(1 for ts in stamps if now - ts < 86400)
        if hour >= self.limits.max_per_hour:
            return f"rate_limit: {hour} allowed in the last hour (cap {self.limits.max_per_hour})"
        if day >= self.limits.max_per_day:
            return f"rate_limit: {day} allowed in the last day (cap {self.limits.max_per_day})"
        return None


# ─────────────────────────────────────────────────────────────────────
# Decision + audit
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MergeDecision:
    """The authority's answer for one merge request."""

    decision: str  # "allowed" | "refused"
    reason: str
    job_id: str
    policy_version: str
    gates: tuple[GateResult, ...] = ()
    tier: int = -1
    jev_score: Optional[float] = None  # always None until Jev is built
    merge_sha: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == DECISION_ALLOWED


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# The authority
# ─────────────────────────────────────────────────────────────────────


class MergeAuthority:
    """Gate in front of the RF-4 merge executor.

    Construct with the policy path; ``request_merge()`` refuses everything
    while the policy is disabled. The merge executor is injected (not built
    here) so the authority can never silently invent a caller — no executor
    configured means refuse.
    """

    def __init__(
        self,
        policy_path: Optional[Path | str] = None,
        *,
        lock_client: Any = None,
        executor: Any = None,
        audit_log: Optional[Path | str] = None,
        now_fn: Any = None,
    ):
        try:
            self.policy = load_auto_policy(policy_path)
            self._policy_error: Optional[str] = None
        except AutoPolicyConfigError as exc:
            # Fail-closed: a malformed policy refuses every request.
            self.policy = AutoMergePolicy.from_dict(
                {"version": "invalid", "enabled": False}
            )
            self._policy_error = str(exc)
        self.lock_client = lock_client
        self.executor = executor
        self.audit_log = Path(audit_log) if audit_log is not None else DEFAULT_AUDIT_LOG
        self._now = now_fn or time.time
        self._limiter = RateLimiter(self.policy.rate_limits, self.audit_log)

    # -- swarmlock merge mutex --------------------------------------

    def _default_lock_client(self) -> Any:
        """Build the real swarmlock client. Fail-closed when unavailable."""
        try:
            from swarmlock import AcquireRequest, SyncSwarmlock
        except ImportError as exc:
            raise AutoPolicyConfigError(
                f"swarmlock is required for the merge mutex: {exc}"
            ) from exc
        cfg = self.policy.lock
        kwargs: dict[str, Any] = {}
        if cfg.backend == "file":
            lock_dir = os.path.expanduser(cfg.lock_dir)
            os.makedirs(lock_dir, exist_ok=True)
            kwargs["registry_file"] = os.path.join(lock_dir, "swarmlock_registry.json")
        return SyncSwarmlock(backend=cfg.backend, **kwargs), AcquireRequest

    def _acquire_merge_mutex(self) -> Any:
        """Acquire the merge mutex. Raises on contention or misconfiguration.

        One merge at a time across all authority instances. The lease is
        non-reentrant and short-lived; contention fails the request rather
        than queuing behind an unknown holder.
        """
        holder = f"{self.policy.lock.holder_prefix}:{os.getpid()}"
        if self.lock_client is not None:
            # Injected client (tests, or a wired deployment): the request
            # shape is the swarmlock one; a fake must accept (resource, holder,
            # ttl_seconds) and return a context manager.
            return self.lock_client.acquire(
                resource=self.policy.lock.resource,
                holder=holder,
                ttl_seconds=self.policy.lock.ttl_seconds,
            )
        client, acquire_request_cls = self._default_lock_client()
        request = acquire_request_cls(
            resource=self.policy.lock.resource,
            holder=holder,
            ttl_seconds=self.policy.lock.ttl_seconds,
            reentrant=False,
        )
        return client.lease(request, heartbeat=False)

    # -- audit -------------------------------------------------------

    def _emit_audit(self, decision: MergeDecision, pr: MergeInput) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "merge_authority",
            "policy_version": decision.policy_version,
            "job_id": decision.job_id,
            "repository": pr.repository,
            "head_sha": pr.head_sha,
            "tier": decision.tier,
            "decision": decision.decision,
            "reason": decision.reason,
            "gates": [
                {"id": g.gate_id, "passed": g.passed, "detail": g.detail}
                for g in decision.gates
            ],
            "jev_score": decision.jev_score,
            "merge_sha": decision.merge_sha,
        }
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            # Audit write failure must not flip a refusal into an allow —
            # and must not crash a refusal path either. The decision stands.
            pass

    def _refuse(
        self, pr: MergeInput, reason: str, gates: tuple[GateResult, ...] = ()
    ) -> MergeDecision:
        decision = MergeDecision(
            decision=DECISION_REFUSED,
            reason=reason,
            job_id=pr.job_id,
            policy_version=self.policy.version,
            gates=gates,
            tier=pr.tier,
            jev_score=None,
        )
        self._emit_audit(decision, pr)
        return decision

    # -- the gate ----------------------------------------------------

    def request_merge(self, pr: MergeInput) -> MergeDecision:
        """Evaluate one auto-merge request. Refuses while disabled."""
        # 1. Policy health. A malformed policy refuses everything.
        if self._policy_error is not None:
            return self._refuse(pr, f"policy_config_error: {self._policy_error}")

        # 2. MASTER SWITCH. Disabled = refuse before anything else runs.
        if not self.policy.enabled:
            return self._refuse(pr, "auto_merge_disabled")

        # 3. Deterministic gates.
        gates = tuple(evaluate_gates(self.policy, pr))
        failed = [g for g in gates if not g.passed]
        if failed:
            return self._refuse(
                pr,
                "gate_failed:" + ",".join(g.gate_id for g in failed),
                gates,
            )

        # 4. Tier check.
        if pr.tier > self.policy.max_tier:
            return self._refuse(
                pr,
                f"tier_refused: tier {pr.tier} above max_tier {self.policy.max_tier}",
                gates,
            )

        # 5. Rate limits.
        over = self._limiter.would_exceed(self._now())
        if over is not None:
            return self._refuse(pr, over, gates)

        # 6. Jev second opinion: PAUSE BUTTON ONLY. When Jev is ever enabled,
        #    it may flip an allowed merge to refused — never the reverse.
        #    (Jev is not built; jev_enabled is false and this stays dead code
        #    with the fail-closed structure in place.)
        if self.policy.jev_enabled:
            return self._refuse(pr, "jev_enabled_but_not_built", gates)

        # 7. Executor. Not wired here by design — the authority never invents
        #    a caller. Wiring the real MergeExecutor is a phase-advancement
        #    step on Michael's word. Checked before the mutex so a request
        #    that cannot merge never touches the global serialization point.
        if self.executor is None:
            return self._refuse(pr, "no_executor_wired", gates)

        # 8. Merge mutex: one merge at a time. Contention fails the request.
        try:
            mutex = self._acquire_merge_mutex()
        except Exception as exc:
            return self._refuse(pr, f"merge_mutex_unavailable: {exc}", gates)

        try:
            with mutex:
                result = self.executor.execute(job_id=pr.job_id)
        except Exception as exc:
            return self._refuse(pr, f"executor_error: {exc}", gates)

        merge_sha = getattr(result, "merge_sha", "") or ""
        if not getattr(result, "success", False):
            return self._refuse(
                pr, f"executor_refused: {getattr(result, 'error', 'unknown')}", gates
            )

        decision = MergeDecision(
            decision=DECISION_ALLOWED,
            reason="all gates passed; executor merged",
            job_id=pr.job_id,
            policy_version=self.policy.version,
            gates=gates,
            tier=pr.tier,
            jev_score=None,
            merge_sha=merge_sha,
        )
        self._emit_audit(decision, pr)
        return decision
