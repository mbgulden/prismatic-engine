"""Novelty quarantine routing (shadow mode) — Jev #27.

Standalone shadow router that answers "is this PR weird enough to hold?"
The deterministic novelty detector (``prismatic.review_factory.novelty``)
keeps its own verdicts; this module does NOT wire into it and never
quarantines, halts, pages, or gates anything.

Pipeline (mirrors the plan):

1. Deterministic rules first (``quarantine_routing_rules_v1.yaml``,
   versioned, never edited in place):
   - definitely-quarantine triggers (evaluated first, fail-closed order):
     ``migration-without-tests``, ``unknown-error-classes``,
     ``first-time-event-types``;
   - ``familiar-precedent``: enough precedent matches, no first-time event
     types, no unknown error classes, and every changed path matches a
     previously-seen path pattern → recommend ``proceed``.
2. Jev decides the gray zone only: no deterministic verdict AND
   ``CallSiteGate("novelty-quarantine-routing")`` allows (master
   ``SWARMJEV_ENABLED`` + per-site
   ``SWARMJEV_CALLSITE_NOVELTY_QUARANTINE_ROUTING_ENABLED``, both
   default-off, fail-closed). One ``DecisionClient.decide()`` call with
   ``Choice("route", ..., options=["proceed", "quarantine"])`` and an
   optional ``Noul("confident", ...)``.
3. No-downgrade: a deterministic verdict is final. Jev is never consulted
   on a deterministic path, so its advice cannot override one.
4. Shadow audit: every ``route()`` appends exactly one JSONL row to
   ``~/.prismatic/audit/novelty-quarantine-routing-shadow.jsonl`` with
   ``action_taken: false`` and the marker "shadow — no action taken".
   A write failure is logged and never changes the answer.

Safety: this module never imports the novelty detector's enforce path,
the quarantine registry, the watchdog halt path, the deploy receiver,
the merge executor, or the review queue — there is no action path to
accidentally call.
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

logger = logging.getLogger("prismatic.review_factory.quarantine_routing")

CALL_SITE = "novelty-quarantine-routing"
POLICY_VERSION = "quarantine-routing-rules-v1"
AUDIT_COMPONENT = "novelty-quarantine-routing"
SHADOW_MARKER = "shadow — no action taken"

_DEFAULT_AUDIT_PATH = os.path.expanduser(
    "~/.prismatic/audit/novelty-quarantine-routing-shadow.jsonl"
)
_SPEC_PATH = (
    Path(__file__).resolve().parent / "spec" / "quarantine_routing_rules_v1.yaml"
)

# Deterministic quarantine triggers, in fail-closed evaluation order. The
# policy file must declare exactly these names (unknown policy semantics
# are a config error).
_TRIGGER_MIGRATION_WITHOUT_TESTS = "migration-without-tests"
_TRIGGER_UNKNOWN_ERROR_CLASSES = "unknown-error-classes"
_TRIGGER_FIRST_TIME_EVENT_TYPES = "first-time-event-types"
_KNOWN_TRIGGER_NAMES = (
    _TRIGGER_MIGRATION_WITHOUT_TESTS,
    _TRIGGER_UNKNOWN_ERROR_CLASSES,
    _TRIGGER_FIRST_TIME_EVENT_TYPES,
)

_RULE_FAMILIAR = "familiar-precedent"
_RULE_MALFORMED = "malformed-candidate"

_JEV_CHOICE_NAME = "route"
_JEV_CONFIDENCE_NAME = "confident"
_JEV_OPTIONS = ("proceed", "quarantine")

# Bounds on state sent toward a Jev backend: a summary, never a dump.
_MAX_STATE_PATHS = 200
_MAX_STATE_VALUE_CHARS = 2000

_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _bound_str(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:_MAX_STATE_VALUE_CHARS]


class RouteVerdict(Enum):
    PROCEED = "proceed"
    QUARANTINE = "quarantine"


class QuarantineRoutingConfigError(ValueError):
    """The routing policy file is missing, malformed, or the wrong version."""


@dataclass(frozen=True)
class RoutingPolicy:
    """Validated, versioned routing policy (from the YAML spec file)."""

    version: str
    familiar_precedent_min: int
    familiar_path_patterns: tuple[re.Pattern[str], ...]
    quarantine_trigger_names: tuple[str, ...]


def _load_policy(spec_path: Path) -> RoutingPolicy:
    """Load and validate the versioned policy. Raises QuarantineRoutingConfigError."""
    where = f"policy {spec_path}"
    if not spec_path.is_file():
        raise QuarantineRoutingConfigError(f"{where}: not found")
    try:
        data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise QuarantineRoutingConfigError(f"{where}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise QuarantineRoutingConfigError(f"{where}: top-level mapping required")
    version = data.get("version")
    if version != POLICY_VERSION:
        raise QuarantineRoutingConfigError(
            f"{where}: version {version!r} != required {POLICY_VERSION!r}"
        )
    minimum = data.get("familiar_precedent_min")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        raise QuarantineRoutingConfigError(
            f"{where}: familiar_precedent_min must be a non-negative int"
        )
    raw_patterns = data.get("familiar_path_patterns")
    if not isinstance(raw_patterns, list) or not all(
        isinstance(p, str) and p for p in raw_patterns
    ):
        raise QuarantineRoutingConfigError(
            f"{where}: familiar_path_patterns must be a non-empty list of regex strings"
        )
    patterns: list[re.Pattern[str]] = []
    for raw in raw_patterns:
        try:
            patterns.append(re.compile(raw))
        except re.error as exc:
            raise QuarantineRoutingConfigError(
                f"{where}: bad path pattern {raw!r}: {exc}"
            ) from exc
    raw_rules = data.get("deterministic_quarantine_rules")
    if not isinstance(raw_rules, list):
        raise QuarantineRoutingConfigError(
            f"{where}: deterministic_quarantine_rules must be a list"
        )
    names: list[str] = []
    for rule in raw_rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("name"), str):
            raise QuarantineRoutingConfigError(
                f"{where}: each quarantine rule needs a string 'name'"
            )
        names.append(rule["name"])
    if len(set(names)) != len(names):
        raise QuarantineRoutingConfigError(f"{where}: duplicate quarantine rule names")
    if tuple(names) != _KNOWN_TRIGGER_NAMES:
        raise QuarantineRoutingConfigError(
            f"{where}: quarantine rules must be exactly {list(_KNOWN_TRIGGER_NAMES)}"
        )
    return RoutingPolicy(
        version=version,
        familiar_precedent_min=minimum,
        familiar_path_patterns=tuple(patterns),
        quarantine_trigger_names=tuple(names),
    )


@dataclass
class QuarantineCandidate:
    """Pure data in: everything the router needs, supplied by the caller."""

    candidate_id: str = ""
    head_sha: str = ""
    changed_paths: tuple[str, ...] = ()
    author_association: str = ""
    labels: tuple[str, ...] = ()
    novelty_context: dict[str, Any] = field(default_factory=dict)


def _normalize_candidate(raw: Any) -> QuarantineCandidate | None:
    """Normalize dict/QuarantineCandidate input. None = malformed (fail-closed)."""
    if isinstance(raw, QuarantineCandidate):
        return raw
    if raw is None or not isinstance(raw, dict):
        return None
    try:
        candidate_id = raw.get("candidate_id")
        head_sha = raw.get("head_sha", "")
        changed_paths = raw.get("changed_paths")
        author_association = raw.get("author_association", "")
        labels = raw.get("labels", [])
        context = raw.get("novelty_context")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return None
        if not isinstance(head_sha, str):
            return None
        if not isinstance(changed_paths, (list, tuple)) or not all(
            isinstance(p, str) for p in changed_paths
        ):
            return None
        if not isinstance(author_association, str):
            return None
        if not isinstance(labels, (list, tuple)) or not all(
            isinstance(item, str) for item in labels
        ):
            return None
        if not isinstance(context, dict):
            return None
        normalized_context = _normalize_context(context)
        if normalized_context is None:
            return None
        return QuarantineCandidate(
            candidate_id=candidate_id,
            head_sha=head_sha,
            changed_paths=tuple(changed_paths),
            author_association=author_association,
            labels=tuple(labels),
            novelty_context=normalized_context,
        )
    except Exception:  # fail-closed: anything unexpected is malformed
        return None


def _normalize_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Validate novelty_context field types. None = malformed."""
    try:
        precedent = context.get("precedent_matches", 0)
        first_time = context.get("first_time_event_types", [])
        unknown_errors = context.get("unknown_error_classes", [])
        has_migration = context.get("has_migration", False)
        has_test_change = context.get("has_test_change", False)
        if (
            isinstance(precedent, bool)
            or not isinstance(precedent, int)
            or precedent < 0
        ):
            return None
        if not isinstance(first_time, (list, tuple)) or not all(
            isinstance(item, str) for item in first_time
        ):
            return None
        if not isinstance(unknown_errors, (list, tuple)) or not all(
            isinstance(item, str) for item in unknown_errors
        ):
            return None
        if not isinstance(has_migration, bool) or not isinstance(has_test_change, bool):
            return None
        return {
            "precedent_matches": precedent,
            "first_time_event_types": list(first_time),
            "unknown_error_classes": list(unknown_errors),
            "has_migration": has_migration,
            "has_test_change": has_test_change,
        }
    except Exception:
        return None


