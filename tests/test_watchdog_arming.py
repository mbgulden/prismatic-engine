"""Tests for the monitor-only watchdog arming (watchdog-v2 / metrics-feed-v2).

The v1 files are the fail-closed default: hard-disabled, inert. The v2
files arm the watchdog monitor-only on Michael's word — trips are logged,
never halting. These tests pin the arming contract:

- the v2 policy is enabled, monitor-only, re-armed only by mbgulden, and
  its thresholds are byte-identical to v1;
- the v2 feed config is enabled, with logs and windows identical to v1;
- run_feed with the v2 configs on synthetic logs returns a REAL state
  (clear / tripped_monitor_only) — never "disabled";
- run_feed with defaults still returns "disabled" (v1 untouched, fail-closed);
- the CLI honors --policy/--config overrides.
"""

import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from prismatic.review_factory import metrics_feed
from prismatic.review_factory.metrics_feed import (
    load_metrics_feed_config,
    main,
    run_feed,
)
from prismatic.review_factory.watchdog import (
    STATE_CLEAR,
    STATE_DISABLED,
    STATE_TRIPPED_MONITOR,
    load_watchdog_policy,
)

SPEC_DIR = Path(metrics_feed.__file__).resolve().parent / "spec"
POLICY_V1 = SPEC_DIR / "watchdog_policy_v1.yaml"
POLICY_V2 = SPEC_DIR / "watchdog_policy_v2.yaml"
FEED_V1 = SPEC_DIR / "watchdog_metrics_feed_v1.yaml"
FEED_V2 = SPEC_DIR / "watchdog_metrics_feed_v2.yaml"

T0 = 1_750_000_000.0


# ── helpers ──────────────────────────────────────────────────────────


def _v2_feed_config_with_synthetic_logs(tmp_path):
    """The shipped v2 feed config, verbatim except the two log paths, which
    point at synthetic tmp logs so the test never touches the home dir."""
    data = yaml.safe_load(FEED_V2.read_text(encoding="utf-8"))
    data["event_log"] = str(tmp_path / "events.jsonl")
    data["auto_merge_log"] = str(tmp_path / "decisions.jsonl")
    path = tmp_path / "watchdog_metrics_feed_v2_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _record(tmp_path, event_type, payload, ts):
    from prismatic.review_factory.metrics_feed import record

    return record(
        event_type,
        payload,
        event_log=tmp_path / "events.jsonl",
        now_fn=lambda: ts,
    )


def _write_decision(tmp_path, ts, decision):
    path = tmp_path / "decisions.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": ts, "decision": decision}) + "\n")


def _clean_synthetic_logs(tmp_path, t0=T0):
    """All rates known and below thresholds: 10 allowed merges 2h back,
    1 escalation, passing CI, healthy Jev, 1 band change."""
    for _ in range(10):
        _write_decision(tmp_path, t0 - 2 * 3600, "allowed")
    _record(
        tmp_path,
        "escalation",
        {"job_id": "j-1", "reason": "low-confidence"},
        t0 - 30 * 60,
    )
    for _ in range(4):
        _record(
            tmp_path,
            "ci_result",
            {"result": "pass", "runner": "self-hosted", "fault_injection": False},
            t0 - 30 * 60,
        )
    for _ in range(5):
        _record(
            tmp_path,
            "jev_call",
            {"call_site": "failure_triage", "ok": True},
            t0 - 30 * 60,
        )
    _record(
        tmp_path,
        "band_change",
        {"band": "novelty", "old_value": 1.0, "new_value": 0.9, "reason": "test"},
        t0 - 30 * 60,
    )


def _tripped_synthetic_logs(tmp_path, t0=T0):
    """Same as clean but with 5 escalations: escalation_rate 0.5 > 0.20."""
    _clean_synthetic_logs(tmp_path, t0=t0)
    for i in range(4):
        _record(
            tmp_path,
            "escalation",
            {"job_id": f"j-{i + 2}", "reason": "low-confidence"},
            t0 - 30 * 60,
        )


# ── v2 policy file ───────────────────────────────────────────────────


