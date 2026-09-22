"""Attention routing (Jev #29) — shadow mode, advisory only.

Risk-score every PR so review time goes to the dangerous ones first.
Advisory only, never blocking.

Pipeline (deterministic features first; Jev refines upward only):
  1. Deterministic feature extraction — pure code, no Jev: diff stats,
     risky-path hits, author novelty, CI history, policy-file touches.
  2. Weighted deterministic score from the versioned weights file
     (``attention_risk_weights_v1.yaml``) -> band low / medium / high.
  3. Gated Jev refinement, upward only — only when the deterministic band
     is ``low`` or ``medium`` (a deterministic ``high`` short-circuits;
     Jev never lowers it). Combined band = max(deterministic, jev).
  4. Advisory markdown report + exactly one JSONL audit signal per
     ``score_pr()``.

This module never imports any merge, block, or policy-enforcement code.
It creates no status checks, holds no merge, approves or denies nothing.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from prismatic.jev import CallSiteGate, DecisionClient, DecisionError, Noul, Score

logger = logging.getLogger("prismatic.review_factory.attention_routing")

CALL_SITE = "attention-routing"

RISK_BAND_LOW = "low"
RISK_BAND_MEDIUM = "medium"
RISK_BAND_HIGH = "high"

_BAND_SEVERITY = {
    RISK_BAND_LOW: 0,
    RISK_BAND_MEDIUM: 1,
    RISK_BAND_HIGH: 2,
}

SHADOW_MARKER = "shadow — advisory only, never blocking"
ADVISORY_COMMENT_MARKER = "<!-- attention-routing-shadow -->"

AUDIT_FILENAME = "attention-routing-shadow.jsonl"

_DEFAULT_AUDIT_PATH = Path.home() / ".prismatic" / "audit" / AUDIT_FILENAME

_DEFAULT_THRESHOLDS = {"low_max": 30.0, "medium_max": 70.0}

_JEV_NOT_CONSULTED = "not_consulted"
_JEV_GATE_CLOSED = "gate_closed"
_JEV_ERRORED = "errored"
_JEV_ADVISED = "advised"


def band_for_score(score: float, thresholds: dict[str, float]) -> str:
    """Map a 0–100 score to a risk band using the versioned thresholds."""
    low_max = float(thresholds.get("low_max", _DEFAULT_THRESHOLDS["low_max"]))
    medium_max = float(thresholds.get("medium_max", _DEFAULT_THRESHOLDS["medium_max"]))
    if score < low_max:
        return RISK_BAND_LOW
    if score < medium_max:
        return RISK_BAND_MEDIUM
    return RISK_BAND_HIGH


def max_band(first: str, second: str) -> str:
    """No-downgrade combination: the more severe band wins."""
    if _BAND_SEVERITY[second] > _BAND_SEVERITY[first]:
        return second
    return first


@dataclass(frozen=True)
class ChangedFile:
    path: str
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class PRInput:
    """One PR to score (event data in — no polling)."""

    files: tuple[ChangedFile, ...] = ()
    author_login: str = ""
    first_time_contributor: bool = False
    prior_failed_runs: int = 0


@dataclass(frozen=True)
class PRFeatures:
    lines_added: int
    lines_removed: int
    files_changed: int
    #: (pattern, path) hits against the versioned risky-path patterns.
    risky_path_hits: tuple[tuple[str, str], ...]
    first_time_contributor: bool
    prior_failed_runs: int
    policy_file_touches: tuple[str, ...]


@dataclass(frozen=True)
class Factor:
    """One scored risk factor: label, detail, and point contribution."""

    name: str
    detail: str
    points: float


@dataclass(frozen=True)
class AdvisoryReport:
    pr_number: int
    head_sha: str
    author_login: str
    deterministic_score: float
    deterministic_band: str
    jev_status: str  # not_consulted | gate_closed | errored | advised
    jev_advice: dict[str, Any] | None
    jev_band: str | None
    combined_band: str
    top_factors: tuple[Factor, ...]  # exactly 3, ranked by points desc
    config_invalid: bool
    advisory_only: bool = True

    def render_markdown(self) -> str:
        lines = [
            f"{ADVISORY_COMMENT_MARKER}",
            "# Attention routing — advisory report (shadow mode)",
            "",
            f"**PR:** #{self.pr_number} (`{self.head_sha[:12]}`) by "
            f"@{self.author_login or 'unknown'}",
            f"**Combined risk band:** {self.combined_band.upper()} "
            f"(deterministic {self.deterministic_band}, score "
            f"{self.deterministic_score:.1f}/100)",
            "",
            "## Top risk factors",
            "",
        ]
        for rank, factor in enumerate(self.top_factors, start=1):
            lines.append(
                f"{rank}. **{factor.name}** (+{factor.points:.1f}) — {factor.detail}"
            )
        lines += ["", "## Jev refinement", ""]
        if self.jev_status == _JEV_ADVISED and self.jev_advice is not None:
            advice = self.jev_advice
            lines.append(
                f"- consulted: review_risk {advice['score']:.2f} "
                f"(band {self.jev_band}), confidence {advice['confidence']}, "
                f"backend {advice['backend']}, latency {advice['latency_ms']} ms"
            )
            lines.append("- Jev refines upward only; it never lowers a band.")
        elif self.jev_status == _JEV_NOT_CONSULTED:
            lines.append(
                "- not consulted: deterministic band is high "
                "(Jev never lowers it — short-circuit)."
            )
        elif self.jev_status == _JEV_GATE_CLOSED:
            lines.append("- not consulted: Jev call-site gate is closed (default off).")
        else:
            lines.append("- Jev call errored; deterministic band kept (fail-closed).")
        if self.config_invalid:
            lines.append("- weights config invalid: baseline scoring only.")
        lines += [
            "",
            "> **" + SHADOW_MARKER + "**",
            ">",
            "> This report is read-only metadata. It creates no status checks,",
            "> holds no merge, and approves or denies nothing.",
        ]
        return "\n".join(lines) + "\n"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_risk_weights(
    path: str | Path,
) -> tuple[str | None, dict[str, float], dict[str, dict[str, float]], list[str], bool]:
    """Load the versioned attention-risk weights.

    Returns ``(weights_version, thresholds, features, risky_patterns,
    config_invalid)``. A missing file yields empty weights (baseline
    scoring only); a malformed file yields ``config_invalid=True`` with
    empty weights — never an exception, never a blocked PR (fail-closed).
    """
    path = Path(path)
    if not path.exists():
        return (None, dict(_DEFAULT_THRESHOLDS), {}, [], False)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.warning("attention weights %s unparseable: %s", path, exc)
        return (None, dict(_DEFAULT_THRESHOLDS), {}, [], True)
    if not isinstance(raw, dict):
        logger.warning("attention weights %s: expected a mapping", path)
        return (None, dict(_DEFAULT_THRESHOLDS), {}, [], True)

    version = raw.get("version")
    if not isinstance(version, str) or not version:
        logger.warning("attention weights %s: missing version", path)
        return (None, dict(_DEFAULT_THRESHOLDS), {}, [], True)

    thresholds = dict(_DEFAULT_THRESHOLDS)
    raw_thresholds = raw.get("thresholds", {})
    if not isinstance(raw_thresholds, dict):
        logger.warning("attention weights %s: bad thresholds", path)
        return (version, dict(_DEFAULT_THRESHOLDS), {}, [], True)
    for key in ("low_max", "medium_max"):
        value = raw_thresholds.get(key, thresholds[key])
        if not _is_number(value):
            logger.warning("attention weights %s: bad threshold %s", path, key)
            return (version, dict(_DEFAULT_THRESHOLDS), {}, [], True)
        thresholds[key] = float(value)

    raw_features = raw.get("features", {})
    if not isinstance(raw_features, dict):
        logger.warning("attention weights %s: bad features", path)
        return (version, thresholds, {}, [], True)
    features: dict[str, dict[str, float]] = {}
    for name, spec in raw_features.items():
        if not isinstance(spec, dict):
            logger.warning("attention weights %s: bad feature %r", path, name)
            return (version, thresholds, {}, [], True)
        clean: dict[str, float] = {}
        for key in ("weight_per_unit", "weight_flat", "cap"):
            value = spec.get(key, 0.0)
            if not _is_number(value) or float(value) < 0:
                logger.warning(
                    "attention weights %s: bad value for %s.%s", path, name, key
                )
                return (version, thresholds, {}, [], True)
            clean[key] = float(value)
        features[str(name)] = clean

    raw_patterns = raw.get("risky_path_patterns", [])
    if not isinstance(raw_patterns, list) or not all(
        isinstance(p, str) for p in raw_patterns
    ):
        logger.warning("attention weights %s: bad risky_path_patterns", path)
        return (version, thresholds, {}, [], True)

    return (version, thresholds, features, [str(p) for p in raw_patterns], False)


def _is_policy_file(path: str) -> bool:
    if not path.endswith((".yaml", ".yml")):
        return False
    return (
        "/spec/" in path
        or path.startswith("spec/")
        or "policy" in path.rsplit("/", 1)[-1]
    )


class AttentionRouter:
    """Deterministic PR risk scoring with upward-only gated Jev refinement.

    Advisory only. Nothing here can block, hold, or gate a PR.
    """

    def __init__(
        self,
        weights_path: str | Path | None = None,
        audit_path: str | Path | None = None,
        client: DecisionClient | None = None,
    ) -> None:
        if weights_path is None:
            weights_path = (
                Path(__file__).resolve().parent
                / "spec"
                / "attention_risk_weights_v1.yaml"
            )
        (
            self._weights_version,
            self._thresholds,
            self._features,
            self._risky_patterns,
            self._config_invalid,
        ) = load_risk_weights(weights_path)
        self._audit_path = Path(audit_path) if audit_path else _DEFAULT_AUDIT_PATH
        self._client = client
        self._gate = CallSiteGate(CALL_SITE)

    # ── public API ────────────────────────────────────────────────────

    @property
    def config_invalid(self) -> bool:
        return self._config_invalid

    def extract_features(self, pr: PRInput) -> PRFeatures:
        """Deterministic feature extraction — pure code, no Jev."""
        lines_added = sum(max(0, f.additions) for f in pr.files)
        lines_removed = sum(max(0, f.deletions) for f in pr.files)
        hits: list[tuple[str, str]] = []
        policy_touches: list[str] = []
        for changed in pr.files:
            for pattern in self._risky_patterns:
                if fnmatch.fnmatch(changed.path, pattern):
                    hits.append((pattern, changed.path))
            if _is_policy_file(changed.path):
                policy_touches.append(changed.path)
        return PRFeatures(
            lines_added=lines_added,
            lines_removed=lines_removed,
            files_changed=len(pr.files),
            risky_path_hits=tuple(hits),
            first_time_contributor=pr.first_time_contributor,
            prior_failed_runs=max(0, pr.prior_failed_runs),
            policy_file_touches=tuple(policy_touches),
        )

    def deterministic_score(self, features: PRFeatures) -> tuple[float, list[Factor]]:
        """Weighted deterministic score 0–100 plus per-factor contributions."""
        weights = self._features

        def spec(name: str) -> dict[str, float]:
            return weights.get(
                name, {"weight_per_unit": 0.0, "weight_flat": 0.0, "cap": 0.0}
            )

        factors: list[Factor] = []

        def add(name: str, detail: str, units: float, flat: bool = False) -> None:
            cfg = spec(name)
            raw_points = (
                cfg["weight_flat"] * units if flat else cfg["weight_per_unit"] * units
            )
            points = min(raw_points, cfg["cap"]) if cfg["cap"] > 0 else raw_points
            factors.append(Factor(name=name, detail=detail, points=points))

        add(
            "lines_added",
            f"{features.lines_added} lines added",
            float(features.lines_added),
        )
        add(
            "lines_removed",
            f"{features.lines_removed} lines removed",
            float(features.lines_removed),
        )
        add(
            "files_changed",
            f"{features.files_changed} files changed",
            float(features.files_changed),
        )
        add(
            "risky_path_hit",
            (
                f"{len(features.risky_path_hits)} risky-path hit(s): "
                + (
                    ", ".join(p for _, p in features.risky_path_hits[:3])
                    if features.risky_path_hits
                    else "none"
                )
            ),
            float(len(features.risky_path_hits)),
        )
        add(
            "first_time_contributor",
            (
                "first-time contributor"
                if features.first_time_contributor
                else "returning contributor"
            ),
            1.0 if features.first_time_contributor else 0.0,
            flat=True,
        )
        add(
            "prior_failed_run",
            f"{features.prior_failed_runs} prior failed run(s) on this head",
            float(features.prior_failed_runs),
        )
        add(
            "policy_file_touch",
            (
                "touches policy file(s): "
                + (
                    ", ".join(features.policy_file_touches[:3])
                    if features.policy_file_touches
                    else "none"
                )
            ),
            1.0 if features.policy_file_touches else 0.0,
            flat=True,
        )

        score = min(100.0, max(0.0, sum(f.points for f in factors)))
        return (score, factors)

    def score_pr(
        self, pr: PRInput, *, pr_number: int = 0, head_sha: str = ""
    ) -> AdvisoryReport:
        """Score one PR; emit exactly one audit signal; block nothing."""
        features = self.extract_features(pr)
        det_score, factors = self.deterministic_score(features)
        det_band = band_for_score(det_score, self._thresholds)

        gate_allowed = self._gate.allow()
        jev_status = _JEV_NOT_CONSULTED
        jev_advice: dict[str, Any] | None = None
        jev_band: str | None = None

        if det_band == RISK_BAND_HIGH:
            # Short-circuit: Jev never lowers a deterministic high.
            jev_status = _JEV_NOT_CONSULTED
        elif not gate_allowed:
            jev_status = _JEV_GATE_CLOSED
        else:
            jev_status, jev_advice, jev_band = self._refine_with_jev(features)

        all_factors = list(factors)
        if jev_status == _JEV_ADVISED and jev_advice is not None:
            all_factors.append(
                Factor(
                    name="jev_review_risk",
                    detail=f"Jev review_risk {jev_advice['score']:.2f}/1.00 "
                    f"(confidence {jev_advice['confidence']})",
                    points=float(jev_advice["score"]) * 100.0,
                )
            )
        top_factors = tuple(sorted(all_factors, key=lambda f: (-f.points, f.name))[:3])

        combined_band = det_band
        if jev_band is not None:
            combined_band = max_band(det_band, jev_band)

        report = AdvisoryReport(
            pr_number=pr_number,
            head_sha=head_sha,
            author_login=pr.author_login,
            deterministic_score=det_score,
            deterministic_band=det_band,
            jev_status=jev_status,
            jev_advice=jev_advice,
            jev_band=jev_band,
            combined_band=combined_band,
            top_factors=top_factors,
            config_invalid=self._config_invalid,
            advisory_only=True,
        )
        self._append_audit(report, features, gate_allowed, det_score)
        return report

    # ── internals ─────────────────────────────────────────────────────

    def _refine_with_jev(
        self, features: PRFeatures
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        """One decide() call: Score + confidence Noul, same billed call."""
        client = self._client or DecisionClient()
        state = {
            "lines_added": features.lines_added,
            "lines_removed": features.lines_removed,
            "files_changed": features.files_changed,
            "risky_path_hits": [path for _, path in features.risky_path_hits],
            "first_time_contributor": features.first_time_contributor,
            "prior_failed_runs": features.prior_failed_runs,
            "policy_file_touches": list(features.policy_file_touches),
        }
        questions = [
            Score("review_risk", "How risky is this PR to merge?"),
            Noul("confident", "How confident are you in this risk score?"),
        ]
        try:
            outcome = client.decide(state, questions)
        except DecisionError as exc:
            logger.warning(
                "Jev decide() failed (%s); keeping deterministic band",
                type(exc).__name__,
            )
            return (_JEV_ERRORED, None, None)

        score_answer = outcome.answers["review_risk"]
        noul_answer = outcome.answers.get("confident")
        advice: dict[str, Any] = {
            "score": score_answer.score,
            "confidence": score_answer.confidence,
            "noul_probability": (
                noul_answer.probability if noul_answer is not None else None
            ),
            "noul_confidence": (
                noul_answer.confidence if noul_answer is not None else None
            ),
            "backend": outcome.backend,
            "latency_ms": round(outcome.latency_ms, 2),
        }
        jev_band = band_for_score(score_answer.score * 100.0, self._thresholds)
        return (_JEV_ADVISED, advice, jev_band)

    def _append_audit(
        self,
        report: AdvisoryReport,
        features: PRFeatures,
        gate_allowed: bool,
        det_score: float,
    ) -> None:
        """Exactly one JSONL signal per score_pr(). Write failure never
        changes the report."""
        row = {
            "component": "attention-routing",
            "ts": datetime.now(timezone.utc).isoformat(),
            "weights_version": self._weights_version,
            "config_invalid": self._config_invalid,
            "pr_number": report.pr_number,
            "head_sha": report.head_sha,
            "features": {
                "lines_added": features.lines_added,
                "lines_removed": features.lines_removed,
                "files_changed": features.files_changed,
                "risky_path_hits": [
                    {"pattern": pattern, "path": path}
                    for pattern, path in features.risky_path_hits
                ],
                "first_time_contributor": features.first_time_contributor,
                "prior_failed_runs": features.prior_failed_runs,
                "policy_file_touches": list(features.policy_file_touches),
            },
            "deterministic_score": round(det_score, 2),
            "deterministic_band": report.deterministic_band,
            "gate": {"site": CALL_SITE, "allowed": gate_allowed},
            "jev_status": report.jev_status,
            "jev_advice": report.jev_advice,
            "jev_band": report.jev_band,
            "combined_band": report.combined_band,
            "top_factors": [
                {"name": f.name, "detail": f.detail, "points": round(f.points, 2)}
                for f in report.top_factors
            ],
            "advisory_only": True,
        }
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            logger.warning(
                "attention-routing audit write failed; report unchanged",
                exc_info=True,
            )


__all__ = [
    "ADVISORY_COMMENT_MARKER",
    "CALL_SITE",
    "RISK_BAND_HIGH",
    "RISK_BAND_LOW",
    "RISK_BAND_MEDIUM",
    "SHADOW_MARKER",
    "AdvisoryReport",
    "AttentionRouter",
    "ChangedFile",
    "Factor",
    "PRFeatures",
    "PRInput",
    "band_for_score",
    "load_risk_weights",
    "max_band",
]
