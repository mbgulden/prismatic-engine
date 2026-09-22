"""Tests for the event-sourced watchdog metrics feed.

Every test asserts a property the feed claims:
- recording is validated: unknown event types and invalid payloads are
  rejected and append nothing (fail-closed);
- every appended row is a well-formed audit signal;
- the rollup is mechanical: rates are numerator/denominator over trailing
  windows, and a rate with no observations is unknown (None) — never zero;
- run_feed is inert while the feed config is disabled: no snapshot, no
  watchdog, no signals;
- the feed never changes watchdog behavior: monitor-only trips are logged,
  never halting.
"""

import json
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory.metrics_feed import (
    ALL_EVENTS,
    MetricsFeedError,
    build_snapshot,
    load_metrics_feed_config,
    record,
    record_band_change,
    record_ci_result,
    record_escalation,
    record_jev_call,
    record_rollback,
    run_feed,
)

HERE = Path(__file__).resolve()
FEED_SPEC_YAML = HERE.parent.parent / "spec" / "watchdog_metrics_feed_v1.yaml"
WD_SPEC_YAML = HERE.parent.parent / "spec" / "watchdog_policy_v1.yaml"

T0 = 1_750_000_000.0


# ── fixtures ─────────────────────────────────────────────────────────


def _feed_config_path(tmp_path, **overrides):
    data = yaml.safe_load(FEED_SPEC_YAML.read_text(encoding="utf-8"))
    data["event_log"] = str(tmp_path / "events.jsonl")
    data["auto_merge_log"] = str(tmp_path / "decisions.jsonl")
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / "feed_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _feed_config(tmp_path, **overrides):
    return load_metrics_feed_config(_feed_config_path(tmp_path, **overrides))


