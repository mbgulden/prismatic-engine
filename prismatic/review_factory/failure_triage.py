"""Failure triage: repair vs. retry vs. reject vs. escalate (roadmap chunk 5).

The flagship exception-path Jev integration (swarmjev spec section 7,
item 26). When CI fails or a deploy fails, deterministic rules decide FIRST:

- (a) known-transient error list (versioned config,
  ``spec/triage_transients_v1.yaml``) — a match means "just retry";
- (b) flaky-test statistics — fail-then-pass (or pass-then-fail) with no code
  change is a flake, no judgment needed — also "just retry".

What the rules can't classify goes to Jev: a typed ``Choice`` question over
exactly {repair, retry, reject, escalate} (+ optional ``Noul`` confidence),
asked via ``prismatic.jev.DecisionClient`` in a single ``decide()`` call.

SHADOW MODE ONLY. Jev decides; the engine IGNORES the decision; everything
is logged. There is no repair/retry/reject/escalate execution path in this
module — it never imports the repair dispatcher
(``ReviewQueue.dispatch_repair_task``) and never calls it. The Jev call site
is individually gated via ``CallSiteGate("failure-triage")`` (master
``SWARMJEV_ENABLED`` + ``SWARMJEV_CALLSITE_FAILURE_TRIAGE_ENABLED``, both
default-off, fail-closed). Jev may advise escalate but NEVER downgrades a
deterministic verdict — combination routes through ``apply_jev_advice``.

Every ``triage()`` emits exactly one audit signal as a JSONL row, component
``failure-triage``, carrying the deterministic outcome, the Jev advice, and
the ``"shadow \\u2014 no action taken"`` marker. No signal, no triage.

Event-based: ``triage_ci_failure()`` consumes GitHub ``workflow_run``
completion events (failed jobs supplied by the caller); ``triage_deploy_failure()``
is the hook signature for the deploy path — provided, NOT wired into the
existing deploy receiver (wiring would edit an existing file; that is a
future step on Michael's explicit word). No polling anywhere in this module.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _HAS_YAML = False

from prismatic.jev.client import DecisionClient
from prismatic.jev.errors import DecisionError
from prismatic.jev.gates import (
    VERDICT_ESCALATE,
    VERDICT_REJECT,
    VERDICT_REPAIR,
    CallSiteGate,
    apply_jev_advice,
)
from prismatic.jev.questions import Choice, ChoiceAnswer, Noul, NoulAnswer

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Verdict vocabulary (the Jev question options, exactly these four)
# ─────────────────────────────────────────────────────────────────────

TRIAGE_REPAIR = "repair"  # dispatch a repair attempt
TRIAGE_RETRY = "retry"  # rerun once: known-transient or flaky
TRIAGE_REJECT = "reject"  # reject the candidate
TRIAGE_ESCALATE = "escalate"  # pause for a human

TRIAGE_VERDICTS = (TRIAGE_REPAIR, TRIAGE_RETRY, TRIAGE_REJECT, TRIAGE_ESCALATE)

# Mapping onto the no-downgrade enforcer's vocabulary. "retry" maps to
# REPAIR (a fix-forward action): the point of the mapping is the invariant,
# not the words — deterministic verdicts are final, Jev advice is recorded
# but never overrides. No triage verdict maps to CLEAN, so the enforcer's
# only upward move (CLEAN -> ESCALATE) never fires from a deterministic
# verdict; combine_verdict() always returns the deterministic word.
_TRIAGE_TO_ENFORCER = {
    TRIAGE_RETRY: VERDICT_REPAIR,
    TRIAGE_REPAIR: VERDICT_REPAIR,
    TRIAGE_REJECT: VERDICT_REJECT,
    TRIAGE_ESCALATE: VERDICT_ESCALATE,
}

_JEV_TO_TRIAGE = {
    "repair": TRIAGE_REPAIR,
    "retry": TRIAGE_RETRY,
    "reject": TRIAGE_REJECT,
    "escalate": TRIAGE_ESCALATE,
}

GATE_SITE = "failure-triage"

SPEC_DIR = Path(__file__).resolve().parent / "spec"
DEFAULT_TRANSIENTS_FILE = SPEC_DIR / "triage_transients_v1.yaml"
DEFAULT_AUDIT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/failure-triage-shadow.jsonl")
)

SHADOW_MARKER = "shadow \u2014 no action taken"

STATE_TRIAGED = "triaged"
STATE_INVALID = "invalid"
STATE_DISABLED = "disabled"  # reserved: the Jev call site gate, not triage()

JEV_NOT_CONSULTED = "not_consulted"  # deterministic verdict short-circuited Jev
JEV_GATE_CLOSED = "gate_closed"  # call site gate refused the Jev call
JEV_ADVISED = "advised"  # Jev answered; advice recorded, ignored
JEV_ERRORED = "errored"  # Jev call raised; fail-closed, no action


class TriageConfigError(Exception):
    """Raised when the transient list cannot be loaded.

    Fail-closed: a triager built on a malformed config never evaluates
    (triage() returns state "invalid", logged).
    """


# ─────────────────────────────────────────────────────────────────────
# Versioned known-transient config
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TransientRule:
    """One known-transient signature: match -> deterministic "retry"."""

    name: str
    description: str
    patterns: tuple[str, ...]
    error_classes: tuple[str, ...] = ()

    def matches(self, error_text: str, error_classes: tuple[str, ...]) -> bool:
        for cls in self.error_classes:
            if cls in error_classes:
                return True
        for pattern in self.patterns:
            if re.search(pattern, error_text, re.IGNORECASE):
                return True
        return False


@dataclass(frozen=True)
class TransientPolicy:
    """The versioned known-transient list. Pure data — no power.

    A missing file means "no known transients" (fail-closed: match nothing,
    never invent a match). A malformed file raises TriageConfigError.
    """

    version: str
    rules: tuple[TransientRule, ...]


def load_transient_policy(path: Optional[Path | str] = None) -> TransientPolicy:
    """Load the versioned transient list, fail-closed."""
    path = Path(path) if path is not None else DEFAULT_TRANSIENTS_FILE
    if not path.exists():
        return TransientPolicy(version="missing", rules=())
    if not _HAS_YAML:
        raise TriageConfigError("PyYAML is required to load the transient policy")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise TriageConfigError(
            f"cannot parse transient policy file {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise TriageConfigError("transient policy root must be a mapping")
    raw_rules = data.get("transients", []) or []
    if not isinstance(raw_rules, list):
        raise TriageConfigError("transients must be a list")
    rules: list[TransientRule] = []
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise TriageConfigError(f"transients[{i}] must be a mapping")
        name = raw.get("name", "")
        if not isinstance(name, str) or not name:
            raise TriageConfigError(f"transients[{i}].name must be a non-empty string")
        patterns = raw.get("patterns", []) or []
        if not isinstance(patterns, list) or not all(
            isinstance(p, str) and p for p in patterns
        ):
            raise TriageConfigError(
                f"transients[{i}].patterns must be a list of non-empty strings"
            )
        for p in patterns:
            try:
                re.compile(p)
            except re.error as exc:
                raise TriageConfigError(
                    f"transients[{i}].patterns: bad regex {p!r}: {exc}"
                ) from exc
        classes = raw.get("error_classes", []) or []
        if not isinstance(classes, list) or not all(
            isinstance(c, str) for c in classes
        ):
            raise TriageConfigError(
                f"transients[{i}].error_classes must be a list of strings"
            )
        rules.append(
            TransientRule(
                name=name,
                description=str(raw.get("description", "")),
                patterns=tuple(patterns),
                error_classes=tuple(classes),
            )
        )
    names = [r.name for r in rules]
    if len(set(names)) != len(names):
        raise TriageConfigError("transient rule names must be unique")
    return TransientPolicy(
        version=str(data.get("version", "unknown")), rules=tuple(rules)
    )


# ─────────────────────────────────────────────────────────────────────
# Input + result models (pure data)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AttemptOutcome:
    """One prior attempt of the same test/job: commit SHA + pass/fail."""

    commit_sha: str
    outcome: str  # "pass" | "fail"


@dataclass(frozen=True)
class FailureInput:
    """One failure to triage. All evidence is caller-supplied data.

    - ``error_text``: free-form failure output (CI step logs, deploy error).
    - ``error_classes``: caller-assigned error class labels, if any.
    - ``test_history``: prior attempts of the same test/job, newest last.
      A same-commit pass+fail pair is a flake — counting, not judgment.
      ``None`` means history is unavailable (never "unknown", never a trip).
    """

    failure_id: str
    source: str  # "ci" | "deploy"
    error_text: str = ""
    error_classes: tuple[str, ...] = ()
    test_name: str = ""
    commit_sha: str = ""
    test_history: Optional[tuple[AttemptOutcome, ...]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JevAdvice:
    """What Jev said, recorded for the shadow audit. Never acted on."""

    status: str  # not_consulted | gate_closed | advised | errored
    choice: Optional[str] = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: Optional[float] = None
    backend: str = ""
    latency_ms: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class TriageResult:
    """The triage answer for one failure. Advisory only — shadow mode."""

    state: str  # triaged | invalid
    failure_id: str = ""
    deterministic_verdict: Optional[str] = None  # retry | None
    deterministic_evidence: str = ""
    jev: JevAdvice = field(default_factory=lambda: JevAdvice(status=JEV_NOT_CONSULTED))
    final_verdict: Optional[str] = None  # post-enforcer; shadow only
    gate_allowed: bool = False
    policy_version: str = "unknown"
    action_taken: bool = False  # ALWAYS False in shadow mode
    shadow: bool = True


def _validate_input(failure: FailureInput) -> Optional[str]:
    if not isinstance(failure.failure_id, str) or not failure.failure_id:
        return "failure_id must be a non-empty string"
    if failure.source not in ("ci", "deploy"):
        return f"source must be 'ci' or 'deploy', got {failure.source!r}"
    if not isinstance(failure.error_text, str):
        return "error_text must be a string"
    if not isinstance(failure.error_classes, (tuple, list)):
        return "error_classes must be a tuple/list of strings"
    if any(not isinstance(c, str) for c in failure.error_classes):
        return "error_classes must be a tuple/list of strings"
    if failure.test_history is not None:
        if not isinstance(failure.test_history, (tuple, list)):
            return "test_history must be a tuple/list of AttemptOutcome or None"
        for a in failure.test_history:
            if not isinstance(a, AttemptOutcome):
                return "test_history items must be AttemptOutcome"
            if a.outcome not in ("pass", "fail"):
                return f"AttemptOutcome.outcome must be 'pass'/'fail': {a.outcome!r}"
            if not isinstance(a.commit_sha, str) or not a.commit_sha:
                return "AttemptOutcome.commit_sha must be a non-empty string"
    return None


def _match_transient(
    policy: TransientPolicy, failure: FailureInput
) -> Optional[TransientRule]:
    """First deterministic rule: known-transient list. Pure."""
    error_text = failure.error_text or ""
    error_classes = tuple(failure.error_classes)
    for rule in policy.rules:
        if rule.matches(error_text, error_classes):
            return rule
    return None


def _is_flaky(failure: FailureInput) -> Optional[str]:
    """Second deterministic rule: flaky-test statistics. Pure.

    Fail-then-pass (or pass-then-fail) with no code change — same commit
    SHA showing both outcomes — is a flake. Counting beats a model.
    Returns evidence text, or None when history is unavailable or clean.
    """
    history = failure.test_history
    if not history:
        return None  # unavailable (None) or empty: never a flake
    by_sha: dict[str, set[str]] = {}
    for attempt in history:
        by_sha.setdefault(attempt.commit_sha, set()).add(attempt.outcome)
    flaky_shas = sorted(sha for sha, outs in by_sha.items() if len(outs) > 1)
    if not flaky_shas:
        return None
    return (
        "flake: same commit showed both pass and fail "
        f"(no code change): {', '.join(flaky_shas)}"
    )


def combine_verdict(
    deterministic: Optional[str], jev_choice: Optional[str]
) -> Optional[str]:
    """Combine a deterministic verdict with Jev's advisory choice.

    Routes through ``apply_jev_advice`` — the mechanical no-downgrade
    enforcer. A deterministic verdict is final: Jev advice is recorded in
    the audit signal but never overrides it. With no deterministic verdict,
    the Jev advice stands alone (still shadow: never acted on).
    """
    if deterministic is None:
        if jev_choice is None:
            return None
        key = str(jev_choice).strip().lower()
        if key not in _JEV_TO_TRIAGE:
            return None  # unrecognized advice is not a verdict
        return _JEV_TO_TRIAGE[key]
    det_key = str(deterministic).strip().lower()
    if det_key not in _TRIAGE_TO_ENFORCER:
        raise DecisionError(f"unknown deterministic triage verdict: {deterministic!r}")
    det_mapped = _TRIAGE_TO_ENFORCER[det_key]
    jev_mapped: Optional[str] = None
    if jev_choice is not None:
        # Unrecognized Jev advice maps to None: the enforcer then ignores
        # it and the deterministic verdict stands (fail-closed direction).
        jev_mapped = _TRIAGE_TO_ENFORCER.get(
            _JEV_TO_TRIAGE.get(str(jev_choice).strip().lower(), "")
        )
    final_mapped = apply_jev_advice(det_mapped, jev_mapped)
    # Back-map: the enforcer returns the mapped deterministic verdict or
    # ESCALATE-from-CLEAN (impossible here — no triage verdict maps to
    # CLEAN). Back-mapping REPAIR recovers the original deterministic word.
    if final_mapped == VERDICT_REJECT:
        return TRIAGE_REJECT
    if final_mapped == VERDICT_ESCALATE:
        return TRIAGE_ESCALATE
    return det_key  # VERDICT_REPAIR -> the original deterministic word


def _triage_questions() -> tuple[Choice, Noul]:
    return (
        Choice(
            "triage_verdict",
            "A CI check or deploy just failed. Which response is correct? "
            "repair = dispatch a repair attempt; retry = rerun once (transient "
            "or flake); reject = fail the candidate; escalate = pause for a human.",
            options=list(TRIAGE_VERDICTS),
        ),
        Noul(
            "confident",
            "How confident are you in this triage choice? 1 = certain, 0 = guessing.",
        ),
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# The triager (shadow mode)
# ─────────────────────────────────────────────────────────────────────


class FailureTriage:
    """Deterministic-first failure triage with shadow Jev advice.

    ``triage()`` runs the deterministic rules, consults Jev only for what
    the rules can't classify and only when ``CallSiteGate("failure-triage")``
    allows, routes any combination through the no-downgrade enforcer, and
    emits exactly one shadow audit signal. It never dispatches, retries,
    repairs, rejects, or escalates anything — shadow mode is observe + log.
    """

    def __init__(
        self,
        policy_path: Optional[Path | str] = None,
        *,
        audit_log: Optional[Path | str] = None,
        now_fn: Any = None,
        client_factory: Optional[Callable[[], DecisionClient]] = None,
    ) -> None:
        try:
            self.policy = load_transient_policy(policy_path)
            self._policy_error: Optional[str] = None
        except TriageConfigError as exc:
            # Fail-closed: a malformed transient list never evaluates.
            self.policy = TransientPolicy(version="invalid", rules=())
            self._policy_error = str(exc)
        self.gate = CallSiteGate(GATE_SITE)
        self.audit_log = Path(audit_log) if audit_log is not None else DEFAULT_AUDIT_LOG
        self._now = now_fn or time.time
        self._client_factory = client_factory or DecisionClient

    # -- audit --------------------------------------------------------

    def _emit_audit(
        self, result: TriageResult, failure: FailureInput, note: str = ""
    ) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "failure-triage",
            "state": result.state,
            "failure_id": result.failure_id,
            "source": failure.source,
            "test_name": failure.test_name,
            "policy_version": result.policy_version,
            "deterministic_verdict": result.deterministic_verdict,
            "deterministic_evidence": result.deterministic_evidence,
            "jev": {
                "status": result.jev.status,
                "advice": result.jev.choice,
                "probabilities": dict(result.jev.probabilities),
                "confidence": result.jev.confidence,
                "backend": result.jev.backend,
                "latency_ms": round(result.jev.latency_ms, 2),
                "error": result.jev.error,
            },
            "final_verdict": result.final_verdict,
            "gate": {"site": GATE_SITE, "allowed": result.gate_allowed},
            "action_taken": result.action_taken,
            "shadow": result.shadow,
            "marker": SHADOW_MARKER,
            "note": note,
        }
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            # Audit write failure must not change the triage outcome.
            pass

    # -- Jev consultation (gated, fail-closed) -------------------------

    def _consult_jev(self, failure: FailureInput, ask_confidence: bool) -> JevAdvice:
        questions: list = list(_triage_questions())
        if not ask_confidence:
            questions = questions[:1]
        client = self._client_factory()
        try:
            decided = client.decide(
                state={
                    "failure_id": failure.failure_id,
                    "source": failure.source,
                    "test_name": failure.test_name,
                    "commit_sha": failure.commit_sha,
                    "error_classes": list(failure.error_classes),
                    # Error text is hashed, not stored: it may carry
                    # customer data; the audit keeps the shape, not content.
                    "error_text_len": len(failure.error_text or ""),
                    "history_available": failure.test_history is not None,
                },
                questions=questions,
            )
        except DecisionError as exc:
            # Fail-closed: a failed Jev call never invents a decision.
            # No credential or backend detail leaks into the record.
            # Watchdog metrics feed (Phase 0 observe-only): record the
            # failed Jev call. A feed failure must never break triage.
            try:
                from prismatic.review_factory.metrics_feed import record_jev_call

                record_jev_call(
                    call_site="failure_triage.FailureTriage._consult_jev",
                    ok=False,
                    error=type(exc).__name__,
                )
            except Exception:
                logger.warning("watchdog feed record_jev_call failed", exc_info=True)
            return JevAdvice(status=JEV_ERRORED, error=type(exc).__name__)
        choice_ans = decided.answers.get("triage_verdict")
        choice = choice_ans.choice if isinstance(choice_ans, ChoiceAnswer) else None
        probabilities = (
            dict(choice_ans.probabilities)
            if isinstance(choice_ans, ChoiceAnswer)
            else {}
        )
        confidence: Optional[float] = None
        noul_ans = decided.answers.get("confident")
        if isinstance(noul_ans, NoulAnswer):
            confidence = noul_ans.probability
        elif isinstance(choice_ans, ChoiceAnswer):
            confidence = choice_ans.confidence
        # Watchdog metrics feed (Phase 0 observe-only): record the
        # successful Jev call. A feed failure must never break triage.
        try:
            from prismatic.review_factory.metrics_feed import record_jev_call

            record_jev_call(
                call_site="failure_triage.FailureTriage._consult_jev",
                ok=True,
            )
        except Exception:
            logger.warning("watchdog feed record_jev_call failed", exc_info=True)
        return JevAdvice(
            status=JEV_ADVISED,
            choice=choice,
            probabilities=probabilities,
            confidence=confidence,
            backend=decided.backend,
            latency_ms=decided.latency_ms,
        )

    # -- triage --------------------------------------------------------

    def triage(
        self, failure: FailureInput, *, ask_confidence: bool = True
    ) -> TriageResult:
        """Triage one failure. Pure apart from the audit signal it emits.

        Order: policy health → input validity → deterministic rules
        (transient match, then flake stats; either short-circuits Jev) →
        gated Jev consult for the unclassified → no-downgrade combine →
        shadow audit. Nothing is ever dispatched.
        """
        # 1. Policy health. A malformed transient list never evaluates.
        if self._policy_error is not None:
            result = TriageResult(
                state=STATE_INVALID,
                failure_id=failure.failure_id
                if isinstance(failure.failure_id, str)
                else "",
                policy_version=self.policy.version,
            )
            self._emit_audit(
                result, failure, note=f"policy_error: {self._policy_error}"
            )
            return result

        # 2. Input validity. An untrustworthy input gets no verdict.
        invalid = _validate_input(failure)
        if invalid is not None:
            result = TriageResult(
                state=STATE_INVALID,
                failure_id=failure.failure_id
                if isinstance(failure.failure_id, str)
                else "",
                policy_version=self.policy.version,
            )
            self._emit_audit(result, failure, note=f"invalid_input: {invalid}")
            return result

        # 3. Deterministic rules first. Either match short-circuits Jev.
        deterministic: Optional[str] = None
        evidence = ""
        rule = _match_transient(self.policy, failure)
        if rule is not None:
            deterministic = TRIAGE_RETRY
            evidence = f"known-transient rule {rule.name!r}: {rule.description}"
        else:
            flake = _is_flaky(failure)
            if flake is not None:
                deterministic = TRIAGE_RETRY
                evidence = flake

        gate_allowed = False
        if deterministic is not None:
            jev = JevAdvice(status=JEV_NOT_CONSULTED)
        elif self.gate.allow():
            gate_allowed = True
            jev = self._consult_jev(failure, ask_confidence)
        else:
            jev = JevAdvice(status=JEV_GATE_CLOSED)

        # 4. No-downgrade combine (shadow: recorded, never acted on).
        final = combine_verdict(deterministic, jev.choice)

        result = TriageResult(
            state=STATE_TRIAGED,
            failure_id=failure.failure_id,
            deterministic_verdict=deterministic,
            deterministic_evidence=evidence,
            jev=jev,
            final_verdict=final,
            gate_allowed=gate_allowed,
            policy_version=self.policy.version,
            action_taken=False,
            shadow=True,
        )
        self._emit_audit(result, failure)
        return result


# ─────────────────────────────────────────────────────────────────────
# Event entry points (no polling)
# ─────────────────────────────────────────────────────────────────────


def triage_ci_failure(
    *,
    workflow_name: str,
    run_id: str,
    conclusion: str,
    failed_jobs: list[dict[str, Any]],
    triager: Optional[FailureTriage] = None,
) -> list[TriageResult]:
    """Triage CI failures from a GitHub workflow_run completion event.

    ``failed_jobs`` is caller-supplied data (job name, failed step names,
    log excerpt, head SHA) — this function performs no network I/O and no
    polling. One ``FailureInput`` per failed job; one shadow audit signal
    per triage. Returns the results; dispatches nothing.
    """
    triager = triager or FailureTriage()
    results: list[TriageResult] = []
    for job in failed_jobs:
        steps = job.get("failed_steps") or []
        error_text = "\n".join(
            str(s) for s in [job.get("log_excerpt", ""), *steps] if s
        )
        results.append(
            triager.triage(
                FailureInput(
                    failure_id=f"ci:{run_id}:{job.get('job_id', 'unknown')}",
                    source="ci",
                    error_text=error_text,
                    error_classes=tuple(job.get("error_classes") or ()),
                    test_name=str(job.get("name", "")),
                    commit_sha=str(job.get("head_sha", "")),
                    test_history=None,  # history store is a later chunk
                    metadata={
                        "workflow_name": workflow_name,
                        "run_id": run_id,
                        "conclusion": conclusion,
                    },
                )
            )
        )
    return results


def triage_deploy_failure(
    *,
    deploy_id: str,
    error_text: str,
    error_classes: tuple[str, ...] = (),
    triager: Optional[FailureTriage] = None,
) -> TriageResult:
    """Hook signature for deploy-failure triage.

    PROVIDED, NOT WIRED: calling this from the deploy receiver would edit
    an existing file, so the wiring is a future step on Michael's explicit
    word. When wired, a failed deploy gets the same deterministic-first,
    shadow-logged triage as CI failures. Dispatches nothing.
    """
    triager = triager or FailureTriage()
    return triager.triage(
        FailureInput(
            failure_id=f"deploy:{deploy_id}",
            source="deploy",
            error_text=error_text,
            error_classes=error_classes,
        )
    )
