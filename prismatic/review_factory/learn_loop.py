"""Learn loop + self-tuning risk bands (chunk 4 of the post-shadow roadmap).

HARD-DISABLED until Michael explicitly advances the rollout ladder. With
``enabled: false`` (the shipped state) ``self_review()`` returns ``disabled``
without reading a single log row, and ``record_outcome()`` refuses every
call. There is no way to be accidentally on.

What this module is:

- **Memory.** It reads the merge authority's decision audit log (chunk 1),
  joins it with an append-only outcome log (``clean`` / ``rolled_back`` /
  ``escalated`` — ground truth is mechanical: rolled back = bad merge, an
  explicit clean outcome after ``good_after_days`` = good merge), and keeps a
  band-change log so loosening stays rate-limited.
- **Batch rollup.** ``self_review()`` is the periodic self-review job's pure
  core: it aggregates the review window, asserts signal coverage (every
  decision row must carry its audit fields; gaps forbid loosening), and
  produces the plain-language report ("212 auto-merges, 2 rollbacks —
  proposal: tighten human_above 0.60 -> 0.65").
- **Proposal engine, tighten-only default.** Tighten proposals are valid any
  time (bad outcomes are evidence enough). Loosen proposals are valid only
  with mechanical evidence: zero coverage gaps, zero window rollbacks,
  ``clean_outcomes_for_loosen`` consecutive explicit clean outcomes, and
  total loosening inside ``max_loosen_per_week``. Beyond the rate limit the
  proposal is still generated but marked ``requires_michael: true`` — the
  learn loop blesses nothing it cannot mechanically justify.

Band changes are applied only through ``LearnLoop.apply_band_change``
(or the ``apply`` CLI subcommand): the proposal is re-validated, the
watchdog metrics-feed event is recorded FIRST (fail-closed -- a record
failure aborts before anything is written), then the new versioned spec
file is filed next to the loaded one. The live bands file is never
overwritten -- activation stays an explicit separate step. There are no
timers, no network calls, no Jev (``jev_score`` parses through as data
only, null today).

Event-based first: ``record_outcome()`` is called on events (the rollback
watcher firing, the 7-day clean-verification backstop confirming). The
periodic ``self_review()`` job is a batch rollup — one of the explicitly
allowed polling cases — but this chunk provides only the pure function; the
scheduler is a later chunk.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from prismatic.review_factory.shadow_agreement import (
    CLASS_JEV_WAS_RIGHT,
    CLASS_TOO_AGGRESSIVE,
    CORRECTION_JEV_WAS_RIGHT,
    classify_disagreement,
)

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _HAS_YAML = False

SPEC_DIR = Path(__file__).resolve().parent / "spec"
DEFAULT_LEARN_POLICY_FILE = SPEC_DIR / "learn_loop_policy_v1.yaml"
DEFAULT_BANDS_FILE = SPEC_DIR / "auto_merge_bands_v1.yaml"
DEFAULT_DECISION_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/auto-merge-decisions.jsonl")
)
DEFAULT_OUTCOME_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/learn-outcomes.jsonl")
)
DEFAULT_BAND_CHANGE_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/learn-band-changes.jsonl")
)
DEFAULT_AUDIT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/learn-loop-decisions.jsonl")
)
DEFAULT_SHADOW_RECORDS_FILE = Path(
    os.path.expanduser("~/.prismatic/audit/shadow-records.jsonl")
)
DEFAULT_CORRECTIONS_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/agreement-corrections.jsonl")
)

# Schema tag on every appended agreement-correction row.
CORRECTION_SCHEMA = "agreement-correction/v1"

MODE_REPORT_ONLY = "report-only"
MODE_PROPOSE = "propose"

OUTCOME_CLEAN = "clean"
OUTCOME_ROLLED_BACK = "rolled_back"
OUTCOME_ESCALATED = "escalated"
OUTCOMES = frozenset({OUTCOME_CLEAN, OUTCOME_ROLLED_BACK, OUTCOME_ESCALATED})

DIRECTION_TIGHTEN = "tighten"
DIRECTION_LOOSEN = "loosen"

# Required fields on every decision-log row. The learn job asserts signal
# coverage: rows missing any of these are coverage gaps, not data.
REQUIRED_DECISION_FIELDS = ("job_id", "ts", "decision", "policy_version")

_SECONDS_PER_DAY = 86400.0

# Epsilon for float comparisons on band units (e.g. 0.6 - 0.55 is
# 0.050000000000000044 in binary floating point; band units are 2-decimal).
_EPS = 1e-9


class LearnConfigError(Exception):
    """Raised when the learn policy or band config cannot be loaded.

    Fail-closed: the learn loop refuses to review or record rather than
    guessing from defaults.
    """


class LearnInputError(ValueError):
    """Raised for a malformed caller input (e.g. an unknown outcome value).

    Fail-closed: nothing is written; the caller must fix the input.
    """


# ─────────────────────────────────────────────────────────────────────
# Config models
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LearnPolicy:
    """Versioned learn-loop policy.

    ``enabled: false`` (the default when the key is absent) means the loop
    is inert: ``self_review()`` returns "disabled" and ``record_outcome()``
    refuses. The policy must say ``enabled: true`` explicitly.
    """

    version: str
    enabled: bool
    mode: str
    review_window_days: int
    good_after_days: int
    clean_outcomes_for_loosen: int
    tighten_rollback_rate: float
    tighten_escalation_rate: float
    max_loosen_per_week: float
    tighten_step: float
    loosen_step: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LearnPolicy":
        if not isinstance(data, dict):
            raise LearnConfigError("learn policy root must be a mapping")
        mode = str(data.get("mode", MODE_REPORT_ONLY))
        if mode not in (MODE_REPORT_ONLY, MODE_PROPOSE):
            raise LearnConfigError(f"unknown learn mode: {mode!r}")
        return cls(
            version=str(data.get("version", "unknown")),
            enabled=bool(data.get("enabled", False)),
            mode=mode,
            review_window_days=int(data.get("review_window_days", 30)),
            good_after_days=int(data.get("good_after_days", 7)),
            clean_outcomes_for_loosen=int(data.get("clean_outcomes_for_loosen", 20)),
            tighten_rollback_rate=float(data.get("tighten_rollback_rate", 0.03)),
            tighten_escalation_rate=float(data.get("tighten_escalation_rate", 0.35)),
            max_loosen_per_week=float(data.get("max_loosen_per_week", 0.05)),
            tighten_step=float(data.get("tighten_step", 0.05)),
            loosen_step=float(data.get("loosen_step", 0.05)),
        )


def load_learn_policy(path: Optional[Path | str] = None) -> LearnPolicy:
    """Load the versioned learn-loop policy, fail-closed.

    A missing file yields a disabled policy (inert), not an error — but a
    malformed file raises ``LearnConfigError`` so the loop never reviews on
    a half-read config.
    """
    path = Path(path) if path is not None else DEFAULT_LEARN_POLICY_FILE
    if not path.exists():
        return LearnPolicy.from_dict({"version": "missing", "enabled": False})
    if not _HAS_YAML:
        raise LearnConfigError("PyYAML is required to load the learn policy")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise LearnConfigError(f"cannot parse learn policy file {path}: {exc}") from exc
    return LearnPolicy.from_dict(data)


@dataclass(frozen=True)
class LearnBands:
    """Versioned auto-merge risk bands the learn loop tunes.

    Unlike the learn policy, there is no safe "missing" default for the
    proposal target: without a band config there are no proposals, and the
    loader fails closed.
    """

    version: str
    values: dict[str, float]
    caps: dict[str, tuple[float, float]]
    tighten_direction: dict[str, str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LearnBands":
        if not isinstance(data, dict):
            raise LearnConfigError("band config root must be a mapping")
        values: dict[str, float] = {}
        for key in ("auto_below", "human_above"):
            raw = data.get(key)
            if raw is None:
                raise LearnConfigError(f"band config missing band {key!r}")
            try:
                values[key] = float(raw)
            except (TypeError, ValueError) as exc:
                raise LearnConfigError(f"band {key!r} is not numeric: {raw!r}") from exc
        caps = {
            "human_above": (
                float(data.get("human_above_min", 0.0)),
                float(data.get("human_above_max", 1.0)),
            ),
            "auto_below": (
                float(data.get("auto_below_min", 0.0)),
                float(data.get("auto_below_max", 1.0)),
            ),
        }
        tighten_direction = {
            str(k): str(v) for k, v in (data.get("tighten_direction", {}) or {}).items()
        }
        for key in values:
            if tighten_direction.get(key) not in ("up", "down"):
                raise LearnConfigError(
                    f"band {key!r} missing tighten_direction (up|down)"
                )
        return cls(
            version=str(data.get("version", "unknown")),
            values=values,
            caps=caps,
            tighten_direction=tighten_direction,
        )


def load_learn_bands(path: Optional[Path | str] = None) -> LearnBands:
    """Load the versioned auto-merge risk bands, fail-closed.

    A missing OR malformed file raises ``LearnConfigError``: the learn loop
    must not propose changes against a band config it cannot read.
    """
    path = Path(path) if path is not None else DEFAULT_BANDS_FILE
    if not path.exists():
        raise LearnConfigError(f"band config file not found: {path}")
    if not _HAS_YAML:
        raise LearnConfigError("PyYAML is required to load the band config")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise LearnConfigError(f"cannot parse band config {path}: {exc}") from exc
    return LearnBands.from_dict(data)


# ─────────────────────────────────────────────────────────────────────
# Log records
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionRecord:
    """One parsed merge-authority decision row.

    ``band_values`` is None for rows that predate the field (chunk 1 did not
    record it) — forward rows may carry it. Absence is not an error.
    """

    job_id: str
    ts: float
    decision: str
    policy_version: str
    tier: int = -1
    jev_score: Optional[float] = None
    band_values: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class OutcomeRecord:
    """One recorded outcome, keyed to a decision by job_id."""

    job_id: str
    ts: float
    outcome: str


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL log. Corrupt lines are skipped, never fatal."""
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue  # corrupt line: skip, never crash the review
        if isinstance(data, dict):
            rows.append(data)
    return rows


