"""Typed failure diagnosis (shadow mode) — Chunk 6 / Jev #28.

Classifies a failure event (CI failure, deploy failure, watchdog trip)
into a *typed* answer — infra vs. code vs. flake vs. deploy vs. unknown —
that routes the response. The routing table here is advisory: response
actions (retry, repair dispatch, deploy freeze, paging) keep their own
deterministic gates and are never invoked from this module.

Safety posture (build-sequence rules):

- Deterministic rules own the answer; Jev classifies only what the rules
  cannot (diagnosis ``unknown``), through the individually gated,
  default-off ``CallSiteGate("failure-diagnosis")``.
- A deterministic concrete diagnosis is immutable: Jev advice is recorded
  in the audit signal but can never rewrite it.
- Every ``diagnose()`` emits exactly one JSONL audit signal, shadow-only
  (``action_taken: False``); the module never imports the repair
  dispatcher, the watchdog halt path, or the deploy receiver.
- Fail-closed: malformed/missing config, Jev error, gate closed, audit
  write failure — none of these change or invent a diagnosis.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from prismatic.jev import (
    CallSiteGate,
    Choice,
    DecisionClient,
    DecisionError,
    Noul,
)

logger = logging.getLogger("prismatic.review_factory.failure_diagnosis")

CALL_SITE = "failure-diagnosis"
POLICY_VERSION = "diagnosis-signatures-v1"
AUDIT_COMPONENT = "failure-diagnosis"
SHADOW_MARKER = "shadow — no action taken"

_DEFAULT_AUDIT_PATH = os.path.expanduser(
    "~/.prismatic/audit/failure-diagnosis-shadow.jsonl"
)
_SPEC_PATH = Path(__file__).resolve().parent / "spec" / "diagnosis_signatures_v1.yaml"

# Bound the error text sent toward a Jev backend: enough for classification,
# small enough to stay a summary rather than a log dump.
_MAX_STATE_ERROR_CHARS = 4000


class DiagnosisConfigError(Exception):
    """Raised when the versioned signature policy cannot be loaded/parsed."""


class Diagnosis(str, Enum):
    INFRA = "infra"
    CODE = "code"
    FLAKE = "flake"
    DEPLOY = "deploy"
    UNKNOWN = "unknown"


class FailureSource(str, Enum):
    CI = "ci"
    DEPLOY = "deploy"
    WATCHDOG = "watchdog"


# Advisory routing table. "route" names the team/queue that should own the
# response; "retry_safe" and "page" describe the recommended response shape.
# Response actions keep their own deterministic gates — this table is a
# recommendation, never an instruction.
ROUTES: dict[Diagnosis, dict[str, Any]] = {
    Diagnosis.INFRA: {"route": "infra-owner", "retry_safe": True, "page": False},
    Diagnosis.CODE: {"route": "author", "retry_safe": False, "page": False},
    Diagnosis.FLAKE: {"route": "retry", "retry_safe": True, "page": False},
    Diagnosis.DEPLOY: {"route": "deploy-freeze", "retry_safe": False, "page": True},
    Diagnosis.UNKNOWN: {"route": "human", "retry_safe": False, "page": True},
}

_JEV_OPTIONS = ("infra", "code", "flake", "deploy")


@dataclass(frozen=True)
class AttemptOutcome:
    """One prior attempt of the same test/check at a given commit."""

    commit_sha: str
    outcome: str  # "pass" or "fail"


@dataclass(frozen=True)
class FailureInput:
    """Everything the diagnoser may look at. All fields optional except
    ``source``; absent data narrows what the rules can conclude (fail-closed
    toward ``unknown``)."""

    source: FailureSource
    error_text: str = ""
    error_classes: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    commit_sha: str = ""
    test_history: tuple[AttemptOutcome, ...] = ()
    metric_name: str = ""  # watchdog source only
    deploy_stage: str = ""  # deploy source only


@dataclass(frozen=True)
class DiagnosisResult:
    diagnosis: Diagnosis
    route: str
    retry_safe: bool
    page: bool
    matched_rule: str | None
    jev_status: str  # not_consulted | gate_closed | advised | errored
    jev_advice: dict[str, Any] | None
    audit_path: str
    action_taken: bool = False

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "component": AUDIT_COMPONENT,
            "policy_version": POLICY_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnosis": self.diagnosis.value,
            "route": self.route,
            "retry_safe": self.retry_safe,
            "page": self.page,
            "matched_rule": self.matched_rule,
            "jev_status": self.jev_status,
            "jev_advice": self.jev_advice,
            "action_taken": self.action_taken,
            "shadow": SHADOW_MARKER,
        }


@dataclass
class _CompiledRule:
    name: str
    diagnosis: Diagnosis
    patterns: list[re.Pattern[str]] = field(default_factory=list)


def _load_policy(spec_path: Path) -> tuple[list[_CompiledRule], dict[str, Diagnosis]]:
    """Load and validate the versioned signature policy.

    Raises DiagnosisConfigError on any problem: missing file, bad YAML,
    wrong version, bad regex, unknown diagnosis name. Fail-closed: a
    diagnoser that cannot load its policy must not guess.
    """
    try:
        raw = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DiagnosisConfigError(f"cannot read policy {spec_path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise DiagnosisConfigError(
            f"policy {spec_path} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DiagnosisConfigError(f"policy {spec_path}: top level must be a mapping")
    if data.get("version") != POLICY_VERSION:
        raise DiagnosisConfigError(
            f"policy {spec_path}: version {data.get('version')!r} != {POLICY_VERSION!r}"
        )

    rules: list[_CompiledRule] = []
    signatures = data.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise DiagnosisConfigError(
            f"policy {spec_path}: 'signatures' must be a non-empty list"
        )
    for entry in signatures:
        if not isinstance(entry, dict):
            raise DiagnosisConfigError(
                f"policy {spec_path}: signature entry must be a mapping"
            )
        name = entry.get("name")
        diag_raw = entry.get("diagnosis")
        patterns = entry.get("patterns")
        try:
            diagnosis = Diagnosis(str(diag_raw))
        except ValueError:
            raise DiagnosisConfigError(
                f"policy {spec_path}: rule {name!r} has unknown diagnosis {diag_raw!r}"
            ) from None
        if not name or not isinstance(patterns, list) or not patterns:
            raise DiagnosisConfigError(
                f"policy {spec_path}: rule {name!r} needs a name and non-empty patterns"
            )
        compiled: list[re.Pattern[str]] = []
        for pat in patterns:
            try:
                compiled.append(re.compile(str(pat), re.IGNORECASE))
            except re.error as exc:
                raise DiagnosisConfigError(
                    f"policy {spec_path}: rule {name!r} has bad regex {pat!r}: {exc}"
                ) from exc
        rules.append(
            _CompiledRule(name=str(name), diagnosis=diagnosis, patterns=compiled)
        )

    metric_map: dict[str, Diagnosis] = {}
    raw_map = data.get("watchdog_metric_map") or {}
    if not isinstance(raw_map, dict):
        raise DiagnosisConfigError(
            f"policy {spec_path}: 'watchdog_metric_map' must be a mapping"
        )
    for metric, diag_raw in raw_map.items():
        try:
            metric_map[str(metric)] = Diagnosis(str(diag_raw))
        except ValueError:
            raise DiagnosisConfigError(
                f"policy {spec_path}: metric {metric!r} has unknown diagnosis {diag_raw!r}"
            ) from None
    return rules, metric_map


class FailureDiagnoser:
    """Typed failure diagnosis. Construct once; call ``diagnose`` per event.

    ``audit_path`` may be overridden (tests). ``client_factory`` may inject
    a stub ``DecisionClient`` (tests); production uses the real client.
    """

    def __init__(
        self,
        spec_path: Path | str = _SPEC_PATH,
        audit_path: str = _DEFAULT_AUDIT_PATH,
        client_factory: Any = None,
    ) -> None:
        self._rules, self._metric_map = _load_policy(Path(spec_path))
        self._audit_path = audit_path
        self._client_factory = client_factory or DecisionClient
        self._gate = CallSiteGate(CALL_SITE)

    # -- deterministic rules -------------------------------------------

    def _match_signature(self, failure: FailureInput) -> _CompiledRule | None:
        haystacks = [failure.error_text, *failure.error_classes]
        for rule in self._rules:
            for pattern in rule.patterns:
                if any(pattern.search(h) for h in haystacks if h):
                    return rule
        return None

    @staticmethod
    def _is_flaky(failure: FailureInput) -> bool:
        """Same commit SHA with both a pass and a fail and no code change
        between → the test is flaky, the change is not at fault."""
        if not failure.commit_sha:
            return False
        outcomes = {
            a.outcome
            for a in failure.test_history
            if a.commit_sha == failure.commit_sha
        }
        return outcomes == {"pass", "fail"}

    def _deterministic(self, failure: FailureInput) -> tuple[Diagnosis, str | None]:
        """Return (diagnosis, matched_rule_name). UNKNOWN means the rules
        could not classify — the only case where Jev may be consulted."""
        if failure.source is FailureSource.DEPLOY:
            rule = self._match_signature(failure)
            if rule is not None and rule.diagnosis is Diagnosis.INFRA:
                return Diagnosis.INFRA, rule.name
            return Diagnosis.DEPLOY, None
        if failure.source is FailureSource.WATCHDOG:
            mapped = self._metric_map.get(failure.metric_name)
            if mapped is not None:
                rule_name = f"watchdog-metric:{failure.metric_name}"
                return mapped, rule_name
            return Diagnosis.UNKNOWN, None
        # FailureSource.CI
        rule = self._match_signature(failure)
        if rule is not None:
            return rule.diagnosis, rule.name
        if self._is_flaky(failure):
            return Diagnosis.FLAKE, "flaky-statistics"
        return Diagnosis.UNKNOWN, None

    # -- Jev (exception path only) -------------------------------------

    def _consult_jev(self, failure: FailureInput) -> tuple[str, dict[str, Any] | None]:
        """Consult Jev for an unclassifiable failure. Returns (status, advice).

        Never raises: every Jev failure mode maps to a status string and the
        caller keeps the deterministic diagnosis (fail-closed).
        """
        if not self._gate.allow():
            return "gate_closed", None
        state = {
            "source": failure.source.value,
            "metric_name": failure.metric_name,
            "deploy_stage": failure.deploy_stage,
            "failed_checks": list(failure.failed_checks),
            "error_classes": list(failure.error_classes),
            "error_text": failure.error_text[:_MAX_STATE_ERROR_CHARS],
        }
        prompt = (
            "A CI/deploy/watchdog failure could not be classified by "
            "deterministic rules. Given the failure state, which diagnosis "
            "best fits: 'infra' (platform, runner, network, or registry "
            "problem), 'code' (the change itself is broken), 'flake' "
            "(nondeterministic failure — a retry is the honest response), "
            "or 'deploy' (the deploy path failed, distinct from CI)? "
            "This is advisory only; it never triggers an action."
        )
        try:
            client = self._client_factory()
            result = client.decide(
                state,
                [
                    Choice("diagnosis_advice", prompt, options=list(_JEV_OPTIONS)),
                    Noul(
                        "confident",
                        "How confident are you in this diagnosis advice?",
                    ),
                ],
                on_error="raise",
            )
        except DecisionError as exc:
            logger.warning("Jev diagnosis call failed closed: %s", exc)
            return "errored", None
        except Exception as exc:  # fail-closed on anything unexpected
            logger.warning("Jev diagnosis call failed closed (unexpected): %r", exc)
            return "errored", None
        advice = result.to_audit_dict()
        advice["advisory_only"] = True
        return "advised", advice

    # -- public API ----------------------------------------------------

    def diagnose(self, failure: FailureInput) -> DiagnosisResult:
        diagnosis, matched_rule = self._deterministic(failure)

        jev_status = "not_consulted"
        jev_advice: dict[str, Any] | None = None
        if diagnosis is Diagnosis.UNKNOWN:
            jev_status, jev_advice = self._consult_jev(failure)

        route_info = ROUTES[diagnosis]
        result = DiagnosisResult(
            diagnosis=diagnosis,
            route=str(route_info["route"]),
            retry_safe=bool(route_info["retry_safe"]),
            page=bool(route_info["page"]),
            matched_rule=matched_rule,
            jev_status=jev_status,
            jev_advice=jev_advice,
            audit_path=self._audit_path,
            action_taken=False,
        )
        self._write_audit(result, failure)
        return result

    def _write_audit(self, result: DiagnosisResult, failure: FailureInput) -> None:
        """Append exactly one JSONL row. A write failure is logged and never
        changes the diagnosis (the result was already computed)."""
        row = result.to_audit_dict()
        row["failure"] = {
            "source": failure.source.value,
            "metric_name": failure.metric_name,
            "deploy_stage": failure.deploy_stage,
            "failed_checks": list(failure.failed_checks),
            "error_classes": list(failure.error_classes),
            "commit_sha": failure.commit_sha,
        }
        try:
            path = Path(self._audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError as exc:
            logger.warning("diagnosis audit write failed (verdict stands): %s", exc)


# -- event-based entry points (provided; wiring is a future step) --------


def diagnose_ci_failure(
    diagnoser: FailureDiagnoser,
    *,
    error_text: str = "",
    error_classes: tuple[str, ...] = (),
    failed_checks: tuple[str, ...] = (),
    commit_sha: str = "",
    test_history: tuple[AttemptOutcome, ...] = (),
) -> DiagnosisResult:
    """Build a CI-source FailureInput (e.g. from a workflow_run completion
    event) and diagnose it."""
    return diagnoser.diagnose(
        FailureInput(
            source=FailureSource.CI,
            error_text=error_text,
            error_classes=tuple(error_classes),
            failed_checks=tuple(failed_checks),
            commit_sha=commit_sha,
            test_history=tuple(test_history),
        )
    )


def diagnose_deploy_failure(
    diagnoser: FailureDiagnoser,
    *,
    error_text: str = "",
    error_classes: tuple[str, ...] = (),
    deploy_stage: str = "",
) -> DiagnosisResult:
    """Hook signature for the deploy path. Provided, not wired — wiring it
    into the existing receiver would edit an existing file."""
    return diagnoser.diagnose(
        FailureInput(
            source=FailureSource.DEPLOY,
            error_text=error_text,
            error_classes=tuple(error_classes),
            deploy_stage=deploy_stage,
        )
    )


def diagnose_watchdog_trip(
    diagnoser: FailureDiagnoser,
    *,
    metric_name: str,
    error_text: str = "",
) -> DiagnosisResult:
    """Hook for the deterministic watchdog (Chunk 2). Provided, not wired."""
    return diagnoser.diagnose(
        FailureInput(
            source=FailureSource.WATCHDOG,
            metric_name=metric_name,
            error_text=error_text,
        )
    )
