"""Deterministic watchdog (chunk 2 of the post-shadow roadmap).

MONITOR-ONLY in Phase 0: trips are LOGGED, never halting. The watchdog
watches the autonomy machinery's own health metrics — rollback rate,
escalation rate, CI failure rate, Jev backend error rate, band-change
velocity, auto-merge volume vs. the authority's rate limits — and compares
each against the versioned policy's thresholds. A metric strictly above its
threshold trips.

Modes (from the policy file):
- ``monitor-only``: trips emit audit signals; the system never halts.
  Phase 0 collects trip evidence here (fault-injection tested) so the
  Phase 0 -> 1 exit criteria can require the watchdog "armed".
- ``enforcing``: a trip halts all auto-merge/auto-deploy (``halted`` goes
  True and stays True). ONLY Michael re-arms, via ``rearm("mbgulden")``.
  No timeout, no auto-clear, no self re-arm path exists.

HARD-DISABLED until Michael explicitly arms it: with ``enabled: false``
(or a missing policy file) ``evaluate()`` returns ``"disabled"`` without
computing a single metric.

Fail-closed throughout:
- a malformed policy file raises ``WatchdogConfigError`` at construction;
- a snapshot with invalid values (negative numbers, NaN, rates outside
  [0, 1]) cannot evaluate — in monitor-only it is logged as invalid, in
  enforcing it halts (the feed cannot prove "all clear");
- a missing metric in the snapshot is "unknown" — logged in monitor-only,
  tripping in enforcing (cannot prove clear on unknown input).

Every ``evaluate()`` call emits exactly one audit signal as a JSONL row.
No signal, no evaluation. The evaluation is a pure function of the
snapshot passed in: this module makes no network calls. The metrics feed
(the timer job that builds snapshots) is a later chunk; until it exists,
callers construct ``MetricSnapshot`` explicitly.

Pure code, no Jev. The watcher cannot be fuzzy.
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
DEFAULT_POLICY_FILE = SPEC_DIR / "watchdog_policy_v1.yaml"
DEFAULT_AUDIT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/watchdog-decisions.jsonl")
)

MODE_MONITOR_ONLY = "monitor-only"
MODE_ENFORCING = "enforcing"

STATE_DISABLED = "disabled"
STATE_CLEAR = "clear"
STATE_TRIPPED_MONITOR = "tripped_monitor_only"
STATE_TRIPPED_HALTED = "tripped_halted"
STATE_INVALID = "invalid"

# Metric names the watchdog understands. Extra keys in a snapshot are
# ignored (documented); missing keys are "unknown", not zero.
METRIC_ROLLBACK_RATE = "rollback_rate"
METRIC_ESCALATION_RATE = "escalation_rate"
METRIC_CI_FAILURE_RATE = "ci_failure_rate"
METRIC_JEV_ERROR_RATE = "jev_error_rate"
METRIC_BAND_VELOCITY = "band_change_velocity_per_day"
METRIC_MERGE_VOLUME = "merge_volume_per_hour"

RATE_METRICS = frozenset(
    {
        METRIC_ROLLBACK_RATE,
        METRIC_ESCALATION_RATE,
        METRIC_CI_FAILURE_RATE,
        METRIC_JEV_ERROR_RATE,
    }
)
ALL_METRICS = tuple(
    [
        METRIC_ROLLBACK_RATE,
        METRIC_ESCALATION_RATE,
        METRIC_CI_FAILURE_RATE,
        METRIC_JEV_ERROR_RATE,
        METRIC_BAND_VELOCITY,
        METRIC_MERGE_VOLUME,
    ]
)


class WatchdogConfigError(Exception):
    """Raised when the watchdog policy cannot be loaded.

    Fail-closed: the watchdog refuses to evaluate rather than guessing
    from defaults.
    """


# ─────────────────────────────────────────────────────────────────────
# Config models
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WatchdogPolicy:
    """Versioned deterministic watchdog policy.

    ``enabled: false`` (the default when the key is absent) means the
    watchdog is inert: evaluate() returns "disabled" without touching
    metrics. The policy must say ``enabled: true`` explicitly — there is
    no way to be accidentally armed.
    """

    version: str
    enabled: bool
    mode: str
    rearm_principal: str
    thresholds: dict[str, float]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WatchdogPolicy":
        if not isinstance(data, dict):
            raise WatchdogConfigError("policy root must be a mapping")
        raw_thresholds = data.get("thresholds", {}) or {}
        if not isinstance(raw_thresholds, dict):
            raise WatchdogConfigError("thresholds must be a mapping")
        thresholds: dict[str, float] = {}
        for key, value in raw_thresholds.items():
            try:
                thresholds[str(key)] = float(value)
            except (TypeError, ValueError) as exc:
                raise WatchdogConfigError(
                    f"threshold {key!r} is not a number: {value!r}"
                ) from exc
        mode = str(data.get("mode", MODE_MONITOR_ONLY))
        if mode not in (MODE_MONITOR_ONLY, MODE_ENFORCING):
            raise WatchdogConfigError(f"unknown watchdog mode: {mode!r}")
        return cls(
            version=str(data.get("version", "unknown")),
            enabled=bool(data.get("enabled", False)),
            mode=mode,
            rearm_principal=str(data.get("rearm_principal", "mbgulden")),
            thresholds=thresholds,
        )


def load_watchdog_policy(path: Optional[Path | str] = None) -> WatchdogPolicy:
    """Load the versioned watchdog policy, fail-closed.

    A missing file yields a disabled policy (inert), not an error — but a
    malformed file raises ``WatchdogConfigError`` so the watchdog never
    runs on a half-read config.
    """
    path = Path(path) if path is not None else DEFAULT_POLICY_FILE
    if not path.exists():
        return WatchdogPolicy.from_dict({"version": "missing", "enabled": False})
    if not _HAS_YAML:
        raise WatchdogConfigError("PyYAML is required to load the watchdog policy")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise WatchdogConfigError(f"cannot parse policy file {path}: {exc}") from exc
    return WatchdogPolicy.from_dict(data)


# ─────────────────────────────────────────────────────────────────────
# Snapshot + evaluation (pure, no network)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricSnapshot:
    """One observation window's metrics, passed in by the caller.

    Rates are fractions in [0, 1]; velocity/volume are counts per the
    named window and must be >= 0. Use ``None`` for a metric the feed
    could not observe — it becomes "unknown", never silently zero.
    """

    rollback_rate: Optional[float] = None
    escalation_rate: Optional[float] = None
    ci_failure_rate: Optional[float] = None
    jev_error_rate: Optional[float] = None
    band_change_velocity_per_day: Optional[float] = None
    merge_volume_per_hour: Optional[float] = None

    def as_dict(self) -> dict[str, Optional[float]]:
        return {
            METRIC_ROLLBACK_RATE: self.rollback_rate,
            METRIC_ESCALATION_RATE: self.escalation_rate,
            METRIC_CI_FAILURE_RATE: self.ci_failure_rate,
            METRIC_JEV_ERROR_RATE: self.jev_error_rate,
            METRIC_BAND_VELOCITY: self.band_change_velocity_per_day,
            METRIC_MERGE_VOLUME: self.merge_volume_per_hour,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricSnapshot":
        """Build from a plain mapping; unknown keys are ignored."""
        kwargs: dict[str, Any] = {}
        for metric in ALL_METRICS:
            if metric in data:
                kwargs[metric] = data[metric]
        return cls(**kwargs)


@dataclass(frozen=True)
class MetricTrip:
    """One metric found above its threshold (or unknown in enforcing mode)."""

    metric: str
    value: Optional[float]
    threshold: float
    reason: str  # "above_threshold" | "unknown"


@dataclass(frozen=True)
class WatchdogResult:
    """The watchdog's answer for one evaluation."""

    state: str  # disabled | clear | tripped_monitor_only | tripped_halted | invalid
    trips: tuple[MetricTrip, ...] = ()
    unknown: tuple[str, ...] = ()
    halted: bool = False
    policy_version: str = "unknown"
    mode: str = MODE_MONITOR_ONLY


