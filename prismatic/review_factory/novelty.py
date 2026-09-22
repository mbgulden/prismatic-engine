"""Deterministic novelty detector + quarantine path (chunk 3 of the roadmap).

When the autonomy machinery cannot classify a candidate, the only safe move
is stop + page. The novelty detector is the deterministic "I don't recognize
this" sensor: any ONE of three novelty inputs trips it, and the response is
pure code, fail-closed — quarantine the candidate, halt its pipeline, emit
the audit signal, prepare the page. Jev *detects* (later); code *contains*.

The three novelty inputs:
- (a) Jev all-low-confidence: a data-in mapping of option -> confidence.
  Trips only when Jev was consulted AND every option scored strictly below
  the policy's ``jev_low_confidence`` threshold — the "never seen this"
  pattern, distinct from confident-risky. UNTIL JEV EXISTS THIS SLOT IS
  INACTIVE: ``jev_confidences=None`` means "Jev not consulted" — recorded as
  *unavailable*, never tripping, never counting as unknown. No Jev import,
  no Jev call, no Jev dependency anywhere in this module.
- (b) no audit precedent: the caller supplies a ``change_shape`` descriptor
  (opaque dict) and ``precedent_matches``, the count of historically similar
  decisions from the audit trail. Zero matches with a present change shape
  trips. ``precedent_matches=None`` means the search is unavailable — the
  input is unavailable, never trips. The similarity-search job is a later
  chunk; this module takes the *count* as pure data.
- (c) deterministic tripwires: ``error_classes`` vs ``known_error_classes``
  (any never-seen class trips), ``event_types`` vs ``seen_event_types``
  (any first-time-ever type trips), ``input_schema_hash`` vs
  ``expected_schema_hash`` (both present and different trips).

Modes (from the versioned policy file):
- ``monitor-only``: a trip returns ``novel_monitor_only`` — logged, the
  candidate's pipeline is NOT halted, NOTHING is quarantined. Phase 0
  collects trip evidence here.
- ``enforcing``: a trip returns ``novel_quarantined`` — the candidate id is
  recorded in the in-memory quarantine registry and its pipeline is halted.
  A quarantined candidate STAYS quarantined across re-evaluations. Release
  is ONLY via ``release("mbgulden", candidate_id)`` — wrong principal gives
  ``refused``, not-quarantined gives ``noop``. No timeout, no auto-clear,
  no self release.

HARD-DISABLED until a phase-advancement step enables it: with
``enabled: false`` (or a missing policy file) ``evaluate()`` returns
``"disabled"`` without reading any input.

Fail-closed throughout:
- a malformed policy file raises ``NoveltyConfigError`` at construction; a
  detector built on it never evaluates (returns ``invalid``);
- invalid input (malformed confidences, negative precedent count, empty
  candidate id, non-string error/event classes) cannot prove familiar: in
  monitor-only it returns ``invalid`` (logged); in enforcing the candidate
  is quarantined — novelty cannot be proven familiar, so code contains it;
- audit write failure never changes the verdict.

Every ``evaluate()`` emits exactly one audit signal as a JSONL row,
component ``novelty-detector``. No signal, no evaluation. This module makes
no network calls, starts no timers, and is not wired into any existing code
path — nothing calls ``evaluate()`` yet.

Pure code, no Jev. Jev detects; code contains.
"""

from __future__ import annotations

import json
import math
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
DEFAULT_POLICY_FILE = SPEC_DIR / "novelty_policy_v1.yaml"
DEFAULT_AUDIT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/novelty-decisions.jsonl")
)

MODE_MONITOR_ONLY = "monitor-only"
MODE_ENFORCING = "enforcing"

# Standing release authority. The ONLY release path is
# NoveltyDetector.release(principal, candidate_id) with
# principal == rearm_principal. Used for page payload text; enforcement
# reads the loaded policy's rearm_principal.
DEFAULT_REARM_PRINCIPAL = "mbgulden"

