"""L2 judgment layer for the review factory (Jev validation-loop plan, §3/§8).

Jev judges ONLY what a deterministic process cannot decide (the closed
5-question pack in :mod:`prismatic.review_factory.review_questions`).
``JevJudge`` wraps the ``prismatic.jev`` typed-decision primitive;
``NullJudge`` is the zero-AI path. Everything sits behind
``CallSiteGate("review_judgment")`` — default-off, inert until enabled.

Invariants (enforced here, in code, never in prompts):

- ``evaluate()`` asserts deterministic == CLEAN or raises — Jev never sees
  red.
- The judge never approves and never overrides REPAIR/REJECT. Its output
  vocabulary is {CLEAR, PAUSE}; PAUSE on CLEAN becomes ESCALATE only via
  :func:`prismatic.jev.gates.apply_jev_advice` — the mechanical
  no-downgrade enforcer (unit-tested, reused as-is).
- Diff and brief content crosses the boundary as :class:`Untrusted` data
  through :func:`prismatic.jev.redact.serialize_state` — untrusted data,
  never instructions.
- A Jev backend failure leaves the deterministic verdict standing
  (``on_error="deterministic"``, defaults -> CLEAR): fail-open toward the
  deterministic result, since Jev can only escalate.
- No backend configured -> NullJudge automatically: the record says
  ``judgment: null`` + ``explicit_non_claims: ["no judgment applied"]``
  (fail-safe, not fail-silent).
- Backend choice comes from ``SWARMJEV_*`` env via ``JevConfig.from_env()``
  — never hardcoded.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from prismatic.jev import CallSiteGate, Choice, DecisionClient
from prismatic.jev.config import JevConfig
from prismatic.jev.errors import DecisionError
from prismatic.jev.gates import advice_choice
from prismatic.jev.memo import MemoCache
from prismatic.jev.redact import Untrusted, serialize_state

from prismatic.review_factory.review_questions import (
    QUESTION_TEXTS,
    REMIT_QUESTIONS,
    render_review_prompt,
)

logger = logging.getLogger(__name__)

# Call-site name for the per-site gate: enabled only when BOTH
# SWARMJEV_ENABLED and SWARMJEV_CALLSITE_REVIEW_JUDGMENT_ENABLED are truthy.
JUDGMENT_CALL_SITE = "review_judgment"

# The verdict question's wire name; the five remit questions keep their
# pack names from review_questions.REMIT_QUESTIONS.
VERDICT_QUESTION_NAME = "review_verdict"

# Confidence floor for the typed answers: below this an answer is marked
# abstained, and per §3 abstain -> ESCALATE (low confidence).
_ABSTAIN_FLOOR = 0.6

# Bound on diff text sent toward a Jev backend: a bounded excerpt, never a
# dump. Truncation is marked so Jev can abstain on truncated
# security-sensitive paths instead of guessing.
_MAX_DIFF_CHARS = 12_000

_DECISIONS = ("CLEAR", "PAUSE")


@dataclass(frozen=True)
class JudgmentReason:
    """One typed reason: which question, what was found, how severe."""

    question: str
    finding: str
    severity: str  # "low" | "medium" | "high"


@dataclass(frozen=True)
class Judgment:
    """The judge's answer: CLEAR | PAUSE, or None when no judgment applied."""

    judge: str  # "jev" | "null"
    decision: str | None  # "CLEAR" | "PAUSE" | None (None = no judgment)
    confidence: float
    reasons: tuple[JudgmentReason, ...] = ()
    trace_id: str = ""
    backend: str = ""
    explicit_non_claims: tuple[str, ...] = ()
    skipped: str | None = None  # e.g. "tier-0" when tiering skipped judgment

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "decision": self.decision,
            "confidence": self.confidence,
            "reasons": [
                {
                    "question": r.question,
                    "finding": r.finding,
                    "severity": r.severity,
                }
                for r in self.reasons
            ],
            "trace_id": self.trace_id,
            "backend": self.backend,
            "explicit_non_claims": list(self.explicit_non_claims),
            "skipped": self.skipped,
        }


class Judge(Protocol):
    """L2 judge interface: artifact + deterministic verdict -> Judgment."""

    name: str

    def evaluate(
        self, artifact: Mapping[str, Any], deterministic_verdict: str
    ) -> Judgment: ...


def _require_clean(deterministic_verdict: str) -> None:
    """HARD: Jev never sees red. Anything but CLEAN raises."""
    if str(deterministic_verdict or "").strip().upper() != "CLEAN":
        raise DecisionError(
            "judge refuses non-CLEAN deterministic verdict "
            f"{deterministic_verdict!r}: Jev never sees red"
        )


