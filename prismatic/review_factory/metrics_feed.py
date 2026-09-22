"""Event-sourced metrics feed for the deterministic watchdog.

The watchdog (``watchdog.py``) evaluates a ``MetricSnapshot`` but builds no
snapshots itself. This module is the other half: it records the events the
watchdog's metrics are computed from, and — on a short cadence — rolls them
up into a snapshot and hands it to the watchdog.

Event-based inputs, batch rollup:
- ``record_*()`` functions append validated event rows to the feed's event
  log (the audit sink) the moment things happen: a rollback, an escalation,
  a CI result, a Jev backend call, a band change. Each appended row IS the
  audit signal for the recording decision (no signal, no action).
- ``run_feed()`` reads the event log over trailing windows, builds the six
  rates the watchdog understands, and calls ``Watchdog.evaluate()``.
  Rolling-window rate computation is the batch-rollup case the event-first
  rule permits polling for. A systemd timer (installed separately, only when
  the feed is wanted live) calls ``main()`` every few minutes.

HARD-DISABLED until the feed's own versioned config says ``enabled: true``.
``run_feed()`` on a disabled feed returns ``"disabled"`` without building a
snapshot, constructing the watchdog, or emitting any signal.

Fail-closed throughout:
- unknown event types and invalid payloads are REJECTED
  (``MetricsFeedError``) — nothing bad is ever appended;
- a failed event write raises: the caller must know the event was not
  recorded (callers in hot paths wrap this);
- corrupt lines in the logs are skipped on read, never crashing the rollup;
- a rate with an empty denominator is ``None`` (unknown) — "no data" must
  never read as "all clear"; the watchdog's own fail-closed logic then
  decides (logged as unknown in monitor-only, trips in enforcing);
- a malformed feed config raises at load; a missing one yields a disabled
  feed (inert).

This module never changes watchdog behavior: it only supplies numbers. The
watchdog's policy decides disabled / monitor-only / enforcing, exactly as
before. Pure code, no Jev, no network.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _HAS_YAML = False

from prismatic.review_factory.watchdog import MetricSnapshot, Watchdog

SPEC_DIR = Path(__file__).resolve().parent / "spec"
DEFAULT_FEED_CONFIG_FILE = SPEC_DIR / "watchdog_metrics_feed_v1.yaml"
DEFAULT_EVENT_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/watchdog-metrics-events.jsonl")
)
DEFAULT_AUTO_MERGE_LOG = Path(
    os.path.expanduser("~/.prismatic/audit/auto-merge-decisions.jsonl")
)

# Event types the feed records. Anything else is rejected, never appended.
EVENT_ROLLBACK = "rollback"
EVENT_ESCALATION = "escalation"
EVENT_CI_RESULT = "ci_result"
EVENT_JEV_CALL = "jev_call"
EVENT_BAND_CHANGE = "band_change"

ALL_EVENTS = (
    EVENT_ROLLBACK,
    EVENT_ESCALATION,
    EVENT_CI_RESULT,
    EVENT_JEV_CALL,
    EVENT_BAND_CHANGE,
)

# Trailing windows (hours) used when the config omits a key. The shipped
# versioned config sets all of these explicitly; the defaults exist so a
# hand-rolled config is still well-defined, never guessed.
DEFAULT_WINDOWS = {
    "rollback_rate_hours": 168,  # 7 days of allowed merges
    "escalation_rate_hours": 24,
    "ci_failure_rate_hours": 24,
    "jev_error_rate_hours": 24,
    "band_change_velocity_hours": 24,
    "merge_volume_per_hour_hours": 1,
}

# Merge-authority decision values (see merge_authority.DECISION_ALLOWED /
# DECISION_REFUSED). Kept as literals so this module never imports the
# authority — the feed reads its audit log, not its code.
DECISION_ALLOWED = "allowed"
DECISION_REFUSED = "refused"


class MetricsFeedError(Exception):
    """Raised when the metrics feed cannot record or configure safely.

    Fail-closed: invalid input is rejected, never half-recorded; a bad
    config refuses to load rather than guessing.
    """


# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricsFeedConfig:
    """Versioned config for the watchdog metrics feed.

    ``enabled: false`` (the default when the key is absent) means
    ``run_feed()`` is inert: no snapshot, no watchdog, no signals. The
    config must say ``enabled: true`` explicitly — there is no way to be
    accidentally on.
    """

    version: str
    enabled: bool
    event_log: str
    auto_merge_log: str
    windows: dict[str, float]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricsFeedConfig":
        if not isinstance(data, dict):
            raise MetricsFeedError("feed config root must be a mapping")
        raw_windows = data.get("windows", {}) or {}
        if not isinstance(raw_windows, dict):
            raise MetricsFeedError("windows must be a mapping")
        windows: dict[str, float] = dict(DEFAULT_WINDOWS)
        for key, value in raw_windows.items():
            try:
                hours = float(value)
            except (TypeError, ValueError) as exc:
                raise MetricsFeedError(
                    f"window {key!r} is not a number: {value!r}"
                ) from exc
            if hours <= 0:
                raise MetricsFeedError(f"window {key!r} must be positive")
            windows[str(key)] = hours
        return cls(
            version=str(data.get("version", "unknown")),
            enabled=bool(data.get("enabled", False)),
            event_log=str(data.get("event_log", str(DEFAULT_EVENT_LOG))),
            auto_merge_log=str(data.get("auto_merge_log", str(DEFAULT_AUTO_MERGE_LOG))),
            windows=windows,
        )


def load_metrics_feed_config(
    path: Optional[Path | str] = None,
) -> MetricsFeedConfig:
    """Load the versioned feed config, fail-closed.

    A missing file yields a disabled config (inert), not an error — but a
    malformed file raises ``MetricsFeedError`` so the feed never runs on a
    half-read config.
    """
    path = Path(path) if path is not None else DEFAULT_FEED_CONFIG_FILE
    if not path.exists():
        return MetricsFeedConfig.from_dict({"version": "missing", "enabled": False})
    if not _HAS_YAML:
        raise MetricsFeedError("PyYAML is required to load the feed config")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        raise MetricsFeedError(f"cannot parse feed config {path}: {exc}") from exc
    return MetricsFeedConfig.from_dict(data)


# ─────────────────────────────────────────────────────────────────────
# Event recording (event-sourced inputs)
# ─────────────────────────────────────────────────────────────────────


def _nonempty_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MetricsFeedError(f"event field {key!r} must be a non-empty string")
    return value


def _strict_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise MetricsFeedError(f"event field {key!r} must be a bool")
    return value


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricsFeedError(f"event field {key!r} must be a number")
    return float(value)


def _validate_rollback(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": _nonempty_str(payload, "job_id"),
        "merge_sha": _nonempty_str(payload, "merge_sha"),
        "reason": _nonempty_str(payload, "reason"),
    }


def _validate_escalation(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": _nonempty_str(payload, "job_id"),
        "reason": _nonempty_str(payload, "reason"),
    }


def _validate_ci_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if result not in ("pass", "fail"):
        raise MetricsFeedError(
            f"ci_result field 'result' must be 'pass' or 'fail', got {result!r}"
        )
    return {
        "result": result,
        "runner": _nonempty_str(payload, "runner"),
        "fault_injection": _strict_bool(payload, "fault_injection"),
    }


def _validate_jev_call(payload: dict[str, Any]) -> dict[str, Any]:
    error = payload.get("error", "")
    if not isinstance(error, str):
        raise MetricsFeedError("jev_call field 'error' must be a string")
    return {
        "call_site": _nonempty_str(payload, "call_site"),
        "ok": _strict_bool(payload, "ok"),
        "error": error,
    }


def _validate_band_change(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "band": _nonempty_str(payload, "band"),
        "old_value": _number(payload, "old_value"),
        "new_value": _number(payload, "new_value"),
        "reason": _nonempty_str(payload, "reason"),
    }


_VALIDATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    EVENT_ROLLBACK: _validate_rollback,
    EVENT_ESCALATION: _validate_escalation,
    EVENT_CI_RESULT: _validate_ci_result,
    EVENT_JEV_CALL: _validate_jev_call,
    EVENT_BAND_CHANGE: _validate_band_change,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(
    event_type: str,
    payload: dict[str, Any],
    *,
    event_log: Optional[Path | str] = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Append one validated event to the feed's event log (the audit sink).

    The appended row IS the audit signal for this recording — no signal, no
    action. Returns the row that was written.

    Fail-closed: an unknown event type or an invalid payload raises
    ``MetricsFeedError`` and appends NOTHING. A failed write also raises —
    the caller must know the event was not recorded (callers in hot paths
    wrap this call).
    """
    validator = _VALIDATORS.get(event_type)
    if validator is None:
        raise MetricsFeedError(f"unknown metrics event type: {event_type!r}")
    if not isinstance(payload, dict):
        raise MetricsFeedError("event payload must be a mapping")
    clean = validator(payload)
    now = (now_fn or time.time)()
    row: dict[str, Any] = {
        "ts": now,
        "ts_iso": _utc_now_iso(),
        "component": "metrics_feed",
        "event": event_type,
    }
    row.update(clean)
    log_path = Path(event_log) if event_log is not None else DEFAULT_EVENT_LOG
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        raise MetricsFeedError(f"cannot append to event log {log_path}: {exc}") from exc
    return row