STATE_DISABLED = "disabled"
STATE_FAMILIAR = "familiar"
STATE_NOVEL_MONITOR = "novel_monitor_only"
STATE_NOVEL_QUARANTINED = "novel_quarantined"
STATE_INVALID = "invalid"

# Novelty input names, in deterministic relevance order for the page's
# top-signal ranking (the shape Jev's advisory selection will later take —
# Jev-shaped, advisory only).
INPUT_JEV_CONFIDENCE = "jev_all_low_confidence"
INPUT_NO_PRECEDENT = "no_precedent"
INPUT_UNKNOWN_ERROR_CLASS = "unknown_error_class"
INPUT_FIRST_TIME_EVENT = "first_time_event"
INPUT_SCHEMA_CHANGE = "schema_change"
INPUT_INVALID = "invalid_input"
INPUT_ALREADY_QUARANTINED = "already_quarantined"

INPUT_RELEVANCE_ORDER = (
    INPUT_JEV_CONFIDENCE,
    INPUT_NO_PRECEDENT,
    INPUT_UNKNOWN_ERROR_CLASS,
    INPUT_FIRST_TIME_EVENT,
    INPUT_SCHEMA_CHANGE,
    INPUT_INVALID,
    INPUT_ALREADY_QUARANTINED,
)

RELEASE_RELEASED = "released"
RELEASE_REFUSED = "refused"
RELEASE_NOOP = "noop"


class NoveltyConfigError(Exception):
    """Raised when the novelty policy cannot be loaded.

    Fail-closed: the detector refuses to evaluate rather than guessing
    from defaults.
    """


# ─────────────────────────────────────────────────────────────────────
# Config models
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NoveltyPolicy:
    """Versioned deterministic novelty policy.

    ``enabled: false`` (the default when the key is absent) means the
    detector is inert: evaluate() returns "disabled" without reading any
    input. The policy must say ``enabled: true`` explicitly — there is no
    way to be accidentally armed. The Jev slot stays inactive until Jev
    exists regardless of the policy.
    """

    version: str
    enabled: bool
    mode: str
    rearm_principal: str
    jev_low_confidence: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NoveltyPolicy":
        if not isinstance(data, dict):
            raise NoveltyConfigError("policy root must be a mapping")
        raw_thresholds = data.get("thresholds", {}) or {}
        if not isinstance(raw_thresholds, dict):
            raise NoveltyConfigError("thresholds must be a mapping")
        raw_conf = raw_thresholds.get("jev_low_confidence", 0.35)
        try:
            jev_low_confidence = float(raw_conf)
        except (TypeError, ValueError) as exc:
            raise NoveltyConfigError(
                f"jev_low_confidence is not a number: {raw_conf!r}"
            ) from exc
        if (
            isinstance(jev_low_confidence, bool)
            or math.isnan(jev_low_confidence)
            or not 0.0 < jev_low_confidence < 1.0
        ):
            raise NoveltyConfigError(
                f"jev_low_confidence must be a finite number in (0, 1): {raw_conf!r}"
            )
        mode = str(data.get("mode", MODE_MONITOR_ONLY))
        if mode not in (MODE_MONITOR_ONLY, MODE_ENFORCING):
            raise NoveltyConfigError(f"unknown novelty mode: {mode!r}")
        return cls(
            version=str(data.get("version", "unknown")),
            enabled=bool(data.get("enabled", False)),
            mode=mode,
            rearm_principal=str(data.get("rearm_principal", DEFAULT_REARM_PRINCIPAL)),
            jev_low_confidence=jev_low_confidence,
        )


def load_novelty_policy(path: Optional[Path | str] = None) -> NoveltyPolicy:
    """Load the versioned novelty policy, fail-closed.

    A missing file yields a disabled policy (inert), not an error — but a
    malformed file raises ``NoveltyConfigError`` so the detector never runs
    on a half-read config.
    """
    path = Path(path) if path is not None else DEFAULT_POLICY_FILE
    if not path.exists():
        return NoveltyPolicy.from_dict({"version": "missing", "enabled": False})
    if not _HAS_YAML:
        raise NoveltyConfigError("PyYAML is required to load the novelty policy")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise NoveltyConfigError(f"cannot parse policy file {path}: {exc}") from exc
    return NoveltyPolicy.from_dict(data)