class NullJudge:
    """Zero-AI path: no judgment applied, recorded explicitly.

    The verdict records ``judgment: null`` + ``explicit_non_claims:
    ["no judgment applied"]`` — fail-safe, not fail-silent.
    """

    name = "null"

    def evaluate(
        self, artifact: Mapping[str, Any], deterministic_verdict: str
    ) -> Judgment:
        _require_clean(deterministic_verdict)
        return Judgment(
            judge=self.name,
            decision=None,
            confidence=0.0,
            explicit_non_claims=("no judgment applied",),
        )


def _backend_configured() -> bool:
    """True when SWARMJEV_BACKEND names a real (network) backend.

    Unset or "fallback" means no backend is configured: the judge falls
    back to NullJudge (fail-safe, not fail-silent). Backend choice stays
    configuration via env — never hardcoded.
    """
    name = os.environ.get("SWARMJEV_BACKEND", "").strip().lower()
    return name not in ("", "fallback")


class JevJudge:
    """Jev-backed judge over the typed-decision primitive.

    Wraps :class:`prismatic.jev.client.DecisionClient`; the final
    deterministic+advice combination always routes through
    :func:`apply_jev_advice`. When no backend is configured (or the
    configured one cannot be built), evaluation degrades to a null
    judgment — never a fabricated CLEAR.
    """

    name = "jev"

    def __init__(
        self,
        *,
        gate: CallSiteGate | None = None,
        client_factory: Any | None = None,
        config: JevConfig | None = None,
    ) -> None:
        self._gate = gate if gate is not None else CallSiteGate(JUDGMENT_CALL_SITE)
        self._client_factory = (
            client_factory if client_factory is not None else DecisionClient
        )
        self._config = config
        # Memoize on artifact_id: identical artifact -> identical judgment
        # record, no re-call (bounded, TTL'd, thread-safe).
        self._memo = MemoCache(max_entries=512, ttl_s=3600.0)

    @property
    def gate(self) -> CallSiteGate:
        return self._gate

    def evaluate(
        self, artifact: Mapping[str, Any], deterministic_verdict: str
    ) -> Judgment:
        _require_clean(deterministic_verdict)
        artifact_id = str(artifact.get("artifact_id") or artifact.get("job_id") or "")
        if artifact_id:
            cached = self._memo.lookup(artifact_id)
            if cached is not None:
                return cached
        if not _backend_configured():
            judgment = self._null_judgment("no Jev backend configured")
        else:
            judgment = self._decide(artifact, artifact_id)
        if artifact_id:
            self._memo.store(artifact_id, judgment)
        return judgment

    # -- internals ------------------------------------------------------

    def _null_judgment(self, reason: str) -> Judgment:
        logger.info("no judgment applied: %s", reason)
        return Judgment(
            judge="null",
            decision=None,
            confidence=0.0,
            explicit_non_claims=("no judgment applied",),
        )

    def _decide(self, artifact: Mapping[str, Any], artifact_id: str) -> Judgment:
        try:
            config = self._config if self._config is not None else JevConfig.from_env()
            client = self._client_factory(config=config)
        except DecisionError as exc:
            # Backend named but unusable (missing credential, bad config):
            # fail safe to no judgment rather than a fabricated CLEAR.
            logger.warning("jev backend unavailable (%s); no judgment applied", exc)
            return self._null_judgment(f"backend unavailable: {exc}")
        state = self._judge_state(artifact)
        wire_state, _untrusted_fields = serialize_state(state, secrets.token_hex(16))
        questions = self._questions()
        defaults = {
            q.name: ("CLEAR" if q.name == VERDICT_QUESTION_NAME else "clear")
            for q in questions
        }
        # Fail-open toward the deterministic result: any backend failure
        # routes to defaults (CLEAR), and the deterministic verdict stands
        # because Jev can only escalate.
        result = client.decide(
            wire_state,
            questions,
            on_error="deterministic",
            defaults=defaults,
            idempotency_key=artifact_id or None,
        )
        return self._to_judgment(result)

    @staticmethod
    def _questions() -> list[Choice]:
        questions: list[Choice] = []
        for (name, _title), text in zip(REMIT_QUESTIONS, QUESTION_TEXTS, strict=True):
            # Per-question prompt is the question text itself — the pack is
            # closed, Jev gets no other instructions.
            questions.append(
                Choice(
                    name=name,
                    prompt=text,
                    options=["clear", "concern"],
                    abstain_below=_ABSTAIN_FLOOR,
                )
            )
        questions.append(
            Choice(
                name=VERDICT_QUESTION_NAME,
                prompt=render_review_prompt(),
                options=["CLEAR", "PAUSE"],
                abstain_below=_ABSTAIN_FLOOR,
            )
        )
        return questions

    @staticmethod
    def _judge_state(artifact: Mapping[str, Any]) -> dict[str, Any]:
        """Build the Jev input state. Diff/brief are Untrusted data."""
        intent = artifact.get("intent") or {}
        diff = artifact.get("diff") or {}
        unified = str(diff.get("unified") or "")
        if len(unified) > _MAX_DIFF_CHARS:
            omitted = len(unified) - _MAX_DIFF_CHARS
            unified = (
                unified[:_MAX_DIFF_CHARS]
                + f"\n...[diff truncated: {omitted} chars omitted]..."
            )
        files = diff.get("files") or []
        checks = artifact.get("checks") or []
        return {
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "intent": {
                "plan_ref": intent.get("plan_ref"),
                "brief": Untrusted(str(intent.get("brief") or "")),
                "goals": [Untrusted(str(goal)) for goal in (intent.get("goals") or [])],
            },
            "diff": {
                "base_tree": str(diff.get("base_tree") or ""),
                "head_tree": str(diff.get("head_tree") or ""),
                "files": [
                    {
                        "path": entry.get("path"),
                        "change_type": entry.get("change_type"),
                        "lines_added": entry.get("lines_added"),
                        "lines_removed": entry.get("lines_removed"),
                    }
                    for entry in files
                    if isinstance(entry, dict)
                ],
                "unified": Untrusted(unified),
                "truncated": bool(diff.get("truncated", False)),
            },
            "checks": [
                {"name": entry.get("name"), "exit_code": entry.get("exit_code")}
                for entry in checks
                if isinstance(entry, dict)
            ],
            "concurrent_artifacts": list(artifact.get("concurrent_artifacts") or []),
        }

    @staticmethod
    def _confidence_of(answer: Any) -> float:
        confidence = getattr(answer, "confidence", None)
        if confidence is not None:
            return float(confidence)
        probs = getattr(answer, "probabilities", None) or {}
        return float(max(probs.values())) if probs else 0.0

    def _to_judgment(self, result: Any) -> Judgment:
        answers = result.answers
        verdict_answer = answers[VERDICT_QUESTION_NAME]
        reasons: list[JudgmentReason] = []
        for name, _title in REMIT_QUESTIONS:
            answer = answers[name]
            confidence = self._confidence_of(answer)
            if answer.abstained:
                severity = "high"
                finding = (
                    "abstained: "
                    f"{answer.abstain_reason or 'below confidence floor'} "
                    f"(confidence {confidence:.2f})"
                )
            elif getattr(answer, "choice", None) == "concern":
                severity = "medium"
                finding = f"concern flagged (confidence {confidence:.2f})"
            else:
                severity = "low"
                finding = f"clear (confidence {confidence:.2f})"
            reasons.append(
                JudgmentReason(question=name, finding=finding, severity=severity)
            )
        if result.abstained:
            # §3: abstain -> ESCALATE (low confidence). An abstain is not
            # advice (advice_choice returns None for it and it must never be
            # fed to apply_jev_advice); the judge maps it to PAUSE here and
            # the caller escalates through the mechanical enforcer.
            decision = "PAUSE"
        else:
            decision = advice_choice(verdict_answer) or "CLEAR"
            if decision not in _DECISIONS:
                decision = "CLEAR"  # unreachable via strict parsing; fail-open
        return Judgment(
            judge=self.name,
            decision=decision,
            confidence=self._confidence_of(verdict_answer),
            reasons=tuple(reasons),
            trace_id=getattr(result, "trace_id", ""),
            backend=getattr(result, "backend", ""),
        )


