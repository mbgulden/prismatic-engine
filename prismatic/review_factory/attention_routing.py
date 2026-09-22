"""Attention routing (shadow mode) — Chunk 6 / Jev #29.

Risk-scores every PR so review time goes to the dangerous ones first.
Deterministic weighted score, 0–100, banded low/medium/high/critical.

Safety posture (build-sequence rules):

- ADVISORY ONLY. This module never blocks, never gates a merge, never
  posts a comment, never creates a status check. Scores are logged and
  emitted as audit signals; the workflow prints a ranked queue to the job
  summary and uploads the shadow log as an artifact.
- Deterministic sub-scores own the answer; Jev is consulted only when the
  deterministic novelty tripwire fires (first-time-ever file path or an
  unknown error class in failing checks), through the individually gated,
  default-off ``CallSiteGate("attention-routing")``. Jev advice is recorded
  in the audit signal and can never rewrite the deterministic score or band.
- Every ``score_pr()`` emits exactly one JSONL audit signal, shadow-only.
- Fail-closed: malformed/missing weights, Jev error, gate closed, audit
  write failure — none of these invent or change a score.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from prismatic.jev import (
    CallSiteGate,
    DecisionClient,
    DecisionError,
    Noul,
    Score,
)

logger = logging.getLogger("prismatic.review_factory.attention_routing")

CALL_SITE = "attention-routing"
WEIGHTS_VERSION = "attention-weights-v1"
AUDIT_COMPONENT = "attention-routing"
SHADOW_MARKER = "shadow — no action taken"

_DEFAULT_AUDIT_PATH = os.path.expanduser(
    "~/.prismatic/audit/attention-routing-shadow.jsonl"
)
_SPEC_PATH = Path(__file__).resolve().parent / "spec" / "attention_weights_v1.yaml"

_SUBSCORES = ("size", "blast_radius", "novelty", "history", "checks")


class AttentionConfigError(Exception):
    """Raised when the versioned weights policy cannot be loaded/parsed."""


@dataclass(frozen=True)
class PRInput:
    """Everything the router may look at. All fields optional except the
    identity ones; absent data yields lower sub-scores (fail-closed toward
    "nothing looks dangerous" — a PR we know nothing about is scored low,
    not high, and the audit row shows why)."""

    number: int
    head_sha: str
    author: str = ""
    additions: int = 0
    deletions: int = 0
    files: tuple[str, ...] = ()
    is_first_time_contributor: bool = False
    failing_checks: int = 0
    prior_rollbacks_by_author: int = 0
    recent_ci_failure_rate: float = 0.0  # 0..1 on touched files
    error_classes: tuple[str, ...] = ()
    days_open: int = 0


@dataclass(frozen=True)
class RiskScore:
    score: float  # 0..100
    band: str  # low | medium | high | critical
    subscores: dict[str, float]
    top_factors: tuple[str, ...]
    jev_status: str  # not_consulted | gate_closed | advised | errored
    jev_advice: dict[str, Any] | None
    weights_version: str = WEIGHTS_VERSION
    advisory_only: bool = True
    action_taken: bool = False

    def to_audit_dict(self) -> dict[str, Any]:
        # Deliberately no block/gate/merge_allowed fields: this signal must
        # never be mistaken for a merge gate.
        return {
            "component": AUDIT_COMPONENT,
            "weights_version": self.weights_version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "score": round(self.score, 2),
            "band": self.band,
            "subscores": {k: round(v, 3) for k, v in self.subscores.items()},
            "top_factors": list(self.top_factors),
            "jev_status": self.jev_status,
            "jev_advice": self.jev_advice,
            "advisory_only": self.advisory_only,
            "action_taken": self.action_taken,
            "shadow": SHADOW_MARKER,
        }


@dataclass
class _Weights:
    weights: dict[str, float]
    bands: dict[str, float]
    size_saturation_lines: float
    critical_paths: dict[str, float]
    known_error_classes: set[str]
    novelty_first_time_contributor: float
    novelty_first_time_path: float
    history_per_rollback: float
    history_max: float
    checks_per_failing: float
    checks_max: float


def _load_weights(spec_path: Path) -> _Weights:
    """Load and validate the versioned weights policy.

    Raises AttentionConfigError on any problem: missing file, bad YAML,
    wrong version, missing keys, wrong types. Fail-closed: a router that
    cannot load its weights must not score.
    """
    try:
        raw = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AttentionConfigError(f"cannot read weights {spec_path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise AttentionConfigError(
            f"weights {spec_path} not valid YAML: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise AttentionConfigError(f"weights {spec_path}: top level must be a mapping")
    if data.get("version") != WEIGHTS_VERSION:
        raise AttentionConfigError(
            f"weights {spec_path}: version {data.get('version')!r} != {WEIGHTS_VERSION!r}"
        )

    def req(key: str) -> Any:
        if key not in data:
            raise AttentionConfigError(f"weights {spec_path}: missing key {key!r}")
        return data[key]

    weights = req("weights")
    if not isinstance(weights, dict) or set(weights) != set(_SUBSCORES):
        raise AttentionConfigError(
            f"weights {spec_path}: 'weights' must cover exactly {sorted(_SUBSCORES)}"
        )
    for k, v in weights.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise AttentionConfigError(
                f"weights {spec_path}: weight {k!r} must be >= 0"
            )

    bands = req("bands")
    if not isinstance(bands, dict) or set(bands) != {"low", "medium", "high"}:
        raise AttentionConfigError(
            f"weights {spec_path}: 'bands' must have exactly low/medium/high"
        )

    critical_paths = req("critical_paths")
    if not isinstance(critical_paths, dict):
        raise AttentionConfigError(
            f"weights {spec_path}: 'critical_paths' must be a mapping"
        )

    known = req("known_error_classes")
    if not isinstance(known, list):
        raise AttentionConfigError(
            f"weights {spec_path}: 'known_error_classes' must be a list"
        )

    def num(key: str, default: float) -> float:
        v = data.get(key, default)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise AttentionConfigError(f"weights {spec_path}: {key!r} must be a number")
        return float(v)

    return _Weights(
        weights={k: float(v) for k, v in weights.items()},
        bands={k: float(v) for k, v in bands.items()},
        size_saturation_lines=num("size_saturation_lines", 2000.0),
        critical_paths={str(k): float(v) for k, v in critical_paths.items()},
        known_error_classes={str(c) for c in known},
        novelty_first_time_contributor=num("novelty_first_time_contributor", 0.6),
        novelty_first_time_path=num("novelty_first_time_path", 0.8),
        history_per_rollback=num("history_per_rollback", 0.25),
        history_max=num("history_max", 1.0),
        checks_per_failing=num("checks_per_failing", 0.2),
        checks_max=num("checks_max", 1.0),
    )


class AttentionRouter:
    """Deterministic PR risk scoring. Construct once; call ``score_pr`` per PR."""

    def __init__(
        self,
        spec_path: Path | str = _SPEC_PATH,
        audit_path: str = _DEFAULT_AUDIT_PATH,
        client_factory: Any = None,
        known_paths: set[str] | None = None,
    ) -> None:
        self._w = _load_weights(Path(spec_path))
        self._audit_path = audit_path
        self._client_factory = client_factory or DecisionClient
        self._gate = CallSiteGate(CALL_SITE)
        # Paths ever seen before (for the first-time-path novelty signal).
        # Data-in slot: a durable store can be injected later; empty set =
        # everything looks new (fail-closed toward *higher* novelty, which is
        # the safe direction for an advisory score).
        self._known_paths = known_paths if known_paths is not None else set()

    # -- sub-scores (each 0..1) ----------------------------------------

    def _size(self, pr: PRInput) -> float:
        total = max(0, pr.additions) + max(0, pr.deletions)
        if total <= 0:
            return 0.0
        sat = max(1.0, self._w.size_saturation_lines)
        return min(1.0, math.log1p(total) / math.log1p(sat))

    def _blast_radius(self, pr: PRInput) -> float:
        if not pr.files:
            return 0.0
        hits = 0.0
        for f in pr.files:
            best = 0.0
            for prefix, factor in self._w.critical_paths.items():
                if f == prefix or f.startswith(prefix):
                    best = max(best, factor)
            hits += best
        return min(1.0, hits / len(pr.files))

    def _novelty(self, pr: PRInput) -> tuple[float, bool]:
        """Return (subscore, tripwire_fired). The tripwire is the only path
        to a Jev consult (Jev #29)."""
        score = 0.0
        tripwire = False
        if pr.is_first_time_contributor:
            score = max(score, self._w.novelty_first_time_contributor)
        new_paths = [f for f in pr.files if f not in self._known_paths]
        if new_paths and pr.files:
            score = max(score, self._w.novelty_first_time_path)
            tripwire = True
        unknown_classes = [
            c for c in pr.error_classes if c not in self._w.known_error_classes
        ]
        if unknown_classes:
            score = max(score, self._w.novelty_first_time_path)
            tripwire = True
        return min(1.0, score), tripwire

    def _history(self, pr: PRInput) -> float:
        rate = min(1.0, max(0.0, pr.recent_ci_failure_rate))
        rollbacks = min(
            self._w.history_max,
            max(0, pr.prior_rollbacks_by_author) * self._w.history_per_rollback,
        )
        return min(1.0, max(rollbacks, rate))

    def _checks(self, pr: PRInput) -> float:
        return min(
            self._w.checks_max, max(0, pr.failing_checks) * self._w.checks_per_failing
        )

    # -- Jev (exception path only) -------------------------------------

    def _consult_jev(
        self, pr: PRInput, score: float
    ) -> tuple[str, dict[str, Any] | None]:
        """Novelty-tripwire Jev consult (Jev #29). Advisory only: the return
        is recorded, never applied to the score. Never raises."""
        if not self._gate.allow():
            return "gate_closed", None
        state = {
            "pr_number": pr.number,
            "head_sha": pr.head_sha,
            "author": pr.author,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "files_changed": len(pr.files),
            "is_first_time_contributor": pr.is_first_time_contributor,
            "failing_checks": pr.failing_checks,
            "error_classes": list(pr.error_classes),
            "deterministic_score": round(score, 2),
        }
        prompt = (
            "A pull request tripped the novelty detector (first-time-ever "
            "file paths touched, or a failing check with an error class the "
            "deterministic rules do not recognize). Place this PR's review "
            "risk on a 0-100 scale, where 0 is trivially safe and 100 is "
            "review-this-first dangerous. This is advisory only; it never "
            "blocks or gates the merge."
        )
        try:
            client = self._client_factory()
            result = client.decide(
                state,
                [
                    Score("risk_advice", prompt, min=0.0, max=100.0),
                    Noul("confident", "How confident are you in this risk advice?"),
                ],
                on_error="raise",
            )
        except DecisionError as exc:
            logger.warning("Jev attention call failed closed: %s", exc)
            return "errored", None
        except Exception as exc:  # fail-closed on anything unexpected
            logger.warning("Jev attention call failed closed (unexpected): %r", exc)
            return "errored", None
        advice = result.to_audit_dict()
        advice["advisory_only"] = True
        return "advised", advice

    # -- public API ----------------------------------------------------

    def _band(self, score: float) -> str:
        b = self._w.bands
        if score < b["low"]:
            return "low"
        if score < b["medium"]:
            return "medium"
        if score < b["high"]:
            return "high"
        return "critical"

    def score_pr(self, pr: PRInput) -> RiskScore:
        novelty, tripwire = self._novelty(pr)
        subscores = {
            "size": self._size(pr),
            "blast_radius": self._blast_radius(pr),
            "novelty": novelty,
            "history": self._history(pr),
            "checks": self._checks(pr),
        }
        total = sum(self._w.weights[k] * v for k, v in subscores.items())
        score = 100.0 * min(1.0, max(0.0, total))
        band = self._band(score)

        contributions = sorted(
            ((self._w.weights[k] * v, k) for k, v in subscores.items()),
            reverse=True,
        )
        top_factors = tuple(
            f"{name}={subscores[name]:.2f}" for _, name in contributions[:3]
        )

        jev_status = "not_consulted"
        jev_advice: dict[str, Any] | None = None
        if tripwire:
            jev_status, jev_advice = self._consult_jev(pr, score)

        result = RiskScore(
            score=score,
            band=band,
            subscores=subscores,
            top_factors=top_factors,
            jev_status=jev_status,
            jev_advice=jev_advice,
        )
        self._write_audit(result, pr)
        return result

    def _write_audit(self, result: RiskScore, pr: PRInput) -> None:
        """Append exactly one JSONL row. A write failure is logged and never
        changes the score (the result was already computed)."""
        row = result.to_audit_dict()
        row["pr"] = {
            "number": pr.number,
            "head_sha": pr.head_sha,
            "author": pr.author,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "files_changed": len(pr.files),
            "is_first_time_contributor": pr.is_first_time_contributor,
            "failing_checks": pr.failing_checks,
        }
        try:
            path = Path(self._audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError as exc:
            logger.warning("attention audit write failed (score stands): %s", exc)