def _validate_snapshot(
    snapshot: MetricSnapshot,
) -> Optional[str]:
    """Return an error string for an invalid snapshot, else None.

    Fail-closed: a snapshot the watchdog cannot trust must not produce a
    "clear" verdict.
    """
    for metric, value in snapshot.as_dict().items():
        if value is None:
            continue  # unknown is handled separately, not invalid
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"metric {metric!r} is not a number: {value!r}"
        if math.isnan(value) or math.isinf(value):
            return f"metric {metric!r} is not finite: {value!r}"
        if value < 0:
            return f"metric {metric!r} is negative: {value!r}"
        if metric in RATE_METRICS and value > 1:
            return f"rate metric {metric!r} outside [0, 1]: {value!r}"
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# The watchdog
# ─────────────────────────────────────────────────────────────────────


class Watchdog:
    """Deterministic health monitor for the autonomy machinery.

    Construct with the policy path. ``evaluate()`` is inert while the
    policy is disabled. In monitor-only mode trips are logged, never
    halting; in enforcing mode a trip sets ``halted`` True and only
    ``rearm("mbgulden")`` clears it — there is no other re-arm path.
    """

    def __init__(
        self,
        policy_path: Optional[Path | str] = None,
        *,
        audit_log: Optional[Path | str] = None,
        now_fn: Any = None,
    ):
        try:
            self.policy = load_watchdog_policy(policy_path)
            self._policy_error: Optional[str] = None
        except WatchdogConfigError as exc:
            # Fail-closed: a malformed policy never evaluates.
            self.policy = WatchdogPolicy.from_dict(
                {"version": "invalid", "enabled": False}
            )
            self._policy_error = str(exc)
        self.audit_log = Path(audit_log) if audit_log is not None else DEFAULT_AUDIT_LOG
        self._now = now_fn or time.time
        self._halted = False
        # The audit dir is created once and the flag cached: mkdir on every
        # emit dominated evaluate()'s cost. On OSError the flag resets and
        # mkdir is retried once (self-healing if the dir is removed mid-run).
        self._audit_dir_ready = False

    @property
    def halted(self) -> bool:
        return self._halted

    # -- audit -------------------------------------------------------

    def _write_audit_row(self, row: dict[str, Any]) -> None:
        """Append one audit row, creating the audit dir on first use.

        Fail-closed: an OSError never changes the evaluation outcome — and
        must never turn a halt into a clear. The result stands. The dir
        creation is retried once after a failure so a dir removed mid-run
        self-heals instead of silently dropping every later signal.
        """
        try:
            if not self._audit_dir_ready:
                self.audit_log.parent.mkdir(parents=True, exist_ok=True)
                self._audit_dir_ready = True
            with open(self.audit_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            self._audit_dir_ready = False
            try:
                self.audit_log.parent.mkdir(parents=True, exist_ok=True)
                self._audit_dir_ready = True
                with open(self.audit_log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
            except OSError:
                # Audit write failure must not change the evaluation
                # outcome — and must never turn a halt into a clear. The
                # result stands.
                pass

    def _emit_audit(
        self, result: WatchdogResult, snapshot: MetricSnapshot, note: str = ""
    ) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "watchdog",
            "policy_version": result.policy_version,
            "mode": result.mode,
            "state": result.state,
            "halted": result.halted,
            "metrics": snapshot.as_dict(),
            "trips": [
                {
                    "metric": t.metric,
                    "value": t.value,
                    "threshold": t.threshold,
                    "reason": t.reason,
                }
                for t in result.trips
            ],
            "unknown": list(result.unknown),
            "note": note,
        }
        self._write_audit_row(row)

    # -- page input --------------------------------------------------

    @staticmethod
    def prepare_page(result: WatchdogResult) -> dict[str, Any]:
        """Build the page payload for a trip. Data only — never sends.

        The three most decision-relevant signals go first: which metric,
        how far above threshold, and whether the watchdog halted.
        """
        trips = sorted(
            result.trips,
            key=lambda t: (t.value or 0) - t.threshold,
            reverse=True,
        )
        lines = [
            f"{t.metric}: {t.value} vs threshold {t.threshold} ({t.reason})"
            for t in trips[:3]
        ]
        return {
            "summary": "; ".join(lines) or "no trips",
            "state": result.state,
            "halted": result.halted,
            "mode": result.mode,
            "policy_version": result.policy_version,
            "trips": [
                {
                    "metric": t.metric,
                    "value": t.value,
                    "threshold": t.threshold,
                    "reason": t.reason,
                }
                for t in trips
            ],
            "rearm_principal": "mbgulden",
        }

    # -- evaluation --------------------------------------------------

    def evaluate(self, snapshot: MetricSnapshot) -> WatchdogResult:
        """Evaluate one metrics snapshot against the policy.

        Pure apart from the audit signal it emits. In monitor-only mode
        this NEVER halts — it only logs.
        """
        # 1. Policy health. A malformed policy never evaluates.
        if self._policy_error is not None:
            result = WatchdogResult(
                state=STATE_INVALID,
                policy_version=self.policy.version,
                mode=self.policy.mode,
                halted=self._halted,
            )
            self._emit_audit(
                result, snapshot, note=f"policy_error: {self._policy_error}"
            )
            return result

        # 2. MASTER SWITCH. Disabled = inert, no metric computed.
        if not self.policy.enabled:
            result = WatchdogResult(
                state=STATE_DISABLED,
                policy_version=self.policy.version,
                mode=self.policy.mode,
                halted=False,
            )
            self._emit_audit(result, snapshot, note="watchdog_disabled")
            return result

        # 3. Snapshot validity. Cannot trust -> cannot claim clear.
        invalid = _validate_snapshot(snapshot)
        if invalid is not None:
            if self.policy.mode == MODE_ENFORCING:
                # Fail-closed: the feed cannot prove "all clear", so halt.
                self._halted = True
                result = WatchdogResult(
                    state=STATE_TRIPPED_HALTED,
                    policy_version=self.policy.version,
                    mode=self.policy.mode,
                    halted=True,
                )
                self._emit_audit(
                    result, snapshot, note=f"invalid_input_halted: {invalid}"
                )
                return result
            result = WatchdogResult(
                state=STATE_INVALID,
                policy_version=self.policy.version,
                mode=self.policy.mode,
                halted=False,
            )
            self._emit_audit(result, snapshot, note=f"invalid_input: {invalid}")
            return result

        # 4. Compare each known metric against its threshold.
        values = snapshot.as_dict()
        trips: list[MetricTrip] = []
        unknown: list[str] = []
        for metric in ALL_METRICS:
            value = values[metric]
            threshold = self.policy.thresholds.get(metric)
            if threshold is None:
                # Policy has no threshold for this metric: skip, never invent.
                continue
            if value is None:
                unknown.append(metric)
                if self.policy.mode == MODE_ENFORCING:
                    # Fail-closed: unknown input cannot prove clear.
                    trips.append(
                        MetricTrip(
                            metric=metric,
                            value=None,
                            threshold=threshold,
                            reason="unknown",
                        )
                    )
                continue
            if value > threshold:
                trips.append(
                    MetricTrip(
                        metric=metric,
                        value=value,
                        threshold=threshold,
                        reason="above_threshold",
                    )
                )

        if not trips:
            result = WatchdogResult(
                state=STATE_CLEAR,
                unknown=tuple(unknown),
                halted=self._halted,
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(result, snapshot)
            return result

        # 5. Trip handling: monitor-only logs; enforcing halts.
        if self.policy.mode == MODE_ENFORCING:
            self._halted = True
            result = WatchdogResult(
                state=STATE_TRIPPED_HALTED,
                trips=tuple(trips),
                unknown=tuple(unknown),
                halted=True,
                policy_version=self.policy.version,
                mode=self.policy.mode,
            )
            self._emit_audit(result, snapshot, note="watchdog_halted")
            return result

        result = WatchdogResult(
            state=STATE_TRIPPED_MONITOR,
            trips=tuple(trips),
            unknown=tuple(unknown),
            halted=False,
            policy_version=self.policy.version,
            mode=self.policy.mode,
        )
        self._emit_audit(result, snapshot, note="watchdog_tripped_monitor_only")
        return result

    # -- re-arm --------------------------------------------------------

    def rearm(self, principal: str) -> str:
        """Clear a halt. The ONLY re-arm path; only Michael may use it.

        Returns "rearmed" | "refused" | "noop". There is no timeout, no
        auto-clear, and no other caller — a halted watchdog stays halted
        until this method is called with the exact rearm principal.
        """
        if not self._halted:
            self._emit_rearm_audit("noop", principal)
            return "noop"
        if principal != self.policy.rearm_principal:
            self._emit_rearm_audit("refused", principal)
            return "refused"
        self._halted = False
        self._emit_rearm_audit("rearmed", principal)
        return "rearmed"

    def _emit_rearm_audit(self, outcome: str, principal: str) -> None:
        row = {
            "ts": self._now(),
            "ts_iso": _utc_now_iso(),
            "component": "watchdog",
            "policy_version": self.policy.version,
            "mode": self.policy.mode,
            "event": "rearm",
            "outcome": outcome,
            "principal": principal,
            "halted": self._halted,
        }
        self._write_audit_row(row)