# ─────────────────────────────────────────────────────────────────────
# Input + result models (pure data)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NoveltyInput:
    """One candidate's novelty evidence, supplied by the caller.

    All three novelty inputs are *data*, not judgments the detector makes:
    - ``jev_confidences``: option -> confidence in [0, 1] from a Jev call,
      or None when Jev was not consulted (the slot is INACTIVE until Jev
      exists — None is *unavailable*, never a trip).
    - ``precedent_matches``: count of historically similar decisions from
      the audit trail (the similarity search is a later chunk), or None
      when the search is unavailable. ``change_shape`` is the opaque
      descriptor the search matched on.
    - ``error_classes`` / ``known_error_classes``: this candidate's error
      classes vs. every class the system has seen before.
    - ``event_types`` / ``seen_event_types``: this candidate's event types
      vs. every type the system has seen before.
    - ``input_schema_hash`` / ``expected_schema_hash``: both present and
      different trips; one missing means unavailable, never a trip.
    """

    candidate_id: str
    jev_confidences: Optional[dict[str, float]] = None
    precedent_matches: Optional[int] = None
    change_shape: Optional[dict[str, Any]] = None
    error_classes: tuple[str, ...] = ()
    known_error_classes: frozenset[str] = frozenset()
    event_types: tuple[str, ...] = ()
    seen_event_types: frozenset[str] = frozenset()
    input_schema_hash: Optional[str] = None
    expected_schema_hash: Optional[str] = None


@dataclass(frozen=True)
class NoveltyTrip:
    """One novelty input that tripped."""

    input: str  # one of the INPUT_* constants
    evidence: str  # human-readable key evidence
    reason: str  # trip | invalid | registry


@dataclass(frozen=True)
class NoveltyResult:
    """The detector's answer for one evaluation."""

    state: str  # disabled | familiar | novel_monitor_only |
    # novel_quarantined | invalid
    candidate_id: str = ""
    trips: tuple[NoveltyTrip, ...] = ()
    quarantined: bool = False
    pipeline_halted: bool = False
    policy_version: str = "unknown"
    mode: str = MODE_MONITOR_ONLY


def _validate_input(candidate: NoveltyInput) -> Optional[str]:
    """Return an error string for an invalid input, else None.

    Fail-closed: an input the detector cannot trust must never produce a
    "familiar" verdict.
    """
    if not isinstance(candidate.candidate_id, str) or not candidate.candidate_id:
        return "candidate_id must be a non-empty string"
    conf = candidate.jev_confidences
    if conf is not None:
        if not isinstance(conf, dict):
            return "jev_confidences must be a mapping or None"
        if not conf:
            # Jev consulted, zero options: malformed Jev output.
            return "jev_confidences is empty (malformed Jev output)"
        for option, value in conf.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or math.isnan(value)
                or math.isinf(value)
                or value < 0
                or value > 1
            ):
                return f"jev confidence for {option!r} not in [0, 1]: {value!r}"
    if candidate.precedent_matches is not None:
        if (
            isinstance(candidate.precedent_matches, bool)
            or not isinstance(candidate.precedent_matches, int)
            or candidate.precedent_matches < 0
        ):
            return f"precedent_matches must be a non-negative int or None: {candidate.precedent_matches!r}"
    for label, classes in (
        ("error_classes", candidate.error_classes),
        ("known_error_classes", candidate.known_error_classes),
        ("event_types", candidate.event_types),
        ("seen_event_types", candidate.seen_event_types),
    ):
        if isinstance(classes, (set, frozenset, list, tuple)):
            items: Any = tuple(classes)
        else:
            return f"{label} must be a collection of strings"
        for item in items:
            if not isinstance(item, str):
                return f"{label} contains a non-string item: {item!r}"
    for label, value in (
        ("input_schema_hash", candidate.input_schema_hash),
        ("expected_schema_hash", candidate.expected_schema_hash),
    ):
        if value is not None and not isinstance(value, str):
            return f"{label} must be a string or None: {value!r}"
    return None


