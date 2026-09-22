"""Typed failure diagnosis (Jev #28) — shadow mode, advisory only.

When the watchdog trips or a deploy fails: infra vs. code vs. flake, as a
typed answer that routes the response. Diagnosis only — the response actions
stay behind their own deterministic gates.

Pipeline (deterministic rules own the happy path; Jev owns the gray):
  1. Versioned infra-signature rules (``failure_diagnosis_rules_v1.yaml``).
     A match short-circuits: Jev is NEVER consulted.
  2. Flake evidence from the ``test_history`` data-in slot (same commit SHA
     with both a pass and a fail, no code change between) -> deterministic
     ``flake`` verdict, Jev never consulted.
  3. Otherwise, one gated ``DecisionClient.decide()`` call classifies what
     the rules can't (``CallSiteGate("failure-diagnosis")``; master +
     per-site env, both default off, fail-closed).
  4. No-downgrade: a deterministic verdict is final. Jev advice is recorded
     in the audit signal regardless; it never overrides a deterministic
     verdict.

Shadow mode: every ``diagnose()`` emits exactly one JSONL audit signal and
takes no action. This module imports ``prismatic.jev`` only — never the
watchdog response paths, the repair dispatcher, or the deploy receiver.
There is no response-action execution path here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from prismatic.jev import CallSiteGate, Choice, DecisionClient, DecisionError, Noul

logger = logging.getLogger("prismatic.review_factory.failure_diagnosis")

#: The exact, ordered option set for the Jev root-cause question.
ROOT_CAUSE_OPTIONS = ("infra", "code", "flake", "unknown")

CALL_SITE = "failure-diagnosis"

#: Recommendation only — nothing in this module ever dispatches these.
CHANNEL_FOR_VERDICT = {
    "infra": "infra-response",
    "code": "code-repair",
    "flake": "retry-flake",
    "unknown": "needs-human",
}

SHADOW_MARKER = "shadow — no action taken"

AUDIT_FILENAME = "failure-diagnosis-shadow.jsonl"

_DEFAULT_AUDIT_PATH = Path.home() / ".prismatic" / "audit" / AUDIT_FILENAME

_MAX_ERROR_TEXT_CHARS = 4000

_JEV_NOT_CONSULTED = "not_consulted"
_JEV_GATE_CLOSED = "gate_closed"
_JEV_ERRORED = "errored"
_JEV_ADVISED = "advised"


class FailureDiagnosisConfigError(Exception):
    """The versioned rules file is malformed.

    Raised at load time, fail-closed: diagnosis never runs against a
    misread policy.
    """


@dataclass
class InfraRule:
    """One compiled infra-signature rule from the versioned YAML."""

    name: str
    description: str
    error_text_pattern: re.Pattern[str]
    error_classes: tuple[str, ...]


@dataclass(frozen=True)
class DiagnosisInput:
    """One failure to diagnose (event data in — no polling)."""

    failure_kind: str  # "watchdog-trip" | "deploy-failure" | "ci-failure"
    error_text: str = ""
    error_classes: tuple[str, ...] = ()
    commit_sha: str = ""
    #: (commit_sha, outcome) pairs; outcome is "pass" or "fail".
    test_history: tuple[tuple[str, str], ...] = ()
    source: str = ""  # e.g. workflow name + run id


@dataclass(frozen=True)
class DiagnosisResult:
    """Shadow diagnosis outcome. ``action_taken`` is always False."""

    failure_kind: str
    source: str
    deterministic_verdict: str | None
    deterministic_evidence: str
    jev_status: str  # not_consulted | gate_closed | errored | advised
    jev_advice: dict[str, Any] | None
    jev_backend: str | None
    jev_latency_ms: float | None
    final_verdict: str
    recommended_channel: str
    action_taken: bool = False
    policy_version: str | None = None
    invalid_rules: tuple[str, ...] = ()


def load_diagnosis_rules(path: str | Path) -> tuple[str, list[InfraRule], list[str]]:
    """Load and compile the versioned infra-signature rules.

    Returns ``(policy_version, rules, invalid_rules)``. A rule whose regex
    does not compile is rejected (recorded in ``invalid_rules``) — it can
    never match silently. Malformed config raises
    ``FailureDiagnosisConfigError`` (fail-closed); a missing file yields
    empty rules.
    """
    path = Path(path)
    if not path.exists():
        return ("missing", [], [])

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FailureDiagnosisConfigError(
            f"cannot parse rules file {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise FailureDiagnosisConfigError(
            f"rules file {path}: expected a mapping, got {type(raw).__name__}"
        )

    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise FailureDiagnosisConfigError(f"rules file {path}: missing 'version'")
    signatures = raw.get("infra_signatures")
    if not isinstance(signatures, list):
        raise FailureDiagnosisConfigError(
            f"rules file {path}: 'infra_signatures' must be a list"
        )

    rules: list[InfraRule] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for idx, entry in enumerate(signatures):
        where = f"rules file {path}: infra_signatures[{idx}]"
        if not isinstance(entry, dict):
            raise FailureDiagnosisConfigError(f"{where}: expected a mapping")
        name = entry.get("name")
        pattern_text = entry.get("error_text_regex")
        if not isinstance(name, str) or not name:
            raise FailureDiagnosisConfigError(f"{where}: missing 'name'")
        if name in seen:
            raise FailureDiagnosisConfigError(f"{where}: duplicate rule name {name!r}")
        seen.add(name)
        if not isinstance(pattern_text, str) or not pattern_text:
            raise FailureDiagnosisConfigError(f"{where}: missing 'error_text_regex'")
        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            # Fail-closed per rule: a bad regex never matches silently.
            logger.warning("%s: rejecting rule %r: bad regex (%s)", where, name, exc)
            invalid.append(name)
            continue
        classes = entry.get("error_classes", [])
        if not isinstance(classes, list) or not all(
            isinstance(c, str) for c in classes
        ):
            raise FailureDiagnosisConfigError(
                f"{where}: 'error_classes' must be a list of strings"
            )
        rules.append(
            InfraRule(
                name=name,
                description=str(entry.get("description", "")),
                error_text_pattern=pattern,
                error_classes=tuple(c.lower() for c in classes),
            )
        )
    return (version, rules, invalid)


class FailureDiagnosis:
    """Typed failure diagnoser — deterministic rules first, gated Jev second.

    Shadow mode only: ``diagnose()`` returns a verdict and appends exactly
    one audit signal. It never acts on the verdict.
    """

    def __init__(
        self,
        rules_path: str | Path | None = None,
        audit_path: str | Path | None = None,
        client: DecisionClient | None = None,
    ) -> None:
        if rules_path is None:
            rules_path = (
                Path(__file__).resolve().parent
                / "spec"
                / "failure_diagnosis_rules_v1.yaml"
            )
        self._policy_version, self._rules, self._invalid_rules = load_diagnosis_rules(
            rules_path
        )
        self._audit_path = Path(audit_path) if audit_path else _DEFAULT_AUDIT_PATH
        self._client = client
        self._gate = CallSiteGate(CALL_SITE)

    # ── public API ────────────────────────────────────────────────────

    def diagnose(self, failure: DiagnosisInput) -> DiagnosisResult:
        """Diagnose one failure; emit exactly one audit signal; take no action."""
        det_verdict, det_evidence = self._deterministic_verdict(failure)

        gate_allowed = self._gate.allow()
        jev_status = _JEV_NOT_CONSULTED
        jev_advice: dict[str, Any] | None = None
        jev_backend: str | None = None
        jev_latency_ms: float | None = None

        if det_verdict is None and gate_allowed:
            jev_status, jev_advice, jev_backend, jev_latency_ms = self._consult_jev(
                failure
            )
        elif det_verdict is None:
            jev_status = _JEV_GATE_CLOSED

        # No-downgrade: a deterministic verdict is final. With no
        # deterministic verdict, the Jev advice stands alone as shadow advice.
        if det_verdict is not None:
            final_verdict = det_verdict
        elif jev_status == _JEV_ADVISED and jev_advice is not None:
            final_verdict = str(jev_advice["choice"])
        else:
            final_verdict = "unknown"

        result = DiagnosisResult(
            failure_kind=failure.failure_kind,
            source=failure.source,
            deterministic_verdict=det_verdict,
            deterministic_evidence=det_evidence,
            jev_status=jev_status,
            jev_advice=jev_advice,
            jev_backend=jev_backend,
            jev_latency_ms=jev_latency_ms,
            final_verdict=final_verdict,
            recommended_channel=CHANNEL_FOR_VERDICT[final_verdict],
            action_taken=False,
            policy_version=self._policy_version,
            invalid_rules=tuple(self._invalid_rules),
        )
        self._append_audit(result, gate_allowed, failure)
        return result

    def diagnose_watchdog_trip(
        self,
        *,
        trip_reason: str,
        trip_source: str,
        commit_sha: str = "",
        error_text: str = "",
        error_classes: tuple[str, ...] = (),
        test_history: tuple[tuple[str, str], ...] = (),
    ) -> DiagnosisResult:
        """Data-in entry point: diagnose one watchdog trip event."""
        return self.diagnose(
            DiagnosisInput(
                failure_kind="watchdog-trip",
                error_text=error_text or trip_reason,
                error_classes=error_classes,
                commit_sha=commit_sha,
                test_history=test_history,
                source=trip_source,
            )
        )

    def diagnose_deploy_failure(
        self,
        *,
        workflow_name: str,
        run_id: str,
        conclusion: str,
        error_text: str = "",
        error_classes: tuple[str, ...] = (),
        commit_sha: str = "",
        test_history: tuple[tuple[str, str], ...] = (),
    ) -> DiagnosisResult:
        """Data-in entry point: diagnose one failed deploy workflow run."""
        return self.diagnose(
            DiagnosisInput(
                failure_kind="deploy-failure",
                error_text=error_text,
                error_classes=error_classes,
                commit_sha=commit_sha,
                test_history=test_history,
                source=f"{workflow_name} run {run_id} ({conclusion})",
            )
        )

    # ── internals ─────────────────────────────────────────────────────

    def _deterministic_verdict(self, failure: DiagnosisInput) -> tuple[str | None, str]:
        """Infra-signature match, then flake evidence. Never consults Jev."""
        lowered_classes = {c.lower() for c in failure.error_classes}
        for rule in self._rules:
            if rule.error_text_pattern.search(failure.error_text):
                return ("infra", f"infra_signature:{rule.name}")
            if rule.error_classes and lowered_classes & set(rule.error_classes):
                return ("infra", f"infra_signature:{rule.name}")

        outcomes: dict[str, set[str]] = {}
        for sha, outcome in failure.test_history:
            outcomes.setdefault(sha, set()).add(outcome)
        for sha, seen in outcomes.items():
            if "pass" in seen and "fail" in seen:
                return ("flake", f"flake_history:sha={sha}")
        return (None, "")

    def _consult_jev(
        self, failure: DiagnosisInput
    ) -> tuple[str, dict[str, Any] | None, str | None, float | None]:
        """One decide() call: Choice + confidence Noul, same billed call."""
        client = self._client or DecisionClient()
        state = {
            "failure_kind": failure.failure_kind,
            "source": failure.source,
            "commit_sha": failure.commit_sha,
            "error_text": failure.error_text[:_MAX_ERROR_TEXT_CHARS],
            "error_classes": list(failure.error_classes),
            "test_history": [
                f"{sha}:{outcome}" for sha, outcome in failure.test_history
            ],
        }
        questions = [
            Choice(
                "root_cause",
                "What is the root cause of this failure?",
                options=list(ROOT_CAUSE_OPTIONS),
            ),
            Noul("confident", "How confident are you in this root-cause choice?"),
        ]
        try:
            outcome = client.decide(state, questions)
        except DecisionError as exc:
            logger.warning(
                "Jev decide() failed (%s); failing closed", type(exc).__name__
            )
            return (_JEV_ERRORED, None, None, None)

        choice_answer = outcome.answers["root_cause"]
        noul_answer = outcome.answers.get("confident")
        advice: dict[str, Any] = {
            "choice": choice_answer.choice,
            "probabilities": dict(choice_answer.probabilities),
            "confidence": choice_answer.confidence,
            "noul_probability": (
                noul_answer.probability if noul_answer is not None else None
            ),
            "noul_confidence": (
                noul_answer.confidence if noul_answer is not None else None
            ),
        }
        return (_JEV_ADVISED, advice, outcome.backend, round(outcome.latency_ms, 2))

    def _append_audit(
        self, result: DiagnosisResult, gate_allowed: bool, failure: DiagnosisInput
    ) -> None:
        """Exactly one JSONL signal per diagnose(). Write failure never
        changes the verdict."""
        row = {
            "component": "failure-diagnosis",
            "ts": datetime.now(timezone.utc).isoformat(),
            "policy_version": result.policy_version,
            "invalid_rules": list(result.invalid_rules),
            "failure_kind": result.failure_kind,
            "source": result.source,
            "commit_sha": failure.commit_sha,
            "deterministic_verdict": result.deterministic_verdict,
            "deterministic_evidence": result.deterministic_evidence,
            "gate": {"site": CALL_SITE, "allowed": gate_allowed},
            "jev_status": result.jev_status,
            "jev_advice": result.jev_advice,
            "jev_backend": result.jev_backend,
            "jev_latency_ms": result.jev_latency_ms,
            "final_verdict": result.final_verdict,
            "recommended_channel": result.recommended_channel,
            "action_taken": False,
            "shadow": SHADOW_MARKER,
        }
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            # Audit is observe-only: a failed write never changes the verdict.
            logger.warning(
                "failure-diagnosis audit write failed; verdict unchanged",
                exc_info=True,
            )


#: Kept for module-level introspection; the class above is the API.
__all__ = [
    "CALL_SITE",
    "CHANNEL_FOR_VERDICT",
    "ROOT_CAUSE_OPTIONS",
    "SHADOW_MARKER",
    "DiagnosisInput",
    "DiagnosisResult",
    "FailureDiagnosis",
    "FailureDiagnosisConfigError",
    "InfraRule",
    "load_diagnosis_rules",
]