@dataclass
class RouteResult:
    """Outcome of one route() call. Advisory only: action_taken is always False."""

    verdict: RouteVerdict
    deterministic_verdict: RouteVerdict | None
    matched_rule: str | None
    jev_status: str  # not_consulted | gate_closed | advised | errored
    jev_advice: dict[str, Any] | None
    gate: dict[str, Any]
    action_taken: bool = False
    audit_path: str = _DEFAULT_AUDIT_PATH

    def to_audit_dict(self, candidate: QuarantineCandidate) -> dict[str, Any]:
        bounded_paths = list(candidate.changed_paths[:_MAX_STATE_PATHS])
        return {
            "component": AUDIT_COMPONENT,
            "policy_version": POLICY_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "head_sha": candidate.head_sha,
                "changed_path_count": len(candidate.changed_paths),
                "changed_paths": bounded_paths,
                "paths_truncated": len(candidate.changed_paths) > len(bounded_paths),
                "author_association": candidate.author_association,
                "labels": list(candidate.labels),
                "novelty_context": dict(candidate.novelty_context),
            },
            "deterministic_verdict": (
                self.deterministic_verdict.value
                if self.deterministic_verdict is not None
                else None
            ),
            "matched_rule": self.matched_rule,
            "jev_status": self.jev_status,
            "jev_advice": self.jev_advice,
            "gate": dict(self.gate),
            "verdict": self.verdict.value,
            "action_taken": False,
            "shadow": SHADOW_MARKER,
        }


