"""Tests for the deterministic watchdog (chunk 2 of the post-shadow roadmap).

Every test asserts a safety property the watchdog claims:
- disabled means inert: no metric computed, no trip, no halt;
- monitor-only trips are logged, never halting;
- enforcing trips halt and STAY halted until Michael re-arms;
- there is no self re-arm, no timeout, no auto-clear;
- invalid or unknown input cannot produce a "clear" verdict (fail-closed);
- every evaluation emits exactly one audit signal.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory.watchdog import (
    ALL_METRICS,
    MODE_ENFORCING,
    MODE_MONITOR_ONLY,
    STATE_CLEAR,
    STATE_DISABLED,
    STATE_INVALID,
    STATE_TRIPPED_HALTED,
    STATE_TRIPPED_MONITOR,
    MetricSnapshot,
    Watchdog,
    WatchdogConfigError,
    load_watchdog_policy,
)

HERE = Path(__file__).resolve()
SPEC_YAML = HERE.parent.parent / "spec" / "watchdog_policy_v1.yaml"


# ── fixtures ─────────────────────────────────────────────────────────


def _write_policy(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / "watchdog_policy_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _watchdog(tmp_path, **overrides):
    return Watchdog(
        _write_policy(tmp_path, **overrides),
        audit_log=tmp_path / "watchdog-audit.jsonl",
    )


def _clean_snapshot(**overrides):
    base = {
        "rollback_rate": 0.01,
        "escalation_rate": 0.05,
        "ci_failure_rate": 0.10,
        "jev_error_rate": 0.02,
        "band_change_velocity_per_day": 1,
        "merge_volume_per_hour": 0.5,
    }
    base.update(overrides)
    return MetricSnapshot.from_dict(base)


def _spike_kwargs():
    """Every metric pushed above its shipped threshold."""
    policy = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    out = {}
    for metric in ALL_METRICS:
        threshold = policy["thresholds"][metric]
        out[metric] = threshold + 0.5
    return out


def _audit_rows(tmp_path):
    log = tmp_path / "watchdog-audit.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# ── policy loading ───────────────────────────────────────────────────


def test_default_policy_is_disabled_and_monitor_only():
    policy = load_watchdog_policy(SPEC_YAML)
    assert policy.enabled is False
    assert policy.mode == MODE_MONITOR_ONLY
    assert policy.rearm_principal == "mbgulden"
    assert set(policy.thresholds) == set(ALL_METRICS)
    assert policy.thresholds["rollback_rate"] == 0.05


def test_missing_policy_file_is_disabled(tmp_path):
    dog = Watchdog(
        tmp_path / "does-not-exist.yaml",
        audit_log=tmp_path / "a.jsonl",
    )
    result = dog.evaluate(_clean_snapshot())
    assert result.state == STATE_DISABLED
    assert result.trips == ()
    assert dog.halted is False


def test_malformed_policy_raises_on_load(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("thresholds: [unclosed\n", encoding="utf-8")
    with pytest.raises(WatchdogConfigError):
        load_watchdog_policy(bad)


def test_malformed_policy_never_evaluates(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("thresholds: [unclosed\n", encoding="utf-8")
    dog = Watchdog(bad, audit_log=tmp_path / "a.jsonl")
    result = dog.evaluate(_clean_snapshot())
    assert result.state == STATE_INVALID
    assert dog.halted is False


def test_unknown_mode_rejected(tmp_path):
    path = _write_policy(tmp_path, enabled=True, mode="chaos")
    with pytest.raises(WatchdogConfigError):
        load_watchdog_policy(path)


def test_non_numeric_threshold_rejected(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("thresholds: {rollback_rate: high}\n", encoding="utf-8")
    with pytest.raises(WatchdogConfigError):
        load_watchdog_policy(path)


# ── disabled behavior ────────────────────────────────────────────────


def test_disabled_never_computes_metrics(tmp_path):
    dog = _watchdog(tmp_path)  # enabled: false, the shipped default
    result = dog.evaluate(MetricSnapshot.from_dict(_spike_kwargs()))
    assert result.state == STATE_DISABLED
    assert result.trips == ()
    assert dog.halted is False


def test_disabled_audit_signal_says_disabled(tmp_path):
    dog = _watchdog(tmp_path)
    dog.evaluate(_clean_snapshot())
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["component"] == "watchdog"
    assert rows[0]["state"] == STATE_DISABLED


# ── monitor-only evaluation ──────────────────────────────────────────


def test_clean_snapshot_is_clear(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(_clean_snapshot())
    assert result.state == STATE_CLEAR
    assert result.trips == ()
    assert dog.halted is False


def test_all_zero_snapshot_is_clear(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(MetricSnapshot())
    # all-None snapshot: every metric unknown, monitor-only logs unknowns
    # but does not trip on them.
    assert result.state == STATE_CLEAR
    assert result.trips == ()
    assert set(result.unknown) == set(ALL_METRICS)


def test_at_threshold_does_not_trip(tmp_path):
    policy = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    at_edge = {m: policy["thresholds"][m] for m in ALL_METRICS}
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(MetricSnapshot.from_dict(at_edge))
    assert result.state == STATE_CLEAR  # trips only STRICTLY above


@pytest.mark.parametrize("metric", ALL_METRICS)
def test_each_metric_spike_trips_monitor_only(tmp_path, metric):
    policy = yaml.safe_load(SPEC_YAML.read_text(encoding="utf-8"))
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(
        _clean_snapshot(**{metric: policy["thresholds"][metric] + 0.5})
    )
    assert result.state == STATE_TRIPPED_MONITOR
    assert dog.halted is False  # monitor-only NEVER halts
    tripped = [t.metric for t in result.trips]
    assert metric in tripped
    trip = next(t for t in result.trips if t.metric == metric)
    assert trip.reason == "above_threshold"
    assert trip.value == pytest.approx(policy["thresholds"][metric] + 0.5)


def test_fault_injection_all_metrics_spiked(tmp_path):
    """The watchdog under test must actually trip — a watchdog that never
    trips in testing is not a watchdog. Monitor-only still never halts."""
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(MetricSnapshot.from_dict(_spike_kwargs()))
    assert result.state == STATE_TRIPPED_MONITOR
    assert len(result.trips) == len(ALL_METRICS)
    assert dog.halted is False


def test_monitor_only_trip_emits_audit_with_trip_detail(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["state"] == STATE_TRIPPED_MONITOR
    trips = rows[0]["trips"]
    assert any(t["metric"] == "rollback_rate" for t in trips)
    trip = next(t for t in trips if t["metric"] == "rollback_rate")
    assert trip["value"] == 0.5
    assert trip["threshold"] == 0.05


def test_policy_without_threshold_skips_metric(tmp_path):
    """A metric with no configured threshold is skipped — never invented."""
    path = _write_policy(tmp_path, enabled=True, thresholds={})
    dog = Watchdog(path, audit_log=tmp_path / "a.jsonl")
    result = dog.evaluate(MetricSnapshot.from_dict(_spike_kwargs()))
    assert result.state == STATE_CLEAR
    assert result.trips == ()


def test_metric_snapshot_ignores_unknown_keys():
    snap = MetricSnapshot.from_dict({"rollback_rate": 0.1, "future_metric": 99})
    assert snap.rollback_rate == 0.1
    assert snap.as_dict()["rollback_rate"] == 0.1


# ── invalid input: fail-closed ───────────────────────────────────────


def test_invalid_snapshot_monitor_only(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(_clean_snapshot(rollback_rate=-0.1))
    assert result.state == STATE_INVALID
    assert dog.halted is False  # logged, never halting in monitor-only


def test_invalid_snapshot_enforcing_halts(tmp_path):
    """In enforcing mode the feed cannot prove 'all clear' on bad input —
    fail closed and halt."""
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    result = dog.evaluate(_clean_snapshot(rollback_rate=-0.1))
    assert result.state == STATE_TRIPPED_HALTED
    assert dog.halted is True


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -1.0])
def test_non_finite_or_negative_is_invalid(tmp_path, bad_value):
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(_clean_snapshot(ci_failure_rate=bad_value))
    assert result.state == STATE_INVALID


def test_rate_above_one_is_invalid(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(_clean_snapshot(rollback_rate=1.5))
    assert result.state == STATE_INVALID


def test_unknown_metric_enforcing_trips(tmp_path):
    """Missing metric in enforcing mode: cannot prove clear -> trip."""
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    snap = _clean_snapshot(rollback_rate=None)
    result = dog.evaluate(snap)
    assert result.state == STATE_TRIPPED_HALTED
    assert dog.halted is True
    trip = next(t for t in result.trips if t.metric == "rollback_rate")
    assert trip.reason == "unknown"


# ── enforcing mode: halt + re-arm ────────────────────────────────────


def test_enforcing_trip_halts(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    result = dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    assert result.state == STATE_TRIPPED_HALTED
    assert dog.halted is True


def test_halt_sticks_after_metrics_recover(tmp_path):
    """A halted watchdog stays halted even when metrics go clean — only
    Michael's re-arm clears it. No auto-clear, no timeout."""
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    assert dog.halted is True
    for _ in range(3):
        result = dog.evaluate(_clean_snapshot())
        assert result.state == STATE_CLEAR  # metrics are fine...
        assert dog.halted is True  # ...but the halt stands