def test_v2_policy_is_armed_monitor_only():
    policy = load_watchdog_policy(POLICY_V2)
    assert policy.enabled is True
    assert policy.mode == "monitor-only"


def test_v2_policy_rearm_principal_is_mbgulden():
    data = yaml.safe_load(POLICY_V2.read_text(encoding="utf-8"))
    assert data["rearm_principal"] == "mbgulden"


def test_v2_thresholds_byte_identical_to_v1():
    v1 = yaml.safe_load(POLICY_V1.read_text(encoding="utf-8"))
    v2 = yaml.safe_load(POLICY_V2.read_text(encoding="utf-8"))
    expected = {
        "rollback_rate": 0.05,
        "escalation_rate": 0.20,
        "ci_failure_rate": 0.30,
        "jev_error_rate": 0.10,
        "band_change_velocity_per_day": 3,
        "merge_volume_per_hour": 1.5,
    }
    assert v1["thresholds"] == expected
    assert v2["thresholds"] == v1["thresholds"]


def test_v1_policy_untouched_still_hard_disabled():
    data = yaml.safe_load(POLICY_V1.read_text(encoding="utf-8"))
    assert data["version"] == "watchdog-v1"
    assert data["enabled"] is False
    assert data["mode"] == "monitor-only"


# ── v2 feed config file ──────────────────────────────────────────────


def test_v2_feed_config_is_enabled():
    config = load_metrics_feed_config(FEED_V2)
    assert config.version == "metrics-feed-v2"
    assert config.enabled is True


def test_v2_feed_logs_and_windows_identical_to_v1():
    v1 = yaml.safe_load(FEED_V1.read_text(encoding="utf-8"))
    v2 = yaml.safe_load(FEED_V2.read_text(encoding="utf-8"))
    assert v2["event_log"] == v1["event_log"]
    assert v2["auto_merge_log"] == v1["auto_merge_log"]
    assert v2["windows"] == v1["windows"]
    assert v1["enabled"] is False  # v1 stays hard-disabled


# ── run_feed behavior ────────────────────────────────────────────────


def test_run_feed_v2_clean_logs_is_clear_never_disabled(tmp_path):
    _clean_synthetic_logs(tmp_path)
    config_path = _v2_feed_config_with_synthetic_logs(tmp_path)
    state = run_feed(
        config_path,
        POLICY_V2,
        audit_log=tmp_path / "watchdog-audit.jsonl",
        now=T0 + 60,
    )
    assert state == STATE_CLEAR
    assert state != STATE_DISABLED


def test_run_feed_v2_trips_monitor_only_and_never_halts(tmp_path):
    _tripped_synthetic_logs(tmp_path)
    config_path = _v2_feed_config_with_synthetic_logs(tmp_path)
    state = run_feed(
        config_path,
        POLICY_V2,
        audit_log=tmp_path / "watchdog-audit.jsonl",
        now=T0 + 60,
    )
    assert state == STATE_TRIPPED_MONITOR
    rows = [
        json.loads(line)
        for line in (tmp_path / "watchdog-audit.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["state"] == STATE_TRIPPED_MONITOR


def test_run_feed_defaults_still_disabled(tmp_path):
    """Fail-closed: no args means the v1 (hard-disabled) defaults."""
    assert run_feed() == STATE_DISABLED
    assert STATE_DISABLED == "disabled"


# ── CLI overrides ────────────────────────────────────────────────────


def test_cli_honors_policy_and_config_overrides(tmp_path):
    now = time.time()
    _tripped_synthetic_logs(tmp_path, t0=now)
    config_path = _v2_feed_config_with_synthetic_logs(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(
            [
                "--config",
                str(config_path),
                "--policy",
                str(POLICY_V2),
                "--audit-log",
                str(tmp_path / "cli-audit.jsonl"),
            ]
        )
    assert rc == 0
    assert buf.getvalue().strip() == STATE_TRIPPED_MONITOR


def test_cli_defaults_still_disabled(tmp_path):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([])
    assert rc == 0
    assert buf.getvalue().strip() == STATE_DISABLED