def _compute_trips(
    candidate: NoveltyInput, jev_threshold: float
) -> tuple[NoveltyTrip, ...]:
    """Compute the novelty trips for a VALID input. Pure, no side effects."""
    trips: list[NoveltyTrip] = []

    # (a) Jev all-low-confidence — INACTIVE until Jev exists. None means
    # "Jev not consulted": unavailable, never a trip, never unknown.
    if candidate.jev_confidences is not None:
        low = [opt for opt, v in candidate.jev_confidences.items() if v < jev_threshold]
        if len(low) == len(candidate.jev_confidences):
            trips.append(
                NoveltyTrip(
                    input=INPUT_JEV_CONFIDENCE,
                    evidence=(
                        f"Jev consulted; all {len(candidate.jev_confidences)} "
                        f"option(s) below {jev_threshold}: "
                        + ", ".join(
                            f"{opt}={candidate.jev_confidences[opt]}"
                            for opt in sorted(candidate.jev_confidences)
                        )
                    ),
                    reason="trip",
                )
            )

    # (b) no audit precedent. Zero matches with a PRESENT change shape
    # trips; a missing search or missing shape is unavailable, never a trip.
    if (
        candidate.precedent_matches is not None
        and candidate.change_shape is not None
        and candidate.precedent_matches == 0
    ):
        trips.append(
            NoveltyTrip(
                input=INPUT_NO_PRECEDENT,
                evidence=(
                    f"0 precedent matches for change shape {candidate.change_shape!r}"
                ),
                reason="trip",
            )
        )

    # (c) deterministic tripwires.
    unknown_classes = sorted(
        c for c in candidate.error_classes if c not in candidate.known_error_classes
    )
    if unknown_classes:
        trips.append(
            NoveltyTrip(
                input=INPUT_UNKNOWN_ERROR_CLASS,
                evidence=f"unknown error classes: {', '.join(unknown_classes)}",
                reason="trip",
            )
        )
    new_event_types = sorted(
        e for e in candidate.event_types if e not in candidate.seen_event_types
    )
    if new_event_types:
        trips.append(
            NoveltyTrip(
                input=INPUT_FIRST_TIME_EVENT,
                evidence=f"first-time event types: {', '.join(new_event_types)}",
                reason="trip",
            )
        )
    if (
        candidate.input_schema_hash is not None
        and candidate.expected_schema_hash is not None
        and candidate.input_schema_hash != candidate.expected_schema_hash
    ):
        trips.append(
            NoveltyTrip(
                input=INPUT_SCHEMA_CHANGE,
                evidence=(
                    f"input schema hash {candidate.input_schema_hash!r} != "
                    f"expected {candidate.expected_schema_hash!r}"
                ),
                reason="trip",
            )
        )

    return tuple(trips)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# The detector
# ─────────────────────────────────────────────────────────────────────