def test_rearm_wrong_principal_refused(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    assert dog.rearm("mallory") == "refused"
    assert dog.halted is True


def test_rearm_empty_principal_refused(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    assert dog.rearm("") == "refused"
    assert dog.halted is True


def test_rearm_michael_clears(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    assert dog.rearm("mbgulden") == "rearmed"
    assert dog.halted is False


def test_rearm_noop_when_not_halted(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    assert dog.rearm("mbgulden") == "noop"
    assert dog.halted is False


def test_rearm_audit_trail(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    dog.rearm("mallory")
    dog.rearm("mbgulden")
    rows = _audit_rows(tmp_path)
    rearm_rows = [r for r in rows if r.get("event") == "rearm"]
    assert [r["outcome"] for r in rearm_rows] == ["refused", "rearmed"]
    assert rearm_rows[0]["principal"] == "mallory"
    assert rearm_rows[1]["principal"] == "mbgulden"


# ── audit coverage ───────────────────────────────────────────────────


def test_every_evaluation_emits_exactly_one_signal(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    dog.evaluate(_clean_snapshot())
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    dog.evaluate(_clean_snapshot(rollback_rate=-1.0))
    rows = _audit_rows(tmp_path)
    assert len(rows) == 3
    assert [r["state"] for r in rows] == [
        STATE_CLEAR,
        STATE_TRIPPED_MONITOR,
        STATE_INVALID,
    ]
    for row in rows:
        assert row["component"] == "watchdog"
        assert "metrics" in row
        assert "policy_version" in row


def test_halted_audit_row_marks_halted(tmp_path):
    dog = _watchdog(tmp_path, enabled=True, mode=MODE_ENFORCING)
    dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    rows = _audit_rows(tmp_path)
    assert rows[-1]["halted"] is True
    assert rows[-1]["state"] == STATE_TRIPPED_HALTED


def test_audit_dir_removed_mid_run_self_heals(tmp_path):
    """Deleting the audit dir mid-run must not drop later signals or verdicts.

    The cached-dir optimization must not wedge: the first emit after the
    removal retries the mkdir once, re-creates the dir, and still writes
    its row. Evaluation outcomes are unaffected throughout (fail-closed).
    (The row written before the deletion is gone with the dir itself —
    only post-deletion signals are the component's responsibility.)
    """
    log = tmp_path / "nested" / "audit.jsonl"
    dog = Watchdog(_write_policy(tmp_path, enabled=True), audit_log=log)
    dog.evaluate(_clean_snapshot())
    assert log.exists()
    shutil.rmtree(tmp_path / "nested")
    assert not log.exists()
    result = dog.evaluate(_clean_snapshot(rollback_rate=0.5))
    assert result.state == STATE_TRIPPED_MONITOR  # verdict unaffected
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1  # dir re-created; the new row was written, not dropped
    assert rows[0]["state"] == STATE_TRIPPED_MONITOR
    assert rows[0]["trips"]  # trip detail intact


# ── page payload ─────────────────────────────────────────────────────


def test_prepare_page_is_data_only(tmp_path):
    dog = _watchdog(tmp_path, enabled=True)
    result = dog.evaluate(MetricSnapshot.from_dict(_spike_kwargs()))
    page = Watchdog.prepare_page(result)
    assert page["halted"] is False
    assert page["mode"] == MODE_MONITOR_ONLY
    assert page["rearm_principal"] == "mbgulden"
    # the three most decision-relevant signals come first
    assert len(page["trips"]) == len(ALL_METRICS)
    assert page["summary"]  # human-readable, non-empty
