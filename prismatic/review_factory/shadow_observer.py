"""Phase 0 shadow-mode observer for code-delivery autonomy.

The observer evaluates the versioned shadow merge policy against a PR's CI
results and review verdict, classifies the change into a risk tier with the
existing PolicyEngine, and emits one audit signal per PR with the system's
merge/skip call. It changes nothing: no webhooks, no polling, no merge
executor wiring. Default-off — ``observe()`` is a no-op unless the policy
sets ``enabled: true`` (enabling needs Michael's word).

The decision path is a pure function of its inputs: callers pass CI results
in; this module makes no network calls to GitHub.

Jev is not built yet: every signal carries ``jev_score: null`` and the call
never consults the risk bands. The bands config exists so thresholds are
decided before any model output can influence a call.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _HAS_YAML = False

from prismatic.review_factory.models import RiskTier
from prismatic.review_factory.policy import PolicyEngine

SPEC_DIR = Path(__file__).resolve().parent / "spec"
DEFAULT_POLICY_FILE = SPEC_DIR / "shadow_merge_policy_v2.yaml"
DEFAULT_SHADOW_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/shadow-decisions.jsonl")
)

CALL_MERGE = "merge"
CALL_SKIP = "skip"


class ShadowConfigError(Exception):
    """Raised when the shadow policy/bands config cannot be loaded.

    Fail-closed: the observer refuses to evaluate rather than guessing
    from defaults.
    """


# ─────────────────────────────────────────────────────────────────────
# Config models
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskBands:
    """Versioned risk-band thresholds for Jev-assisted calls (not live yet)."""

    version: str
    auto_below: float
    human_above: float
    on_jev_error: str = "fail_closed"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskBands:
        return cls(
            version=str(data.get("version", "unknown")),
            auto_below=float(data["auto_below"]),
            human_above=float(data["human_above"]),
            on_jev_error=str(data.get("on_jev_error", "fail_closed")),
        )


@dataclass(frozen=True)
class ShadowPolicy:
    """Versioned deterministic merge policy for the shadow observer."""

    version: str
    enabled: bool
    gates: tuple[str, ...]
    verdicts_mergeable: tuple[str, ...]
    tier_policy_file: str
    shadow_merge_max_tier: int
    jev_enabled: bool
    bands_file: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowPolicy:
        jev = data.get("jev") or {}
        return cls(
            version=str(data.get("version", "unknown")),
            enabled=bool(data.get("enabled", False)),
            gates=tuple(g.get("id") for g in data.get("gates", [])),
            verdicts_mergeable=tuple(data.get("verdicts_mergeable", ["CLEAN"])),
            tier_policy_file=str(data.get("tier_policy_file", "")),
            shadow_merge_max_tier=int(data.get("shadow_merge_max_tier", 1)),
            jev_enabled=bool(jev.get("enabled", False)),
            bands_file=str(jev.get("bands_file", "")),
        )


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ShadowConfigError(f"shadow config not found: {path}")
    if not _HAS_YAML:
        raise ShadowConfigError(
            "PyYAML is required to load shadow policy config "
            f"({path}); refusing to evaluate without it"
        )
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ShadowConfigError(f"shadow config is not a mapping: {path}")
    return data


def load_policy(path: str | Path = DEFAULT_POLICY_FILE) -> ShadowPolicy:
    """Load the versioned shadow merge policy. Fail-closed on any problem."""
    return ShadowPolicy.from_dict(_load_yaml_file(Path(path)))


def load_bands(path: str | Path) -> RiskBands:
    """Load the versioned risk bands. Fail-closed on any problem."""
    return RiskBands.from_dict(_load_yaml_file(Path(path)))


# ─────────────────────────────────────────────────────────────────────
# Decision model
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShadowInput:
    """Everything the observer needs. Passed in by the caller — the
    observer never fetches this itself."""

    pr_number: int
    pr_title: str
    head_sha: str
    base_sha: str
    changed_files: tuple[str, ...]
    ci_green_self_hosted: bool
    ruff_clean: bool
    review_verdict: str  # CLEAN | REPAIR | REJECT | ...
    merge_conflicts: bool
    branch_protection_satisfied: bool


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ShadowDecision:
    """The system's call for one PR, with full provenance."""

    pr_number: int
    pr_title: str
    head_sha: str
    gate_results: tuple[GateResult, ...]
    tier: int
    tier_name: str
    jev_score: None  # Jev not built yet; field reserved for the schema
    bands: dict[str, Any]
    call: str  # "merge" | "skip"
    reasons: tuple[str, ...]
    policy_version: str

    def to_signal(self) -> dict[str, Any]:
        """Render as an agent_signal_stream audit signal (same schema as
        the prismatic-audit skill's bin/emit)."""
        now = datetime.now(timezone.utc)
        failed = [g.gate for g in self.gate_results if not g.passed]
        summary = f"shadow call for PR #{self.pr_number}: {self.call.upper()}" + (
            f" ({', '.join(failed)} failed)" if failed else " (all gates passed)"
        )
        return {
            "agent": "prismatic-shadow-observer",
            "event_type": "decision",
            "id": f"shadow-{int(time.time() * 1000)}-{random.randrange(16**8):08x}",
            "issue_id": "",
            "log_path": "",
            "message": summary[:500],
            "metadata": {
                "pr_number": self.pr_number,
                "pr_title": self.pr_title,
                "head_sha": self.head_sha,
                "gate_results": [
                    {"gate": g.gate, "passed": g.passed, "detail": g.detail}
                    for g in self.gate_results
                ],
                "tier": self.tier,
                "tier_name": self.tier_name,
                "jev_score": self.jev_score,
                "bands": self.bands,
                "call": self.call,
                "reasons": list(self.reasons),
                "policy_version": self.policy_version,
                "shadow_mode": True,
            },
            "run_id": "",
            "severity": "info",
            "source": "prismatic-engine",
            "status": "completed",
            "timestamp": now.isoformat(),
            "transcript": "",
        }