def record_rollback(
    job_id: str,
    merge_sha: str,
    reason: str,
    *,
    event_log: Optional[Path | str] = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Record that an auto-merge was rolled back."""
    return record(
        EVENT_ROLLBACK,
        {"job_id": job_id, "merge_sha": merge_sha, "reason": reason},
        event_log=event_log,
        now_fn=now_fn,
    )


def record_escalation(
    job_id: str,
    reason: str,
    *,
    event_log: Optional[Path | str] = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Record that an auto-merge attempt was escalated to human review."""
    return record(
        EVENT_ESCALATION,
        {"job_id": job_id, "reason": reason},
        event_log=event_log,
        now_fn=now_fn,
    )


def record_ci_result(
    result: str,
    runner: str,
    *,
    fault_injection: bool = False,
    event_log: Optional[Path | str] = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Record one CI run's outcome. ``fault_injection`` marks deliberate
    fault-injection runs, which the rollup excludes (per the watchdog
    policy: the CI failure rate must not count sabotage)."""
    return record(
        EVENT_CI_RESULT,
        {"result": result, "runner": runner, "fault_injection": fault_injection},
        event_log=event_log,
        now_fn=now_fn,
    )


def record_jev_call(
    call_site: str,
    ok: bool,
    *,
    error: str = "",
    event_log: Optional[Path | str] = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Record one Jev backend call's outcome. Jev is exception-path only,
    so both calls and errors are recorded — the rate needs the denominator."""
    return record(
        EVENT_JEV_CALL,
        {"call_site": call_site, "ok": ok, "error": error},
        event_log=event_log,
        now_fn=now_fn,
    )


def record_band_change(
    band: str,
    old_value: float,
    new_value: float,
    reason: str,
    *,
    event_log: Optional[Path | str] = None,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Record one risk-band change (the learn loop's tighten/loosen moves)."""
    return record(
        EVENT_BAND_CHANGE,
        {
            "band": band,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
        },
        event_log=event_log,
        now_fn=now_fn,
    )


# ─────────────────────────────────────────────────────────────────────
# Snapshot rollup (batch, on a short cadence)
# ─────────────────────────────────────────────────────────────────────


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Read JSONL rows, skipping corrupt lines. Never crashes the rollup —
    the same pattern as the authority's RateLimiter."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _in_window(ts: Any, now: float, window_hours: float) -> bool:
    """True for events inside the trailing window. Future-dated events are
    excluded — clock skew must not inflate rates."""
    return (
        isinstance(ts, (int, float))
        and not isinstance(ts, bool)
        and ts <= now
        and now - ts <= window_hours * 3600
    )


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A rate with an empty denominator is unknown — never zero.

    "No data" must not read as "all clear": the watchdog treats unknown as
    logged in monitor-only and tripping in enforcing.
    """
    if denominator <= 0:
        return None
    return numerator / denominator


def build_snapshot(
    config: MetricsFeedConfig, *, now: Optional[float] = None
) -> MetricSnapshot:
    """Roll the event logs up into one ``MetricSnapshot`` for the watchdog.

    Pure apart from reading the logs. Rates with no observations in their
    window come back ``None`` (unknown); counts are always real numbers.
    """
    now = time.time() if now is None else now
    windows = config.windows
    events = _read_rows(Path(os.path.expanduser(config.event_log)))
    decisions = _read_rows(Path(os.path.expanduser(config.auto_merge_log)))

    def feed_events(event_type: str, window_hours: float) -> list[dict[str, Any]]:
        return [
            e
            for e in events
            if e.get("event") == event_type
            and _in_window(e.get("ts"), now, window_hours)
        ]

    def authority_decisions(
        allowed_only: bool, window_hours: float
    ) -> list[dict[str, Any]]:
        return [
            d
            for d in decisions
            if (
                d.get("decision") == DECISION_ALLOWED
                or (not allowed_only and d.get("decision") == DECISION_REFUSED)
            )
            and _in_window(d.get("ts"), now, window_hours)
        ]

    rollback_window = windows["rollback_rate_hours"]
    rollbacks = len(feed_events(EVENT_ROLLBACK, rollback_window))
    allowed_7d = len(authority_decisions(True, rollback_window))

    escalation_window = windows["escalation_rate_hours"]
    escalations = len(feed_events(EVENT_ESCALATION, escalation_window))
    attempts_24h = len(authority_decisions(False, escalation_window))

    ci_window = windows["ci_failure_rate_hours"]
    ci_runs = [
        e
        for e in feed_events(EVENT_CI_RESULT, ci_window)
        if not e.get("fault_injection")
    ]
    ci_failures = sum(1 for e in ci_runs if e.get("result") == "fail")

    jev_window = windows["jev_error_rate_hours"]
    jev_calls = feed_events(EVENT_JEV_CALL, jev_window)
    jev_errors = sum(1 for e in jev_calls if not e.get("ok"))

    band_window = windows["band_change_velocity_hours"]
    band_changes = len(feed_events(EVENT_BAND_CHANGE, band_window))

    volume_window = windows["merge_volume_per_hour_hours"]
    merges_last_hour = len(authority_decisions(True, volume_window))

    return MetricSnapshot(
        rollback_rate=_rate(rollbacks, allowed_7d),
        escalation_rate=_rate(escalations, attempts_24h),
        ci_failure_rate=_rate(ci_failures, len(ci_runs)),
        jev_error_rate=_rate(jev_errors, len(jev_calls)),
        band_change_velocity_per_day=float(band_changes),
        merge_volume_per_hour=float(merges_last_hour),
    )


# ─────────────────────────────────────────────────────────────────────
# Cadence entry point
# ─────────────────────────────────────────────────────────────────────


def run_feed(
    config_path: Optional[Path | str] = None,
    policy_path: Optional[Path | str] = None,
    *,
    audit_log: Optional[Path | str] = None,
    now: Optional[float] = None,
) -> str:
    """Run one feed tick: build the snapshot, hand it to the watchdog.

    Returns the watchdog's result state. When the feed config is disabled
    (the shipped default) this returns ``"disabled"`` WITHOUT building a
    snapshot, constructing the watchdog, or emitting any signal — the feed
    is inert until the config says ``enabled: true``.

    The watchdog's ``evaluate()`` emits its usual one audit row per tick;
    the feed adds no second row. A malformed feed config raises
    ``MetricsFeedError`` instead of evaluating on guesses.
    """
    config = load_metrics_feed_config(config_path)
    if not config.enabled:
        return "disabled"
    snapshot = build_snapshot(config, now=now)
    dog = Watchdog(policy_path, audit_log=audit_log)
    return dog.evaluate(snapshot).state


def main(argv: Optional[list[str]] = None) -> int:
    """CLI for the cadence timer: run one tick and print the state.

    A systemd timer (installed separately, only when the feed is wanted
    live) invokes ``python -m prismatic.review_factory.metrics_feed``.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Watchdog metrics feed: build one snapshot and evaluate it."
    )
    parser.add_argument("--config", default=None, help="feed config YAML path")
    parser.add_argument("--policy", default=None, help="watchdog policy YAML path")
    parser.add_argument(
        "--audit-log", default=None, help="watchdog audit log path override"
    )
    args = parser.parse_args(argv)
    state = run_feed(args.config, args.policy, audit_log=args.audit_log)
    print(state)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