def build_judge(
    *, client_factory: Any | None = None, config: JevConfig | None = None
) -> Judge:
    """Build the L2 judge.

    Always a :class:`JevJudge`; it degrades to a null judgment internally
    when no backend is configured (fail-safe, not fail-silent). Callers
    check the ``CallSiteGate`` (default-off) before invoking.
    """
    return JevJudge(client_factory=client_factory, config=config)


def judgment_advice(judgment: Judgment) -> str | None:
    """Translate a Judgment into the enforcer's advisory vocabulary.

    The judge speaks {CLEAR, PAUSE}; the mechanical enforcer
    (:func:`apply_jev_advice`) speaks {CLEAN, REPAIR, REJECT, ESCALATE}.
    PAUSE is the judge's only upward move and maps to ESCALATE advice;
    CLEAR maps to CLEAN; a null judgment carries no advice (None), so the
    deterministic verdict stands untouched.
    """
    if judgment.decision == "PAUSE":
        return "ESCALATE"
    if judgment.decision == "CLEAR":
        return "CLEAR"
    return None


# -- tiered invocation (§3) ----------------------------------------------------
# Judgment fires only when ALL hold: deterministic == CLEAN (asserted by
# evaluate()) AND (tier >= 1 OR novelty-flagged OR first-time author-pattern).
# Tier-0 trivial diffs skip; the verdict records judgment_skipped: "tier-0".