def parse_decision_log(path: Path | str) -> tuple[list[DecisionRecord], int]:
    """Parse the authority's decision log.

    Returns (records, coverage_gaps): rows missing a required field count
    as coverage gaps and are excluded from records — the learn job asserts
    signal coverage, and incomplete data must not feed loosen evidence.
    """
    rows = _read_jsonl(Path(path))
    records: list[DecisionRecord] = []
    gaps = 0
    for row in rows:
        if not _valid_decision_row(row):
            gaps += 1
            continue
        ts = row.get("ts")
        band_values = row.get("band_values")
        records.append(
            DecisionRecord(
                job_id=str(row["job_id"]),
                ts=float(ts),
                decision=str(row["decision"]),
                policy_version=str(row["policy_version"]),
                tier=int(row.get("tier", -1)),
                jev_score=(
                    float(row["jev_score"])
                    if row.get("jev_score") is not None
                    else None
                ),
                band_values=(
                    dict(band_values) if isinstance(band_values, dict) else None
                ),
            )
        )
    return records, gaps


def _valid_decision_row(row: dict[str, Any]) -> bool:
    """Row-validity rules for the decision log (shared by the full parser
    and the event-path tail scanner): all required fields present and a
    real numeric timestamp. Malformed rows are skipped, never fatal."""
    if any(row.get(f) is None for f in REQUIRED_DECISION_FIELDS):
        return False
    ts = row.get("ts")
    return isinstance(ts, (int, float)) and not isinstance(ts, bool)


def _valid_outcome_row(row: dict[str, Any]) -> bool:
    """Row-validity rules for the outcome log (shared by the full parser
    and the event-path tail scanner)."""
    job_id = row.get("job_id")
    outcome = row.get("outcome")
    ts = row.get("ts")
    return (
        isinstance(job_id, str)
        and outcome in OUTCOMES
        and isinstance(ts, (int, float))
        and not isinstance(ts, bool)
    )