def _policy_path(tmp_path, **overrides):
    data = yaml.safe_load(WD_SPEC_YAML.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / "wd_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _rows(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_decisions(tmp_path, rows):
    """rows: list of (ts, decision) tuples."""
    path = tmp_path / "decisions.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        for ts, decision in rows:
            fh.write(json.dumps({"ts": ts, "decision": decision}) + "\n")


def _event_log(tmp_path):
    return tmp_path / "events.jsonl"


# ── config loading ───────────────────────────────────────────────────


def test_missing_feed_config_is_disabled(tmp_path):
    config = load_metrics_feed_config(tmp_path / "does-not-exist.yaml")
    assert config.enabled is False
    assert config.version == "missing"


def test_malformed_feed_config_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("windows: [unclosed\n", encoding="utf-8")
    with pytest.raises(MetricsFeedError):
        load_metrics_feed_config(bad)


def test_non_mapping_feed_config_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(MetricsFeedError):
        load_metrics_feed_config(bad)


def test_shipped_feed_config_is_disabled_and_complete():
    config = load_metrics_feed_config(FEED_SPEC_YAML)
    assert config.enabled is False
    assert config.version == "metrics-feed-v1"
    assert config.event_log.endswith("watchdog-metrics-events.jsonl")
    assert config.auto_merge_log.endswith("auto-merge-decisions.jsonl")
    for key in (
        "rollback_rate_hours",
        "escalation_rate_hours",
        "ci_failure_rate_hours",
        "jev_error_rate_hours",
        "band_change_velocity_hours",
        "merge_volume_per_hour_hours",
    ):
        assert config.windows[key] > 0


def test_negative_window_rejected(tmp_path):
    path = _feed_config_path(tmp_path, windows={"rollback_rate_hours": -5})
    with pytest.raises(MetricsFeedError):
        load_metrics_feed_config(path)


def test_all_event_types_known():
    assert set(ALL_EVENTS) == {
        "rollback",
        "escalation",
        "ci_result",
        "jev_call",
        "band_change",
    }


# ── event recording ──────────────────────────────────────────────────


def test_record_rollback_appends_well_formed_row(tmp_path):
    row = record_rollback(
        "job-1",
        "abc123",
        "deploy failed health check",
        event_log=_event_log(tmp_path),
        now_fn=lambda: T0,
    )
    rows = _rows(_event_log(tmp_path))
    assert len(rows) == 1
    assert rows[0] == row
    assert row["component"] == "metrics_feed"
    assert row["event"] == "rollback"
    assert row["job_id"] == "job-1"
    assert row["merge_sha"] == "abc123"
    assert row["reason"] == "deploy failed health check"
    assert row["ts"] == T0
    assert row["ts_iso"]


def test_record_escalation(tmp_path):
    row = record_escalation(
        "job-2",
        "tier above max",
        event_log=_event_log(tmp_path),
        now_fn=lambda: T0,
    )
    assert row["event"] == "escalation"
    assert row["job_id"] == "job-2"
    assert _rows(_event_log(tmp_path)) == [row]


def test_record_ci_result_round_trips_flags(tmp_path):
    row = record_ci_result(
        "fail",
        "self-hosted",
        fault_injection=True,
        event_log=_event_log(tmp_path),
        now_fn=lambda: T0,
    )
    assert row["event"] == "ci_result"
    assert row["result"] == "fail"
    assert row["runner"] == "self-hosted"
    assert row["fault_injection"] is True


def test_record_jev_call_ok_and_error(tmp_path):
    ok_row = record_jev_call(
        "triage", True, event_log=_event_log(tmp_path), now_fn=lambda: T0
    )
    err_row = record_jev_call(
        "triage",
        False,
        error="timeout",
        event_log=_event_log(tmp_path),
        now_fn=lambda: T0,
    )
    assert ok_row["ok"] is True
    assert err_row["ok"] is False
    assert err_row["error"] == "timeout"
    assert len(_rows(_event_log(tmp_path))) == 2


def test_record_band_change(tmp_path):
    row = record_band_change(
        "tier0.deps",
        0.20,
        0.15,
        "learn loop tighten",
        event_log=_event_log(tmp_path),
        now_fn=lambda: T0,
    )
    assert row["event"] == "band_change"
    assert row["old_value"] == 0.20
    assert row["new_value"] == 0.15


def test_unknown_event_type_rejected_and_appends_nothing(tmp_path):
    with pytest.raises(MetricsFeedError):
        record(
            "supernova",
            {"job_id": "x"},
            event_log=_event_log(tmp_path),
            now_fn=lambda: T0,
        )
    assert not _event_log(tmp_path).exists()


def test_empty_job_id_rejected(tmp_path):
    with pytest.raises(MetricsFeedError):
        record_rollback(
            "  ",
            "abc",
            "reason",
            event_log=_event_log(tmp_path),
            now_fn=lambda: T0,
        )
    assert not _event_log(tmp_path).exists()


def test_ci_result_invalid_value_rejected(tmp_path):
    with pytest.raises(MetricsFeedError):
        record_ci_result(
            "maybe",
            "self-hosted",
            event_log=_event_log(tmp_path),
            now_fn=lambda: T0,
        )
    assert not _event_log(tmp_path).exists()


def test_ci_result_non_bool_fault_injection_rejected(tmp_path):
    with pytest.raises(MetricsFeedError):
        record(
            "ci_result",
            {"result": "pass", "runner": "self-hosted", "fault_injection": "yes"},
            event_log=_event_log(tmp_path),
            now_fn=lambda: T0,
        )
    assert not _event_log(tmp_path).exists()


def test_jev_call_non_bool_ok_rejected(tmp_path):
    with pytest.raises(MetricsFeedError):
        record_jev_call("triage", 1, event_log=_event_log(tmp_path), now_fn=lambda: T0)
    assert not _event_log(tmp_path).exists()


def test_band_change_bool_value_rejected(tmp_path):
    with pytest.raises(MetricsFeedError):
        record_band_change(
            "tier0.deps",
            True,
            0.15,
            "bad",
            event_log=_event_log(tmp_path),
            now_fn=lambda: T0,
        )
    assert not _event_log(tmp_path).exists()


def test_non_dict_payload_rejected(tmp_path):
    with pytest.raises(MetricsFeedError):
        record(
            "rollback",
            ["not", "a", "dict"],
            event_log=_event_log(tmp_path),
            now_fn=lambda: T0,
        )


# ── snapshot rollup ────────────────────────────────────────────────


def test_empty_logs_all_rates_unknown_counts_zero(tmp_path):
    """The negative test: no data must not read as "all clear". Rates are
    unknown (None); counts are honest zeros."""
    snap = build_snapshot(_feed_config(tmp_path), now=T0)
    assert snap.rollback_rate is None
    assert snap.escalation_rate is None
    assert snap.ci_failure_rate is None
    assert snap.jev_error_rate is None
    assert snap.band_change_velocity_per_day == 0
    assert snap.merge_volume_per_hour == 0


def test_rollback_rate_math(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    _write_decisions(tmp_path, [(T0 - 3600, "allowed")] * 10)
    record_rollback("j1", "sha1", "bad", event_log=log, now_fn=lambda: T0)
    assert len(_rows(log)) == 1  # sanity: event landed
    snap = build_snapshot(cfg, now=T0)
    assert snap.rollback_rate == pytest.approx(0.1)


def test_rollback_with_no_merges_is_unknown_not_zero(tmp_path):
    """Fail-closed: rollbacks with no allowed merges in the window cannot
    produce a rate — None, never 0/0 == "clear"."""
    cfg = _feed_config(tmp_path)
    record_rollback(
        "j1",
        "sha1",
        "bad",
        event_log=_event_log(tmp_path),
        now_fn=lambda: T0,
    )
    snap = build_snapshot(cfg, now=T0)
    assert snap.rollback_rate is None


def test_ci_failure_rate_excludes_fault_injection(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    now_fn = lambda: T0  # noqa: E731
    record_ci_result("pass", "self-hosted", event_log=log, now_fn=now_fn)
    record_ci_result("fail", "self-hosted", event_log=log, now_fn=now_fn)
    # Deliberate fault-injection runs must not count on either side.
    record_ci_result(
        "fail", "self-hosted", fault_injection=True, event_log=log, now_fn=now_fn
    )
    record_ci_result(
        "fail", "self-hosted", fault_injection=True, event_log=log, now_fn=now_fn
    )
    snap = build_snapshot(cfg, now=T0)
    assert snap.ci_failure_rate == pytest.approx(0.5)


def test_escalation_rate_denominator_includes_refused(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    _write_decisions(
        tmp_path,
        [(T0 - 100, "allowed"), (T0 - 200, "refused"), (T0 - 300, "refused")],
    )
    record_escalation("j9", "novel", event_log=log, now_fn=lambda: T0)
    snap = build_snapshot(cfg, now=T0)
    assert snap.escalation_rate == pytest.approx(1 / 3)


def test_jev_error_rate_math(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    now_fn = lambda: T0  # noqa: E731
    record_jev_call("triage", True, event_log=log, now_fn=now_fn)
    record_jev_call("triage", True, event_log=log, now_fn=now_fn)
    record_jev_call("triage", True, event_log=log, now_fn=now_fn)
    record_jev_call("diagnose", False, error="boom", event_log=log, now_fn=now_fn)
    snap = build_snapshot(cfg, now=T0)
    assert snap.jev_error_rate == pytest.approx(0.25)


def test_band_change_velocity_counts_trailing_day(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    for i in range(2):
        record_band_change(
            "tier0.deps",
            0.20,
            0.15,
            f"tighten {i}",
            event_log=log,
            now_fn=lambda: T0,
        )
    snap = build_snapshot(cfg, now=T0)
    assert snap.band_change_velocity_per_day == 2


def test_merge_volume_counts_allowed_in_trailing_hour_only(tmp_path):
    cfg = _feed_config(tmp_path)
    _write_decisions(
        tmp_path,
        [
            (T0 - 100, "allowed"),  # in the hour
            (T0 - 200, "allowed"),  # in the hour
            (T0 - 100, "refused"),  # refused: never volume
            (T0 - 7200, "allowed"),  # older than the hour
        ],
    )
    snap = build_snapshot(cfg, now=T0)
    assert snap.merge_volume_per_hour == 2


def test_events_outside_window_excluded(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    # Rollback older than the 7-day window; escalation older than 24h.
    record_rollback(
        "old",
        "sha",
        "stale",
        event_log=log,
        now_fn=lambda: T0 - 200 * 3600,
    )
    record_escalation("old", "stale", event_log=log, now_fn=lambda: T0 - 25 * 3600)
    _write_decisions(tmp_path, [(T0 - 100, "allowed")])
    snap = build_snapshot(cfg, now=T0)
    assert snap.rollback_rate == pytest.approx(0.0)
    assert snap.escalation_rate == pytest.approx(0.0)


def test_future_dated_events_excluded(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    record_rollback(
        "future",
        "sha",
        "clock skew",
        event_log=log,
        now_fn=lambda: T0 + 3600,
    )
    _write_decisions(tmp_path, [(T0 - 100, "allowed")])
    snap = build_snapshot(cfg, now=T0)
    assert snap.rollback_rate == pytest.approx(0.0)


def test_corrupt_lines_skipped_without_crash(tmp_path):
    cfg = _feed_config(tmp_path)
    log = _event_log(tmp_path)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
        fh.write('{"half": "a row"\n')
    record_ci_result("fail", "self-hosted", event_log=log, now_fn=lambda: T0)
    snap = build_snapshot(cfg, now=T0)
    assert snap.ci_failure_rate == pytest.approx(1.0)


# ── run_feed: the cadence tick ───────────────────────────────────────


def test_run_feed_disabled_is_inert(tmp_path):
    """Default-off: no snapshot, no watchdog, no signals."""
    audit = tmp_path / "wd-audit.jsonl"
    state = run_feed(
        _feed_config_path(tmp_path),  # enabled: false, the shipped default
        _policy_path(tmp_path, enabled=True),
        audit_log=audit,
        now=T0,
    )
    assert state == "disabled"
    assert not audit.exists()


def test_run_feed_enabled_clean_data_is_clear(tmp_path):
    cfg_path = _feed_config_path(tmp_path, enabled=True)
    cfg = load_metrics_feed_config(cfg_path)
    log = Path(cfg.event_log)
    now_fn = lambda: T0  # noqa: E731
    # 20 clean merges 2h ago (inside the 7d rollback window, outside the
    # 1h volume window), CI all green, Jev all fine, one band change.
    _write_decisions(tmp_path, [(T0 - 7200, "allowed")] * 20)
    for _ in range(10):
        record_ci_result("pass", "self-hosted", event_log=log, now_fn=now_fn)
    for _ in range(5):
        record_jev_call("triage", True, event_log=log, now_fn=now_fn)
    record_band_change(
        "tier0.deps", 0.20, 0.15, "tighten", event_log=log, now_fn=now_fn
    )

    audit = tmp_path / "wd-audit.jsonl"
    state = run_feed(
        cfg_path, _policy_path(tmp_path, enabled=True), audit_log=audit, now=T0
    )
    assert state == "clear"
    rows = _rows(audit)
    assert len(rows) == 1  # the watchdog's own signal; the feed adds none
    assert rows[0]["component"] == "watchdog"
    assert rows[0]["state"] == "clear"


def test_run_feed_enabled_spike_trips_monitor_only(tmp_path):
    """Fault injection through the feed: the watchdog must trip — and the
    feed must never change watchdog behavior (monitor-only never halts)."""
    cfg_path = _feed_config_path(tmp_path, enabled=True)
    cfg = load_metrics_feed_config(cfg_path)
    log = Path(cfg.event_log)
    now_fn = lambda: T0  # noqa: E731
    _write_decisions(tmp_path, [(T0 - 100, "allowed")])
    record_rollback("j1", "sha1", "bad", event_log=log, now_fn=now_fn)
    record_ci_result("fail", "self-hosted", event_log=log, now_fn=now_fn)

    audit = tmp_path / "wd-audit.jsonl"
    state = run_feed(
        cfg_path, _policy_path(tmp_path, enabled=True), audit_log=audit, now=T0
    )
    assert state == "tripped_monitor_only"
    rows = _rows(audit)
    assert len(rows) == 1
    assert rows[0]["halted"] is False
    assert rows[0]["state"] == "tripped_monitor_only"
    tripped = {t["metric"] for t in rows[0]["trips"]}
    assert "rollback_rate" in tripped
    assert "ci_failure_rate" in tripped


def test_run_feed_malformed_config_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("enabled: [unclosed\n", encoding="utf-8")
    with pytest.raises(MetricsFeedError):
        run_feed(
            bad,
            _policy_path(tmp_path, enabled=True),
            audit_log=tmp_path / "a.jsonl",
            now=T0,
        )