class QuarantineRouter:
    """Shadow novelty-quarantine router. Deterministic rules own the verdict;
    Jev owns only the gray zone, behind a default-off CallSiteGate."""

    def __init__(
        self,
        spec_path: str | Path = _SPEC_PATH,
        audit_path: str = _DEFAULT_AUDIT_PATH,
        client_factory: Any = None,
    ) -> None:
        self._policy = _load_policy(Path(spec_path))
        self._audit_path = str(audit_path)
        self._gate = CallSiteGate(CALL_SITE)
        self._client_factory = client_factory or DecisionClient

    # -- deterministic rules --------------------------------------------

    def _path_familiar(self, path: str) -> bool:
        return any(rx.search(path) for rx in self._policy.familiar_path_patterns)

    def _deterministic(
        self, candidate: QuarantineCandidate
    ) -> tuple[RouteVerdict | None, str | None]:
        """Pure-code rules over the data-in mapping. No Jev import, no Jev call.

        Quarantine triggers evaluate before the familiar rule (fail-closed
        ordering); first match wins.
        """
        context = candidate.novelty_context
        if context["has_migration"] and not context["has_test_change"]:
            return RouteVerdict.QUARANTINE, _TRIGGER_MIGRATION_WITHOUT_TESTS
        if context["unknown_error_classes"]:
            return RouteVerdict.QUARANTINE, _TRIGGER_UNKNOWN_ERROR_CLASSES
        if context["first_time_event_types"]:
            return RouteVerdict.QUARANTINE, _TRIGGER_FIRST_TIME_EVENT_TYPES
        if (
            context["precedent_matches"] >= self._policy.familiar_precedent_min
            and not context["first_time_event_types"]
            and not context["unknown_error_classes"]
            and all(self._path_familiar(path) for path in candidate.changed_paths)
        ):
            return RouteVerdict.PROCEED, _RULE_FAMILIAR
        return None, None

    # -- Jev (gray zone only) --------------------------------------------

    def _gate_snapshot(self) -> dict[str, Any]:
        return {
            "site": self._gate.site,
            "site_env": self._gate.site_env,
            "master_env": CallSiteGate.MASTER_ENV,
            "master_set": _env_truthy(CallSiteGate.MASTER_ENV),
            "site_set": _env_truthy(self._gate.site_env),
            "allowed": self._gate.allow(),
        }

    def _jev_state(self, candidate: QuarantineCandidate) -> dict[str, Any]:
        context = candidate.novelty_context
        return {
            "candidate_id": _bound_str(candidate.candidate_id),
            "author_association": _bound_str(candidate.author_association),
            "labels": [_bound_str(label) for label in candidate.labels],
            "changed_paths": [
                _bound_str(path) for path in candidate.changed_paths[:_MAX_STATE_PATHS]
            ],
            "precedent_matches": context["precedent_matches"],
            "first_time_event_types": [
                _bound_str(item) for item in context["first_time_event_types"]
            ],
            "unknown_error_classes": [
                _bound_str(item) for item in context["unknown_error_classes"]
            ],
            "has_migration": context["has_migration"],
            "has_test_change": context["has_test_change"],
        }

    def _consult_jev(
        self, candidate: QuarantineCandidate
    ) -> tuple[str, dict[str, Any] | None]:
        """Consult Jev for a gray-zone candidate. Returns (status, advice).

        Never raises: every Jev failure mode maps to a status string and the
        caller keeps the fail-closed recommendation (quarantine).
        """
        if not self._gate.allow():
            return "gate_closed", None
        state = self._jev_state(candidate)
        prompt = (
            "A pull request could not be classified by deterministic novelty "
            "rules: it has too few precedent matches to be called familiar, "
            "but no deterministic quarantine trigger fired. Given the "
            "candidate state (changed paths, precedent count, event types), "
            "should it 'proceed' or be routed to 'quarantine' for human "
            "review? This is advisory only; it never triggers an action."
        )
        try:
            client = self._client_factory()
            result = client.decide(
                state,
                [
                    Choice(_JEV_CHOICE_NAME, prompt, options=list(_JEV_OPTIONS)),
                    Noul(
                        _JEV_CONFIDENCE_NAME,
                        "How confident are you in this routing choice?",
                    ),
                ],
                on_error="raise",
            )
        except DecisionError as exc:
            logger.warning("Jev routing call failed closed: %s", exc)
            return "errored", None
        except Exception as exc:  # fail-closed on anything unexpected
            logger.warning("Jev routing call failed closed (unexpected): %r", exc)
            return "errored", None
        advice = result.to_audit_dict()
        advice["advisory_only"] = True
        return "advised", advice

    @staticmethod
    def _advice_choice(advice: dict[str, Any]) -> str | None:
        """Extract the advised route from a Jev advice dict. None = unusable."""
        answers = advice.get("answers")
        if not isinstance(answers, dict):
            return None
        route_answer = answers.get(_JEV_CHOICE_NAME)
        if not isinstance(route_answer, dict):
            return None
        choice = route_answer.get("choice")
        return choice if choice in _JEV_OPTIONS else None

    # -- public API ------------------------------------------------------

    def route(self, candidate: Any) -> RouteResult:
        """Route one candidate. Shadow-only: recommends, never acts."""
        normalized = _normalize_candidate(candidate)
        gate = self._gate_snapshot()

        if normalized is None:
            # Malformed/missing input cannot prove familiar: fail closed.
            result = RouteResult(
                verdict=RouteVerdict.QUARANTINE,
                deterministic_verdict=RouteVerdict.QUARANTINE,
                matched_rule=_RULE_MALFORMED,
                jev_status="not_consulted",
                jev_advice=None,
                gate=gate,
                audit_path=self._audit_path,
            )
            self._write_audit(result, self._empty_candidate())
            return result

        deterministic, matched_rule = self._deterministic(normalized)
        if deterministic is not None:
            # Deterministic verdict is final; Jev is never consulted.
            result = RouteResult(
                verdict=deterministic,
                deterministic_verdict=deterministic,
                matched_rule=matched_rule,
                jev_status="not_consulted",
                jev_advice=None,
                gate=gate,
                audit_path=self._audit_path,
            )
            self._write_audit(result, normalized)
            return result

        jev_status, jev_advice = self._consult_jev(normalized)
        if jev_status == "advised" and jev_advice is not None:
            choice = self._advice_choice(jev_advice)
            if choice == "proceed":
                verdict = RouteVerdict.PROCEED
            else:
                # Unusable or missing choice advice fails closed to quarantine.
                verdict = RouteVerdict.QUARANTINE
                if choice is None:
                    jev_status = "errored"
        else:
            # gate_closed or errored: cannot prove familiar, fail closed.
            verdict = RouteVerdict.QUARANTINE
        result = RouteResult(
            verdict=verdict,
            deterministic_verdict=None,
            matched_rule=None,
            jev_status=jev_status,
            jev_advice=jev_advice,
            gate=gate,
            audit_path=self._audit_path,
        )
        self._write_audit(result, normalized)
        return result

    @staticmethod
    def _empty_candidate() -> QuarantineCandidate:
        return QuarantineCandidate(candidate_id="(malformed)")

    def _write_audit(self, result: RouteResult, candidate: QuarantineCandidate) -> None:
        """Append exactly one JSONL row. A write failure is logged and never
        changes the routing answer (the result was already computed)."""
        try:
            path = Path(self._audit_path)
            if path.parent != Path("."):
                path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result.to_audit_dict(candidate)) + "\n")
        except OSError as exc:
            logger.warning("shadow audit write failed (answer unchanged): %s", exc)


def route_candidate(
    candidate: Any,
    *,
    spec_path: str | Path = _SPEC_PATH,
    audit_path: str = _DEFAULT_AUDIT_PATH,
    client_factory: Any = None,
) -> RouteResult:
    """One-shot convenience wrapper around QuarantineRouter.route()."""
    return QuarantineRouter(
        spec_path=spec_path, audit_path=audit_path, client_factory=client_factory
    ).route(candidate)