class NoveltyDetector:
    """Deterministic novelty sensor + quarantine path.

    Construct with the policy path. ``evaluate()`` is inert while the
    policy is disabled. In monitor-only mode a trip is logged, never
    quarantined; in enforcing mode a trip quarantines the candidate id
    (in-memory registry, no durable store yet) and halts its pipeline, and
    only ``release("mbgulden", candidate_id)`` frees it — there is no other
    release path.
    """

    def __init__(
        self,
        policy_path: Optional[Path | str] = None,
        *,
        audit_log: Optional[Path | str] = None,
        now_fn: Any = None,
    ):
        try:
            self.policy = load_novelty_policy(policy_path)
            self._policy_error: Optional[str] = None
        except NoveltyConfigError as exc:
            # Fail-closed: a malformed policy never evaluates.
            self.policy = NoveltyPolicy.from_dict(
                {"version": "invalid", "enabled": False}
            )
            self._policy_error = str(exc)
        self.audit_log = Path(audit_log) if audit_log is not None else DEFAULT_AUDIT_LOG
        self._now = now_fn or time.time
        # In-memory quarantine registry: candidate_id -> quarantine ts.
        # Durable store is a later chunk's job; no timers, no auto-clear.
        self._quarantine: dict[str, float] = {}

    @property
    def quarantined_ids(self) -> tuple[str, ...]:
        """Candidate ids currently quarantined (in-memory registry)."""
        return tuple(self._quarantine)

    # -- audit --------------------------------------------------------

    def _emit_audit(
        self,
        result: NoveltyResult,
        candidate: NoveltyInput,
        note: str = "",
    ) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "novelty-detector",
            "policy_version": result.policy_version,
            "mode": result.mode,
            "state": result.state,
            "candidate_id": result.candidate_id,
            "tripped_inputs": [t.input for t in result.trips],
            "trips": [
                {"input": t.input, "evidence": t.evidence, "reason": t.reason}
                for t in result.trips
            ],
            "quarantined": result.quarantined,
            "pipeline_halted": result.pipeline_halted,
            "jev_confidences_available": candidate.jev_confidences is not None,
            "precedent_matches": candidate.precedent_matches,
            "note": note,
        }
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            # Audit write failure must not change the evaluation outcome —
            # and must never turn a quarantine into a familiar. The verdict
            # stands.
            pass

    # -- page input ----------------------------------------------------

    @staticmethod
    def prepare_page(result: NoveltyResult) -> dict[str, Any]:
        """Build the page payload for a novelty result. Data only — NEVER
        sends anything. This is the human-readable page *input*, not the
        page itself.

        The 3 most decision-relevant signals go first, in deterministic
        relevance order (the shape Jev's advisory selection will later
        take — Jev-shaped, advisory only):
        1. which novelty input tripped and its key evidence,
        2. the quarantine / pipeline-halt state,
        3. the release authority.
        """
        rank = {name: i for i, name in enumerate(INPUT_RELEVANCE_ORDER)}
        ordered = sorted(result.trips, key=lambda t: rank.get(t.input, len(rank)))
        top: list[str] = [f"{t.input}: {t.evidence}" for t in ordered[:3]]
        top.append(
            f"quarantine: {'QUARANTINED' if result.quarantined else 'not quarantined'}; "
            f"pipeline: {'HALTED' if result.pipeline_halted else 'running'}"
        )
        top.append(f"release authority: {DEFAULT_REARM_PRINCIPAL}")
        return {
            "summary": (
                f"novelty {result.state} for candidate {result.candidate_id!r}"
            ),
            "state": result.state,
            "candidate_id": result.candidate_id,
            "top_signals": top[:3],
            "quarantined": result.quarantined,
            "pipeline_halted": result.pipeline_halted,
            "mode": result.mode,
            "policy_version": result.policy_version,
            "release_principal": DEFAULT_REARM_PRINCIPAL,
            "trips": [
                {"input": t.input, "evidence": t.evidence, "reason": t.reason}
                for t in ordered
            ],
        }

    # -- evaluation ----------------------------------------------------

    def evaluate(self, candidate: NoveltyInput) -> NoveltyResult:
        """Evaluate one candidate's novelty evidence against the policy.

        Pure apart from the audit signal it emits and (in enforcing mode)
        the in-memory quarantine registry update.
        """
        # 1. Policy health. A malformed policy never evaluates.
        if self._policy_error is not None:
            result = NoveltyResult(
                state=STATE_INVALID,
                candidate_id=candidate.candidate_id
                if isinstance(candidate.candidate_id, str)
                else "",
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(
                result, candidate, note=f"policy_error: {self._policy_error}"
            )
            return result

        # 2. MASTER SWITCH. Disabled = inert, no input read.
        if not self.policy.enabled:
            result = NoveltyResult(
                state=STATE_DISABLED,
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(result, candidate, note="novelty_detector_disabled")
            return result

        # 3. Input validity. An untrustworthy input cannot prove familiar.
        invalid = _validate_input(candidate)
        if invalid is not None:
            if self.policy.mode == MODE_ENFORCING:
                # Fail-closed: novelty cannot be proven familiar, so code
                # contains the candidate. (An empty candidate id cannot be
                # registered — there is nothing to contain — so it stays a
                # plain invalid.)
                if candidate.candidate_id:
                    self._quarantine[candidate.candidate_id] = self._now()
                    result = NoveltyResult(
                        state=STATE_NOVEL_QUARANTINED,
                        candidate_id=candidate.candidate_id,
                        trips=(
                            NoveltyTrip(
                                input=INPUT_INVALID,
                                evidence=f"invalid input contained: {invalid}",
                                reason="fail_closed_contain",
                            ),
                        ),
                        quarantined=True,
                        pipeline_halted=True,
                        policy_version=self.policy.version,
                        mode=self.policy.mode,
                    )
                    self._emit_audit(
                        result, candidate, note=f"invalid_input_contained: {invalid}"
                    )
                    return result
            result = NoveltyResult(
                state=STATE_INVALID,
                candidate_id=candidate.candidate_id
                if isinstance(candidate.candidate_id, str)
                else "",
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(result, candidate, note=f"invalid_input: {invalid}")
            return result

        # 4. Compute trips for the valid input.
        trips = _compute_trips(candidate, self.policy.jev_low_confidence)
        already = candidate.candidate_id in self._quarantine
        if already:
            trips = trips + (
                NoveltyTrip(
                    input=INPUT_ALREADY_QUARANTINED,
                    evidence=(
                        f"candidate {candidate.candidate_id!r} quarantined at "
                        f"{self._quarantine[candidate.candidate_id]}; "
                        "stays quarantined across re-evaluations"
                    ),
                    reason="registry",
                ),
            )

        if not trips:
            result = NoveltyResult(
                state=STATE_FAMILIAR,
                candidate_id=candidate.candidate_id,
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(result, candidate)
            return result

        # 5. Trip handling: monitor-only logs; enforcing quarantines.
        if self.policy.mode == MODE_ENFORCING:
            self._quarantine[candidate.candidate_id] = self._quarantine.get(
                candidate.candidate_id, self._now()
            )
            result = NoveltyResult(
                state=STATE_NOVEL_QUARANTINED,
                candidate_id=candidate.candidate_id,
                trips=trips,
                quarantined=True,
                pipeline_halted=True,
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(result, candidate, note="novelty_quarantined")
            return result

        result = NoveltyResult(
            state=STATE_NOVEL_MONITOR,
            candidate_id=candidate.candidate_id,
            trips=trips,
            quarantined=False,
            pipeline_halted=False,
            policy_version=self.policy.version,
            mode=self.policy.mode,
        )
        self._emit_audit(result, candidate, note="novelty_trip_monitor_only")
        return result

    # -- release --------------------------------------------------------

    def release(self, principal: str, candidate_id: str) -> str:
        """Release a quarantined candidate. The ONLY release path.

        Returns "released" | "refused" | "noop". Only the rearm principal
        may release; a refused release keeps the candidate quarantined.
        There is no timeout, no auto-clear, and no other caller.
        """
        if candidate_id not in self._quarantine:
            self._emit_release_audit("noop", principal, candidate_id)
            return RELEASE_NOOP
        if principal != self.policy.rearm_principal:
            self._emit_release_audit("refused", principal, candidate_id)
            return RELEASE_REFUSED
        del self._quarantine[candidate_id]
        self._emit_release_audit("released", principal, candidate_id)
        return RELEASE_RELEASED

    def _emit_release_audit(
        self, outcome: str, principal: str, candidate_id: str
    ) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "novelty-detector",
            "policy_version": self.policy.version,
            "mode": self.policy.mode,
            "event": "quarantine_release",
            "outcome": outcome,
            "principal": principal,
            "candidate_id": candidate_id,
            "quarantined_ids": list(self._quarantine),
        }
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            pass