def judgment_earned(
    *,
    tier: int,
    novelty_flagged: bool = False,
    first_time_author: bool = False,
) -> tuple[bool, str | None]:
    """Decide whether judgment earns its keep for this artifact.

    Returns ``(earned, skip_reason)``; ``skip_reason`` is ``"tier-0"`` when
    tiering skips judgment, else None.
    """
    try:
        tier_num = int(tier)
    except (TypeError, ValueError):
        tier_num = 0
    if tier_num >= 1 or novelty_flagged or first_time_author:
        return True, None
    return False, "tier-0"


def maybe_evaluate(
    judge: Judge,
    artifact: Mapping[str, Any],
    deterministic_verdict: str,
    *,
    tier: int,
    novelty_flagged: bool = False,
    first_time_author: bool = False,
) -> Judgment:
    """Tiered entry point: skip (recorded) or delegate to the judge."""
    earned, skip_reason = judgment_earned(
        tier=tier,
        novelty_flagged=novelty_flagged,
        first_time_author=first_time_author,
    )
    if not earned:
        return Judgment(
            judge=getattr(judge, "name", "unknown"),
            decision=None,
            confidence=0.0,
            explicit_non_claims=("judgment skipped: tier-0 trivial diff",),
            skipped=skip_reason,
        )
    return judge.evaluate(artifact, deterministic_verdict)


# -- job bridge (until workstream A ships ReviewArtifact) -----------------------


def artifact_from_review_job(job: Any) -> dict[str, Any]:
    """Bridge a review-factory ReviewJob into the judge's artifact mapping.

    Reads defensively via getattr: the canonical ``ReviewArtifact`` (plan
    §2) replaces this bridge once it ships. Fields the job does not carry
    come through empty/None — never fabricated.
    """

    def _get(name: str, default: Any = None) -> Any:
        return getattr(job, name, default)

    def _seq(name: str) -> list[Any]:
        value = _get(name, ())
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            return [value]
        try:
            return list(value)
        except TypeError:
            return []

    intent = _get("review_intent") or _get("intent") or {}
    if not isinstance(intent, dict):
        intent = {}
    diff = _get("review_diff") or _get("diff") or {}
    if not isinstance(diff, dict):
        diff = {}
    raw_files = diff.get("files") or []
    files = [
        {
            "path": entry.get("path"),
            "change_type": entry.get("change_type"),
            "lines_added": entry.get("lines_added"),
            "lines_removed": entry.get("lines_removed"),
        }
        for entry in raw_files
        if isinstance(entry, dict)
    ]
    return {
        "artifact_id": str(
            _get("review_artifact_id")
            or _get("artifact_id")
            or _get("review_job_id")
            or ""
        ),
        "job_id": str(_get("review_job_id") or ""),
        "tier": _get("risk_tier", 1),
        "intent": {
            "plan_ref": intent.get("plan_ref"),
            "brief": intent.get("brief") or "",
            "goals": [
                goal for goal in (intent.get("goals") or []) if isinstance(goal, str)
            ],
        },
        "diff": {
            "base_tree": str(_get("base_tree") or diff.get("base_tree") or ""),
            "head_tree": str(_get("candidate_commit") or diff.get("head_tree") or ""),
            "files": files,
            "unified": str(_get("diff_unified") or diff.get("unified") or ""),
            "truncated": bool(
                _get("diff_truncated", False) or diff.get("truncated", False)
            ),
        },
        "checks": [
            {"name": entry.get("name"), "exit_code": entry.get("exit_code")}
            for entry in _seq("verification_checks")
            if isinstance(entry, dict)
        ],
        "concurrent_artifacts": _seq("concurrent_artifacts"),
        "novelty_flagged": bool(_get("novelty_flagged", False)),
        "first_time_author": bool(_get("first_time_author", False)),
    }


__all__ = [
    "JUDGMENT_CALL_SITE",
    "VERDICT_QUESTION_NAME",
    "Judge",
    "Judgment",
    "JudgmentReason",
    "JevJudge",
    "NullJudge",
    "artifact_from_review_job",
    "build_judge",
    "judgment_advice",
    "judgment_earned",
    "maybe_evaluate",
]