def _decision_has_job(path: Path | str, job_id: str) -> bool:
    """Tail-first membership check: does a *valid* decision row for
    ``job_id`` exist?

    Event-path fast path for ``record_outcome``'s unknown-job refusal.
    Scans newest-first and stops at the first valid row, so the common
    case (the just-decided merge) never parses the whole log. The
    ``json.dumps`` needle pre-filter is exact: a valid row for this
    job_id must contain its JSON-encoded form, so skipped lines cannot
    hide a decision. An unreadable log reads as "no decision row"
    (fail-closed: the caller refuses the unknown job).
    """
    needle = json.dumps(job_id).encode("utf-8")
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    for line in reversed(data.splitlines()):
        if needle not in line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # corrupt line: skip, never crash the event path
        if (
            isinstance(row, dict)
            and _valid_decision_row(row)
            and str(row["job_id"]) == job_id
        ):
            return True
    return False


def _outcome_recorded(path: Path | str, job_id: str) -> bool:
    """Tail-first membership check: does a *valid* outcome row for
    ``job_id`` exist?

    Event-path fast path for ``record_outcome``'s duplicate refusal.
    Same fail-closed contract as ``_decision_has_job``: validity rules
    identical to the full parser, malformed rows never count.
    """
    needle = json.dumps(job_id).encode("utf-8")
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    for line in reversed(data.splitlines()):
        if needle not in line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # corrupt line: skip, never crash the event path
        if (
            isinstance(row, dict)
            and _valid_outcome_row(row)
            and row["job_id"] == job_id
        ):
            return True
    return False


def _round2(value: float) -> float:
    return round(value + 1e-12, 2)


# ─────────────────────────────────────────────────────────────────────
# Proposals and reports
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BandProposal:
    """One proposed band change. Data only — never applied by this module."""

    band_version: str
    band_key: str
    old_value: float
    new_value: float
    direction: str  # "tighten" | "loosen"
    evidence: dict[str, Any] = field(default_factory=dict)
    requires_michael: bool = False
    reason: str = ""
    proposed_spec_text: str = ""

    @property
    def loosen(self) -> bool:
        return self.direction == DIRECTION_LOOSEN


@dataclass(frozen=True)
class LearnReport:
    """The self-review's answer: aggregates, evidence, proposals, words."""

    state: str  # "disabled" | "invalid" | "reviewed"
    policy_version: str
    window_days: int
    auto_merges: int = 0
    refusals: int = 0
    rollbacks: int = 0
    escalations: int = 0
    cleans: int = 0
    rollback_rate: float = 0.0
    escalation_rate: float = 0.0
    coverage_gaps: int = 0
    consecutive_clean: int = 0
    proposals: tuple[BandProposal, ...] = ()
    summary: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_VERSION_SUFFIX_RE = re.compile(r"^(?P<stem>.*-v)(?P<num>\d+)$")


def _next_band_version(version: str) -> str:
    """auto-bands-v1 -> auto-bands-v2. Non-matching versions get '-next'."""
    match = _VERSION_SUFFIX_RE.match(version)
    if match:
        return f"{match.group('stem')}{int(match.group('num')) + 1}"
    return f"{version}-next"


def _next_spec_path(bands_path: Path, new_version: str) -> Path:
    """Sibling path for the next versioned bands spec file.

    ``auto_merge_bands_v1.yaml`` + ``auto-bands-v2`` ->
    ``auto_merge_bands_v2.yaml``. Never overwrites: callers refuse when
    the target already exists.
    """
    stem = bands_path.stem
    version_num = new_version.rsplit("-v", 1)[-1] if "-v" in new_version else None
    m = re.match(r"^(.*[-_]v)\d+$", stem)
    if m and version_num:
        new_stem = f"{m.group(1)}{version_num}"
    else:
        new_stem = f"{stem}-{new_version}"
    return bands_path.with_name(new_stem + bands_path.suffix)


def _build_proposed_spec_text(
    bands: LearnBands,
    band_key: str,
    new_value: float,
    evidence: dict[str, Any],
) -> str:
    """Full YAML text of the would-be new versioned band file.

    Carried on the proposal so it can be filed through the normal review
    pipeline as a PR — this module never writes it to spec/ itself.
    """
    version = _next_band_version(bands.version)
    lines = [
        "# Auto-merge risk bands — PROPOSED by the chunk-4 learn loop.",
        "#",
        "# NOT APPLIED. This text is a proposal payload: file it as a new",
        "# versioned spec file (e.g. spec/auto_merge_bands_v2.yaml) through",
        "# the normal review pipeline. Applying requires Michael's word.",
        "#",
        f"# Evidence: {json.dumps(evidence, sort_keys=True)}",
        "",
        f"version: {version}",
        "",
        f"auto_below: {_round2(bands.values['auto_below'])}",
        f"human_above: {_round2(bands.values['human_above'])}",
        "",
        "on_jev_error: fail_closed",
        "",
        f"human_above_min: {_round2(bands.caps['human_above'][0])}",
        f"human_above_max: {_round2(bands.caps['human_above'][1])}",
        f"auto_below_min: {_round2(bands.caps['auto_below'][0])}",
        f"auto_below_max: {_round2(bands.caps['auto_below'][1])}",
        "",
        "tighten_direction:",
        f"  human_above: {bands.tighten_direction['human_above']}",
        f"  auto_below: {bands.tighten_direction['auto_below']}",
        "",
        "loosening_requires: michael_word_and_evidence",
        "",
    ]
    # Apply the proposed value into the text.
    text = "\n".join(lines)
    if band_key == "human_above":
        text = text.replace(
            f"human_above: {_round2(bands.values['human_above'])}",
            f"human_above: {_round2(new_value)}",
            1,
        )
    else:
        text = text.replace(
            f"auto_below: {_round2(bands.values['auto_below'])}",
            f"auto_below: {_round2(new_value)}",
            1,
        )
    return text


# ─────────────────────────────────────────────────────────────────────
# The learn loop
# ─────────────────────────────────────────────────────────────────────