# ─────────────────────────────────────────────────────────────────────
# Pure decision function
# ─────────────────────────────────────────────────────────────────────


def _run_gate(gate_id: str, inp: ShadowInput, policy: ShadowPolicy) -> GateResult:
    if gate_id == "ci_green_self_hosted":
        return GateResult(
            gate_id, inp.ci_green_self_hosted, "self-hosted runner CI green"
        )
    if gate_id == "ruff_clean":
        return GateResult(gate_id, inp.ruff_clean, "ruff check + format clean")
    if gate_id == "verdict_not_reject":
        ok = inp.review_verdict in policy.verdicts_mergeable
        return GateResult(
            gate_id,
            ok,
            f"deterministic verdict {inp.review_verdict!r} "
            f"{'mergeable' if ok else 'not mergeable'}",
        )
    if gate_id == "no_merge_conflicts":
        ok = not inp.merge_conflicts
        return GateResult(
            gate_id, ok, "no merge conflicts" if ok else "has merge conflicts"
        )
    if gate_id == "branch_protection_satisfied":
        return GateResult(
            gate_id,
            inp.branch_protection_satisfied,
            "branch protection required checks satisfied",
        )
    return GateResult(gate_id, False, f"unknown gate id: {gate_id}")


def evaluate(
    inp: ShadowInput,
    policy: ShadowPolicy,
    bands: RiskBands,
    tier_engine: PolicyEngine,
) -> ShadowDecision:
    """Pure decision function: inputs in, ShadowDecision out.

    No network, no side effects. The call is "merge" only when every
    deterministic gate passes AND the tier is within the policy's
    shadow_merge_max_tier. Jev is not consulted (not built); jev_score
    stays null and bands are recorded for the audit trail only.
    """
    gate_results = tuple(_run_gate(g, inp, policy) for g in policy.gates)

    tier_result = tier_engine.classify(list(inp.changed_files))
    tier = int(tier_result.risk_tier)
    try:
        tier_name = RiskTier(tier).name
    except ValueError:
        tier_name = f"TIER_{tier}"

    reasons: list[str] = []
    for g in gate_results:
        if not g.passed:
            reasons.append(f"gate failed: {g.gate} ({g.detail})")
    if tier > policy.shadow_merge_max_tier:
        reasons.append(
            f"tier {tier} ({tier_name}) above shadow max tier "
            f"{policy.shadow_merge_max_tier} — human-only"
        )

    call = CALL_MERGE if not reasons else CALL_SKIP
    if call == CALL_MERGE:
        reasons.append(
            f"all {len(gate_results)} gates passed; "
            f"tier {tier} ({tier_name}) within shadow max"
        )

    return ShadowDecision(
        pr_number=inp.pr_number,
        pr_title=inp.pr_title,
        head_sha=inp.head_sha,
        gate_results=gate_results,
        tier=tier,
        tier_name=tier_name,
        jev_score=None,
        bands={
            "version": bands.version,
            "auto_below": bands.auto_below,
            "human_above": bands.human_above,
            "on_jev_error": bands.on_jev_error,
            "applied": False,  # Jev not built; bands recorded, not consulted
        },
        call=call,
        reasons=tuple(reasons),
        policy_version=policy.version,
    )