class LearnLoop:
    """Chunk-4 learn loop: record outcomes (events), self-review (rollup).

    Construct with explicit log paths (defaults point at the prismatic
    state home). A malformed policy or band config refuses every call —
    fail-closed, like its chunk-1/2 siblings.
    """

    def __init__(
        self,
        policy_path: Optional[Path | str] = None,
        bands_path: Optional[Path | str] = None,
        *,
        decision_log: Optional[Path | str] = None,
        outcome_log: Optional[Path | str] = None,
        band_change_log: Optional[Path | str] = None,
        audit_log: Optional[Path | str] = None,
        now_fn: Any = None,
    ):
        self._policy_error: Optional[str] = None
        self._bands_error: Optional[str] = None
        try:
            self.policy = load_learn_policy(policy_path)
        except LearnConfigError as exc:
            self.policy = LearnPolicy.from_dict(
                {"version": "invalid", "enabled": False}
            )
            self._policy_error = str(exc)
        try:
            self.bands = load_learn_bands(bands_path)
        except LearnConfigError as exc:
            # A placeholder that never proposes: without a band config the
            # loop must not invent bands. Every proposal path checks the
            # error first and refuses.
            self.bands = LearnBands.from_dict(
                {
                    "version": "invalid",
                    "auto_below": 0.0,
                    "human_above": 0.0,
                    "tighten_direction": {
                        "human_above": "up",
                        "auto_below": "up",
                    },
                }
            )
            self._bands_error = str(exc)
        self.bands_path = (
            Path(bands_path) if bands_path is not None else DEFAULT_BANDS_FILE
        )
        self.decision_log = (
            Path(decision_log) if decision_log is not None else DEFAULT_DECISION_LOG
        )
        self.outcome_log = (
            Path(outcome_log) if outcome_log is not None else DEFAULT_OUTCOME_LOG
        )
        self.band_change_log = (
            Path(band_change_log)
            if band_change_log is not None
            else DEFAULT_BAND_CHANGE_LOG
        )
        self.audit_log = Path(audit_log) if audit_log is not None else DEFAULT_AUDIT_LOG
        self._now = now_fn or time.time

    # -- audit -------------------------------------------------------

    def _emit_audit(self, action: str, payload: dict[str, Any]) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "learn_loop",
            "policy_version": self.policy.version,
            "action": action,
        }
        row.update(payload)
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            # Audit write failure must not change a decision — and must not
            # crash a refusal path either. The decision stands.
            pass

    def _config_refusal_reason(self) -> Optional[str]:
        if self._policy_error is not None:
            return f"policy_config_error: {self._policy_error}"
        if self._bands_error is not None:
            return f"bands_config_error: {self._bands_error}"
        if not self.policy.enabled:
            return "learn_loop_disabled"
        return None

    # -- outcome recording (event path) -------------------------------

    def _recorded_outcomes(self) -> dict[str, OutcomeRecord]:
        outcomes: dict[str, OutcomeRecord] = {}
        for row in _read_jsonl(self.outcome_log):
            if not _valid_outcome_row(row):
                continue  # malformed outcome row: skip, never crash the read
            job_id = row.get("job_id")
            outcome = row.get("outcome")
            ts = row.get("ts")
            outcomes[job_id] = OutcomeRecord(
                job_id=job_id, ts=float(ts), outcome=str(outcome)
            )
        return outcomes

    def record_outcome(self, job_id: str, outcome: str) -> dict[str, Any]:
        """Record one outcome for a decided merge. Event-path entry point.

        Fail-closed: refuses while disabled or misconfigured, on an unknown
        job_id, on a duplicate (append-only — no silent overwrites), and
        raises ``LearnInputError`` for an outcome outside the enum.
        """
        refusal = self._config_refusal_reason()
        if refusal is not None:
            self._emit_audit(
                "record_outcome",
                {"status": "refused", "reason": refusal, "job_id": job_id},
            )
            return {"status": "refused", "reason": refusal}
        if outcome not in OUTCOMES:
            raise LearnInputError(
                f"unknown outcome {outcome!r}: must be one of {sorted(OUTCOMES)}"
            )
        # Event path: tail-first membership checks. The just-decided merge
        # is at (or near) the log tail, so the common case never parses
        # the whole log. Validity rules match the full parsers exactly.
        if not _decision_has_job(self.decision_log, job_id):
            reason = f"unknown_job: no decision row for job_id {job_id!r}"
            self._emit_audit(
                "record_outcome",
                {
                    "status": "refused",
                    "reason": reason,
                    "job_id": job_id,
                    "outcome": outcome,
                },
            )
            return {"status": "refused", "reason": reason}
        if _outcome_recorded(self.outcome_log, job_id):
            reason = (
                f"outcome_already_recorded: job_id {job_id!r} has an outcome "
                "row; the outcome log is append-only"
            )
            self._emit_audit(
                "record_outcome",
                {
                    "status": "refused",
                    "reason": reason,
                    "job_id": job_id,
                    "outcome": outcome,
                },
            )
            return {"status": "refused", "reason": reason}
        row = {"job_id": job_id, "ts": self._now(), "outcome": outcome}
        try:
            self.outcome_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.outcome_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            reason = f"outcome_log_unwritable: {exc}"
            self._emit_audit(
                "record_outcome",
                {
                    "status": "refused",
                    "reason": reason,
                    "job_id": job_id,
                    "outcome": outcome,
                },
            )
            return {"status": "refused", "reason": reason}
        self._emit_audit(
            "record_outcome",
            {"status": "ok", "job_id": job_id, "outcome": outcome},
        )
        return {"status": "ok", "job_id": job_id, "outcome": outcome}

    # -- band-change log (loosening budget) ----------------------------

    def _recent_loosening(self, band_key: str, now: float) -> float:
        """Total loosening of one band key applied in the last 7 days.

        Reads the band-change log (written when a proposal is actually
        applied — a later phase). For ``human_above`` loosening means the
        value went DOWN; same for ``auto_below`` (refusing less of the low
        end). Only rows with numeric, sane values count.
        """
        total = 0.0
        for row in _read_jsonl(self.band_change_log):
            if row.get("band_key") != band_key:
                continue
            if row.get("direction") != DIRECTION_LOOSEN:
                continue
            ts = row.get("ts")
            old = row.get("old_value")
            new = row.get("new_value")
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                continue
            if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
                continue
            if ts > now or now - ts >= 7 * _SECONDS_PER_DAY:
                continue
            delta = float(old) - float(new)
            if delta > 0:
                total += delta
        return total

    # -- proposal machinery -------------------------------------------

    def _direction_for(self, band_key: str, old: float, new: float) -> str:
        """Classify a band move as tighten or loosen.

        Tightening makes auto-merge harder. Equal values are not a change
        (callers refuse them); defensively classify as tighten so a zero
        move can never ride the loosen path.
        """
        up_is_tighter = self.bands.tighten_direction[band_key] == "up"
        if new == old:
            return DIRECTION_TIGHTEN
        moved_up = new > old
        return DIRECTION_TIGHTEN if moved_up == up_is_tighter else DIRECTION_LOOSEN

    def _within_caps(self, band_key: str, new_value: float) -> bool:
        lo, hi = self.bands.caps[band_key]
        return lo <= new_value <= hi

    def _make_proposal(
        self,
        band_key: str,
        new_value: float,
        evidence: dict[str, Any],
        *,
        requires_michael: bool = False,
        reason: str = "",
    ) -> BandProposal:
        old_value = self.bands.values[band_key]
        direction = self._direction_for(band_key, old_value, new_value)
        return BandProposal(
            band_version=self.bands.version,
            band_key=band_key,
            old_value=_round2(old_value),
            new_value=_round2(new_value),
            direction=direction,
            evidence=evidence,
            requires_michael=requires_michael,
            reason=reason,
            proposed_spec_text=_build_proposed_spec_text(
                self.bands, band_key, new_value, evidence
            ),
        )

    def _loosen_evidence_ok(
        self,
        coverage_gaps: int,
        rollback_rate: float,
        consecutive_clean: int,
    ) -> Optional[str]:
        """Return a refusal reason if loosen evidence is insufficient."""
        if coverage_gaps > 0:
            return (
                "loosen_evidence_insufficient: signal coverage gaps in the "
                "decision log — cannot prove clean on incomplete data"
            )
        if rollback_rate > 0:
            return "loosen_evidence_insufficient: rollbacks in the review window"
        if consecutive_clean < self.policy.clean_outcomes_for_loosen:
            return (
                "loosen_evidence_insufficient: "
                f"{consecutive_clean} consecutive clean outcomes, need "
                f"{self.policy.clean_outcomes_for_loosen}"
            )
        return None

    def propose_band_change(self, band_key: str, new_value: float) -> dict[str, Any]:
        """Manually propose one band change. Tighten any time; loosen needs
        mechanical evidence (zero gaps, zero window rollbacks, N consecutive
        clean outcomes) AND weekly loosening budget. Beyond the budget the
        proposal is generated with ``requires_michael: true``.

        Never applies anything. Every call emits one audit row.
        """
        refusal = self._config_refusal_reason()
        if refusal is not None:
            self._emit_audit(
                "propose_band_change",
                {
                    "status": "refused",
                    "reason": refusal,
                    "band_key": band_key,
                    "new_value": new_value,
                },
            )
            return {"status": "refused", "reason": refusal}
        if band_key not in self.bands.values:
            reason = f"unknown_band_key: {band_key!r}"
            self._emit_audit(
                "propose_band_change",
                {
                    "status": "refused",
                    "reason": reason,
                    "band_key": band_key,
                    "new_value": new_value,
                },
            )
            return {"status": "refused", "reason": reason}
        if not isinstance(new_value, (int, float)) or isinstance(new_value, bool):
            reason = f"invalid_band_value: {new_value!r} is not numeric"
            self._emit_audit(
                "propose_band_change",
                {
                    "status": "refused",
                    "reason": reason,
                    "band_key": band_key,
                },
            )
            return {"status": "refused", "reason": reason}
        if not self._within_caps(band_key, float(new_value)):
            reason = (
                f"band_limit_exceeded: {band_key}={new_value} outside caps "
                f"{self.bands.caps[band_key]}"
            )
            self._emit_audit(
                "propose_band_change",
                {
                    "status": "refused",
                    "reason": reason,
                    "band_key": band_key,
                    "new_value": new_value,
                },
            )
            return {"status": "refused", "reason": reason}

        now = self._now()
        records, coverage_gaps = parse_decision_log(self.decision_log)
        outcomes = self._recorded_outcomes()
        window = self._window_stats(records, outcomes, now)

        direction = self._direction_for(
            band_key, self.bands.values[band_key], float(new_value)
        )
        requires_michael = False
        reason = ""
        if direction == DIRECTION_LOOSEN:
            evidence_refusal = self._loosen_evidence_ok(
                coverage_gaps,
                window["rollback_rate"],
                window["consecutive_clean"],
            )
            if evidence_refusal is not None:
                self._emit_audit(
                    "propose_band_change",
                    {
                        "status": "refused",
                        "reason": evidence_refusal,
                        "band_key": band_key,
                        "new_value": new_value,
                        "direction": direction,
                    },
                )
                return {"status": "refused", "reason": evidence_refusal}
            loosen_amount = self.bands.values[band_key] - float(new_value)
            budget_used = self._recent_loosening(band_key, now)
            if budget_used + loosen_amount > self.policy.max_loosen_per_week + _EPS:
                requires_michael = True
                reason = (
                    "loosening rate limit exceeded "
                    f"({budget_used:.2f} applied this week + "
                    f"{loosen_amount:.2f} proposed > "
                    f"{self.policy.max_loosen_per_week} cap) — "
                    "Michael's word required"
                )
        evidence = {
            "rollback_rate": window["rollback_rate"],
            "escalation_rate": window["escalation_rate"],
            "consecutive_clean": window["consecutive_clean"],
            "coverage_gaps": coverage_gaps,
            "window_days": self.policy.review_window_days,
        }
        proposal = self._make_proposal(
            band_key,
            float(new_value),
            evidence,
            requires_michael=requires_michael,
            reason=reason,
        )
        self._emit_audit(
            "propose_band_change",
            {
                "status": "ok",
                "band_key": band_key,
                "old_value": proposal.old_value,
                "new_value": proposal.new_value,
                "direction": direction,
                "requires_michael": requires_michael,
                "reason": reason,
            },
        )
        return {"status": "ok", "proposal": proposal}

    # -- the application path ------------------------------------------

    def apply_band_change(
        self,
        band_key: str,
        new_value: float,
        *,
        reason: str = "",
        by: str = "mbgulden",
        event_log: Optional[Path | str] = None,
    ) -> dict[str, Any]:
        """Validate, record, and file one band change.

        This is the learn loop's application path: ``propose_band_change``
        only ever proposes. Apply re-runs the full proposal validation,
        enforces the Michael gate (over-budget loosenings apply only when
        ``by == "mbgulden"``), then records FIRST and writes second --
        fail-closed: if the watchdog metrics-feed event cannot be
        recorded, nothing is written.

        On success a new versioned bands spec file is filed next to the
        loaded one (never overwrites the live file or an existing
        version), one row is appended to the band-change log (the format
        ``_recent_loosening`` reads), and one audit row is emitted.
        Returns ``{"status": "ok", ...}`` or
        ``{"status": "refused", "reason": ...}``.
        """
        outcome = self.propose_band_change(band_key, new_value)
        if outcome.get("status") != "ok":
            refusal = outcome.get("reason", "proposal refused")
            self._emit_audit(
                "apply_band_change",
                {
                    "status": "refused",
                    "reason": refusal,
                    "band_key": band_key,
                    "new_value": new_value,
                    "by": by,
                },
            )
            return {"status": "refused", "reason": refusal}
        proposal: BandProposal = outcome["proposal"]
        if proposal.requires_michael and by != "mbgulden":
            refusal = (
                "apply_band_change refused: this loosening exceeds the "
                "weekly budget -- Michael's word is required"
            )
            self._emit_audit(
                "apply_band_change",
                {
                    "status": "refused",
                    "reason": refusal,
                    "band_key": band_key,
                    "new_value": new_value,
                    "by": by,
                },
            )
            return {"status": "refused", "reason": refusal}

        new_version = _next_band_version(proposal.band_version)
        spec_path = _next_spec_path(self.bands_path, new_version)
        if spec_path.exists():
            refusal = f"apply_band_change refused: {spec_path} already exists"
            self._emit_audit(
                "apply_band_change",
                {
                    "status": "refused",
                    "reason": refusal,
                    "band_key": band_key,
                    "new_value": new_value,
                    "by": by,
                },
            )
            return {"status": "refused", "reason": refusal}

        # Record FIRST: the metrics-feed row is the audit signal for this
        # change ("no signal, no action"). A failed write raises here,
        # before any file is written.
        from prismatic.review_factory.metrics_feed import record_band_change

        change_reason = (
            reason
            or proposal.reason
            or (
                f"learn-loop {proposal.direction}: {band_key} "
                f"{proposal.old_value} -> {proposal.new_value}"
            )
        )
        record_band_change(
            band=band_key,
            old_value=proposal.old_value,
            new_value=proposal.new_value,
            reason=change_reason,
            event_log=event_log,
        )

        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(proposal.proposed_spec_text, encoding="utf-8")

        log_row = {
            "ts": self._now(),
            "band_key": band_key,
            "direction": proposal.direction,
            "old_value": proposal.old_value,
            "new_value": proposal.new_value,
        }
        self.band_change_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.band_change_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_row) + "\n")

        self._emit_audit(
            "apply_band_change",
            {
                "status": "ok",
                "band_key": band_key,
                "old_value": proposal.old_value,
                "new_value": proposal.new_value,
                "direction": proposal.direction,
                "spec_file": str(spec_path),
                "by": by,
            },
        )
        return {
            "status": "ok",
            "band_key": band_key,
            "old_value": proposal.old_value,
            "new_value": proposal.new_value,
            "direction": proposal.direction,
            "spec_file": str(spec_path),
        }

    # -- the batch rollup ---------------------------------------------

    def _window_stats(
        self,
        records: list[DecisionRecord],
        outcomes: dict[str, OutcomeRecord],
        now: float,
    ) -> dict[str, Any]:
        cutoff = now - self.policy.review_window_days * _SECONDS_PER_DAY
        # Single pass: accumulate the window counts and collect the
        # in-window allowed records (in log order) for the trailing
        # consecutive-clean run below. No eager default OutcomeRecord
        # construction — a missing outcome row reads as "" (unknown).
        n_allowed = 0
        refusals = 0
        rollbacks = 0
        escalations = 0
        cleans = 0
        windowed_allowed: list[DecisionRecord] = []
        for r in records:
            if r.ts < cutoff:
                continue
            if r.decision != "allowed":
                refusals += 1
                continue
            n_allowed += 1
            windowed_allowed.append(r)
            outcome = outcomes.get(r.job_id)
            outcome_name = outcome.outcome if outcome is not None else ""
            if outcome_name == OUTCOME_ROLLED_BACK:
                rollbacks += 1
            elif outcome_name == OUTCOME_ESCALATED:
                escalations += 1
            elif outcome_name == OUTCOME_CLEAN:
                cleans += 1
        # Consecutive clean: newest-first trailing run of allowed decisions
        # with an explicit clean outcome. Stops at rollback, escalation, or
        # a merge with no outcome row yet (unknown = not clean).
        consecutive_clean = 0
        for rec in sorted(windowed_allowed, key=lambda r: r.ts, reverse=True):
            outcome = outcomes.get(rec.job_id)
            if outcome is not None and outcome.outcome == OUTCOME_CLEAN:
                consecutive_clean += 1
            else:
                break
        return {
            "auto_merges": n_allowed,
            "refusals": refusals,
            "rollbacks": rollbacks,
            "escalations": escalations,
            "cleans": cleans,
            "consecutive_clean": consecutive_clean,
            "rollback_rate": (rollbacks / n_allowed) if n_allowed else 0.0,
            "escalation_rate": (escalations / n_allowed) if n_allowed else 0.0,
        }

    def self_review(self, now: Optional[float] = None) -> LearnReport:
        """Run the periodic batch rollup: aggregates, proposals, report.

        Returns a ``LearnReport`` with the plain-language ``summary``.
        Disabled or misconfigured → a ``disabled``/``invalid`` report; the
        one audit row is still emitted (no signal, no review).
        """
        if self._policy_error is not None or self._bands_error is not None:
            report = LearnReport(
                state="invalid",
                policy_version=self.policy.version,
                window_days=self.policy.review_window_days,
                summary="learn loop misconfigured; review refused",
            )
            self._emit_audit("self_review", {"status": "invalid", "state": "invalid"})
            return report
        if not self.policy.enabled:
            report = LearnReport(
                state="disabled",
                policy_version=self.policy.version,
                window_days=self.policy.review_window_days,
                summary="learn loop disabled",
            )
            self._emit_audit("self_review", {"status": "disabled", "state": "disabled"})
            return report

        now = self._now() if now is None else now
        records, coverage_gaps = parse_decision_log(self.decision_log)
        outcomes = self._recorded_outcomes()
        stats = self._window_stats(records, outcomes, now)

        proposals: list[BandProposal] = []
        key = "human_above"
        old = self.bands.values[key]

        # Tighten (any time): rollback pressure or escalation pressure.
        tighten_reasons: list[str] = []
        if stats["rollback_rate"] > self.policy.tighten_rollback_rate:
            tighten_reasons.append(
                f"rollback_rate {stats['rollback_rate']:.3f} > "
                f"{self.policy.tighten_rollback_rate}"
            )
        if stats["escalation_rate"] > self.policy.tighten_escalation_rate:
            tighten_reasons.append(
                f"escalation_rate {stats['escalation_rate']:.3f} > "
                f"{self.policy.tighten_escalation_rate}"
            )
        if tighten_reasons:
            new = min(old + self.policy.tighten_step, self.bands.caps[key][1])
            if new > old:
                proposals.append(
                    self._make_proposal(
                        key,
                        new,
                        {
                            "triggers": tighten_reasons,
                            "rollback_rate": stats["rollback_rate"],
                            "escalation_rate": stats["escalation_rate"],
                            "window_days": self.policy.review_window_days,
                        },
                    )
                )
        else:
            # Loosen: only with mechanical evidence AND weekly budget.
            evidence_refusal = self._loosen_evidence_ok(
                coverage_gaps,
                stats["rollback_rate"],
                stats["consecutive_clean"],
            )
            if evidence_refusal is None:
                new = max(old - self.policy.loosen_step, self.bands.caps[key][0])
                if new < old:
                    loosen_amount = old - new
                    budget_used = self._recent_loosening(key, now)
                    requires_michael = (
                        budget_used + loosen_amount
                        > self.policy.max_loosen_per_week + _EPS
                    )
                    reason = ""
                    if requires_michael:
                        reason = (
                            "loosening rate limit exceeded "
                            f"({budget_used:.2f} applied this week + "
                            f"{loosen_amount:.2f} proposed > "
                            f"{self.policy.max_loosen_per_week} cap) — "
                            "Michael's word required"
                        )
                    proposals.append(
                        self._make_proposal(
                            key,
                            new,
                            {
                                "rollback_rate": stats["rollback_rate"],
                                "consecutive_clean": stats["consecutive_clean"],
                                "required_clean": self.policy.clean_outcomes_for_loosen,
                                "weekly_loosen_budget": self.policy.max_loosen_per_week,
                                "weekly_loosen_used": _round2(budget_used),
                                "window_days": self.policy.review_window_days,
                            },
                            requires_michael=requires_michael,
                            reason=reason,
                        )
                    )

        summary = self._render_summary(stats, coverage_gaps, proposals)
        report = LearnReport(
            state="reviewed",
            policy_version=self.policy.version,
            window_days=self.policy.review_window_days,
            auto_merges=stats["auto_merges"],
            refusals=stats["refusals"],
            rollbacks=stats["rollbacks"],
            escalations=stats["escalations"],
            cleans=stats["cleans"],
            rollback_rate=stats["rollback_rate"],
            escalation_rate=stats["escalation_rate"],
            coverage_gaps=coverage_gaps,
            consecutive_clean=stats["consecutive_clean"],
            proposals=tuple(proposals),
            summary=summary,
        )
        self._emit_audit(
            "self_review",
            {
                "status": "ok",
                "state": "reviewed",
                "auto_merges": report.auto_merges,
                "refusals": report.refusals,
                "rollbacks": report.rollbacks,
                "escalations": report.escalations,
                "cleans": report.cleans,
                "coverage_gaps": coverage_gaps,
                "consecutive_clean": stats["consecutive_clean"],
                "proposals": len(proposals),
            },
        )
        return report

    # -- plain-language report -----------------------------------------

    @staticmethod
    def _render_summary(
        stats: dict[str, Any],
        coverage_gaps: int,
        proposals: list[BandProposal],
    ) -> str:
        parts = [
            f"{stats['auto_merges']} auto-merges, "
            f"{stats['rollbacks']} rollbacks, "
            f"{stats['escalations']} escalations, "
            f"{stats['cleans']} clean in the review window"
        ]
        if coverage_gaps:
            parts.append(
                f"{coverage_gaps} decision-log rows missing audit fields "
                "(loosening forbidden until coverage is complete)"
            )
        for proposal in proposals:
            line = (
                f"proposal: {proposal.direction} {proposal.band_key} "
                f"{proposal.old_value:.2f} → {proposal.new_value:.2f}"
            )
            if proposal.requires_michael:
                line += " (requires Michael's word — loosening rate limit)"
            parts.append(line)
        if not proposals:
            parts.append("no band changes proposed")
        return " — ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# CLI: the weekly self-review job
# ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    """Run the periodic learn-loop self-review from a scheduler.

    This is the pure core of the weekly systemd timer: it runs
    ``self_review()`` and prints the resulting report. Inert while the
    learn loop is disabled — ``self_review()`` returns the ``"disabled"``
    report, one audit row is emitted, and nothing else happens. The loop
    activates only when the rollout ladder advances the learn policy
    (a phase-advancement step on Michael's word).

    The ``correct`` subcommand records a human "Jev was right" agreement
    correction for one shadow PR: append-only, the original record is
    never mutated.

    Exit code 0 = the review ran (reviewed, disabled, or invalid), or the
    correction was recorded (or was already recorded).
    Exit code 1 = misconfigured CLI input (not a review verdict), or a
    refused correction.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Learn-loop self-review: aggregate decision/outcome logs, "
            "propose band changes, print the report. "
            "Inert while the learn policy is disabled."
        )
    )
    subparsers = parser.add_subparsers(dest="command")
    correct_parser = subparsers.add_parser(
        "correct",
        help=(
            "record a human agreement correction for one shadow PR "
            "('Jev was right'): append-only, original records preserved"
        ),
    )
    correct_parser.add_argument(
        "--pr", type=int, required=True, help="shadow PR number to correct"
    )
    correct_parser.add_argument(
        "--verdict",
        required=True,
        choices=[CORRECTION_JEV_WAS_RIGHT],
        help="the human's verdict on their own override",
    )
    correct_parser.add_argument(
        "--note", default="", help="optional free-text note on the correction"
    )
    correct_parser.add_argument(
        "--by",
        default="mbgulden",
        help="who is teaching (the correction is a human judgment)",
    )
    correct_parser.add_argument(
        "--shadow-records",
        default=str(DEFAULT_SHADOW_RECORDS_FILE),
        help="joined shadow records JSONL "
        "(one {system_call, actual_outcome, pr_number, ...} row per PR)",
    )
    correct_parser.add_argument(
        "--corrections-log",
        default=str(DEFAULT_CORRECTIONS_LOG),
        help="append-only corrections log",
    )
    apply_parser = subparsers.add_parser(
        "apply",
        help=(
            "validate, record, and file one band change "
            "(writes a new versioned bands spec; never overwrites live)"
        ),
    )
    apply_parser.add_argument("--band-key", required=True, help="band key to move")
    apply_parser.add_argument(
        "--value", type=float, required=True, help="new band value"
    )
    apply_parser.add_argument("--reason", default="", help="why the band is moving")
    apply_parser.add_argument(
        "--by",
        default="mbgulden",
        help="who is applying (over-budget loosenings require mbgulden)",
    )
    apply_parser.add_argument(
        "--event-log", default=None, help="metrics-feed event log override"
    )
    parser.add_argument("--policy", default=str(DEFAULT_LEARN_POLICY_FILE))
    parser.add_argument("--bands", default=str(DEFAULT_BANDS_FILE))
    parser.add_argument("--decision-log", default=str(DEFAULT_DECISION_LOG))
    parser.add_argument("--outcome-log", default=str(DEFAULT_OUTCOME_LOG))
    parser.add_argument("--band-change-log", default=str(DEFAULT_BAND_CHANGE_LOG))
    parser.add_argument("--audit-log", default=str(DEFAULT_AUDIT_LOG))
    args = parser.parse_args(argv)

    if args.command == "correct":
        return _correct_command(args)
    if args.command == "apply":
        return _apply_command(args)

    try:
        loop = LearnLoop(
            policy_path=args.policy,
            bands_path=args.bands,
            decision_log=args.decision_log,
            outcome_log=args.outcome_log,
            band_change_log=args.band_change_log,
            audit_log=args.audit_log,
        )
    except LearnConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    report = loop.self_review()
    print(
        json.dumps(
            {
                "status": report.state,
                "policy_version": report.policy_version,
                "window_days": report.window_days,
                "auto_merges": report.auto_merges,
                "refusals": report.refusals,
                "rollbacks": report.rollbacks,
                "escalations": report.escalations,
                "cleans": report.cleans,
                "coverage_gaps": report.coverage_gaps,
                "consecutive_clean": report.consecutive_clean,
                "proposals": len(report.proposals),
                "summary": report.summary,
            },
            indent=2,
        )
    )
    return 0