# ─────────────────────────────────────────────────────────────────────
# Emission (append-only JSONL, queryable)
# ─────────────────────────────────────────────────────────────────────


def emit_decision(
    decision: ShadowDecision,
    sink: str | Path = DEFAULT_SHADOW_LOG,
) -> Path:
    """Append one audit signal per shadow decision. Returns the sink path."""
    sink = Path(sink)
    sink.parent.mkdir(parents=True, exist_ok=True)
    with open(sink, "a", encoding="utf-8") as f:
        f.write(json.dumps(decision.to_signal()) + "\n")
    return sink


def default_tier_engine(policy: ShadowPolicy) -> PolicyEngine:
    """Build the tier classifier from the policy's tier policy file,
    resolved relative to the repo root."""
    tier_path = Path(policy.tier_policy_file)
    if not tier_path.is_absolute():
        # spec/ dir sits at prismatic/review_factory/spec/; repo root is
        # three levels up.
        repo_root = SPEC_DIR.parent.parent.parent
        tier_path = repo_root / policy.tier_policy_file
    return PolicyEngine.from_yaml(tier_path)


def observe(
    inp: ShadowInput,
    policy: ShadowPolicy | None = None,
    bands: RiskBands | None = None,
    tier_engine: PolicyEngine | None = None,
    sink: str | Path = DEFAULT_SHADOW_LOG,
) -> ShadowDecision | None:
    """Evaluate one PR and emit its audit signal.

    Default-off: returns None and emits nothing unless the policy sets
    ``enabled: true``. Enabling shadow mode on live PRs needs Michael's word.
    """
    policy = policy or load_policy()
    if not policy.enabled:
        return None
    bands = bands or load_bands(SPEC_DIR / Path(policy.bands_file).name)
    tier_engine = tier_engine or default_tier_engine(policy)
    decision = evaluate(inp, policy, bands, tier_engine)
    emit_decision(decision, sink)
    return decision


def load_input_from_dict(data: dict[str, Any]) -> ShadowInput:
    """Build a ShadowInput from a plain dict (webhook/poll adapter input)."""
    return ShadowInput(
        pr_number=int(data["pr_number"]),
        pr_title=str(data.get("pr_title", "")),
        head_sha=str(data.get("head_sha", "")),
        base_sha=str(data.get("base_sha", "")),
        changed_files=tuple(data.get("changed_files", [])),
        ci_green_self_hosted=bool(data.get("ci_green_self_hosted", False)),
        ruff_clean=bool(data.get("ruff_clean", False)),
        review_verdict=str(data.get("review_verdict", "REJECT")),
        merge_conflicts=bool(data.get("merge_conflicts", True)),
        branch_protection_satisfied=bool(
            data.get("branch_protection_satisfied", False)
        ),
    )