def _read_records_jsonl(path: str) -> list[dict[str, Any]]:
    """Read a JSONL file of record dicts, skipping corrupt lines."""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _correct_command(args: argparse.Namespace) -> int:
    """Implement ``learn_loop correct``.

    Records the human's explicit "Jev was right" teaching for one shadow
    PR: the override was the human's mistake, not Jev's. Append-only —
    the correction row goes to the corrections log; the original shadow
    record is never mutated. ``apply_corrections`` (shadow_agreement)
    folds corrections into the metric at read time.

    Fail-closed: refuses when there is no shadow record for the PR, when
    the record agrees (or is undecided), and when the verdict is
    incoherent (D1: Jev said merge, the PR was closed — there is no
    "Jev was right" to record there).
    """
    try:
        records = _read_records_jsonl(args.shadow_records)
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": f"cannot read shadow records: {exc}",
                }
            )
        )
        return 1

    matches = [r for r in records if str(r.get("pr_number")) == str(args.pr)]
    if not matches:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": f"no shadow record for PR #{args.pr}",
                }
            )
        )
        return 1
    if len(matches) > 1:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": (
                        f"multiple shadow records for PR #{args.pr}; correct manually"
                    ),
                }
            )
        )
        return 1
    record = matches[0]

    try:
        cls = classify_disagreement(record)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": f"PR #{args.pr}: unclassifiable record ({exc})",
                }
            )
        )
        return 1

    if cls is None:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": (
                        f"PR #{args.pr}: Jev's call and the outcome agree "
                        "(or the PR is still open); nothing to correct"
                    ),
                }
            )
        )
        return 1
    if cls == CLASS_TOO_AGGRESSIVE:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": (
                        f"PR #{args.pr}: jev-was-right does not apply "
                        "(D1: Jev said merge, the PR was closed)"
                    ),
                }
            )
        )
        return 1
    if cls == CLASS_JEV_WAS_RIGHT or record.get("corrected_by"):
        print(
            json.dumps(
                {
                    "status": "already_recorded",
                    "pr_number": args.pr,
                    "class": cls,
                }
            )
        )
        return 0

    entry = {
        "schema": CORRECTION_SCHEMA,
        "pr_number": args.pr,
        "verdict": args.verdict,
        "previous_class": cls,
        "note": args.note,
        "corrected_by": args.by,
        "corrected_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path = Path(args.corrections_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        print(
            json.dumps({"status": "error", "error": f"cannot append correction: {exc}"})
        )
        return 1
    print(json.dumps({"status": "recorded", **entry}, indent=2))
    return 0


def _apply_command(args: argparse.Namespace) -> int:
    """Implement ``learn_loop apply``.

    Validates, records, and files one band change through
    ``LearnLoop.apply_band_change``. Exit 0 = applied; exit 1 =
    refused, misconfigured, or a feed/write error (fail-closed: on a
    metrics-feed write failure nothing is filed).
    """
    try:
        loop = LearnLoop(
            policy_path=args.policy,
            bands_path=args.bands,
            decision_log=args.decision_log,
            outcome_log=args.outcome_log,
            band_change_log=args.band_change_log,
            audit_log=args.audit_log,
        )
    except LearnConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    try:
        result = loop.apply_band_change(
            args.band_key,
            args.value,
            reason=args.reason,
            by=args.by,
            event_log=args.event_log,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
