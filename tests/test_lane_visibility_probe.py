"""Tests for scripts/lane_visibility_probe.py (WI-6, cron overhaul plan).

The probe is the check that would have caught the July blind-lane incident:
the dispatcher built ``agent::<name>`` (double-colon) labels while Linear only
had ``agent:<name>`` (single-colon), so every lane silently matched nothing.

Fail-first contract of this suite:
  * a fixture reproducing the double-colon-era query construction MUST fail
    the probe (bug class detected);
  * a fixture reproducing the current single-colon construction MUST pass;
  * with no Linear credentials the script must exit cleanly (code 2, no
    traceback) instead of crashing.

All core logic is exercised with injected fixtures — no Linear access, no
dispatcher import, no network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "scripts" / "lane_visibility_probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location(
        "lane_visibility_probe", PROBE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec: the script uses `from __future__ import
    # annotations`, and dataclass field resolution needs the module present
    # in sys.modules.
    sys.modules["lane_visibility_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def probe():
    return load_probe()


# ── Fixtures modelling the two eras of dispatcher query logic ──────────────

LANES = ["fred", "george", "kai"]


def double_colon_era_label_for(agent_name: str) -> str:
    """Pre-#572 dispatcher construction: ``agent::<name>`` (matched nothing)."""
    return f"agent::{agent_name}"


def current_label_for(agent_name: str) -> str:
    """Current dispatcher construction: ``agent:<name>`` (canonical)."""
    return f"agent:{agent_name}"


BOX_LABELS = ["agent:fred", "agent:george", "agent:kai", "dispatch:ready"]


# ── Fail-first: the pre-#572 bug class must fail the probe ─────────────────


def test_double_colon_era_logic_fails_probe(probe):
    checks = probe.probe_lanes(
        LANES, double_colon_era_label_for, BOX_LABELS
    )
    assert len(checks) == 3
    for check in checks:
        assert check.ok is False
        assert check.canonical is False  # non-canonical form is the bug
    report = probe.summarize(checks)
    assert report["ok"] is False
    assert report["blind_lanes"] == LANES


def test_evaluate_returns_nonzero_exit_for_double_colon_era(probe):
    report, exit_code = probe.evaluate(
        LANES, double_colon_era_label_for, BOX_LABELS
    )
    assert exit_code == 1
    assert report["ok"] is False


def test_canonical_check_catches_bug_even_if_box_had_double_colon(probe):
    """Defense in depth: the canonical-form assertion must fire even when the
    (buggy) labels happen to exist on the box, so a membership-only check
    could never silently pass the pre-#572 bug class."""
    box_with_bug = [f"agent::{name}" for name in LANES]
    checks = probe.probe_lanes(LANES, double_colon_era_label_for, box_with_bug)
    assert all(check.ok is False for check in checks)
    assert all(check.canonical is False for check in checks)


# ── Current logic must pass ────────────────────────────────────────────────


def test_current_logic_passes_probe(probe):
    checks = probe.probe_lanes(LANES, current_label_for, BOX_LABELS)
    assert all(check.ok for check in checks)
    report, exit_code = probe.evaluate(LANES, current_label_for, BOX_LABELS)
    assert exit_code == 0
    assert report["ok"] is True
    assert report["blind_lanes"] == []


def test_probe_uses_same_construction_as_dispatcher_mirror(probe):
    """The probe's default label construction must equal the dispatcher's
    lane-scan construction (``f"agent:{agent_name}"``)."""
    for name in LANES:
        assert probe.dispatcher_lane_label(name) == f"agent:{name}"


# ── Label missing on the box is a blind lane ───────────────────────────────


def test_label_missing_on_box_is_blind(probe):
    box_missing_kai = ["agent:fred", "agent:george", "dispatch:ready"]
    checks = probe.probe_lanes(LANES, current_label_for, box_missing_kai)
    by_agent = {c.agent: c for c in checks}
    assert by_agent["fred"].ok is True
    assert by_agent["george"].ok is True
    kai = by_agent["kai"]
    assert kai.ok is False
    assert kai.canonical is True  # form is fine; the label just isn't on the box
    assert kai.known_on_box is False
    report, exit_code = probe.evaluate(LANES, current_label_for, box_missing_kai)
    assert exit_code == 1
    assert report["blind_lanes"] == ["kai"]


def test_empty_lane_set_is_a_config_error(probe):
    report, exit_code = probe.evaluate([], current_label_for, BOX_LABELS)
    assert exit_code == 2
    assert "zero lanes" in report["error"].lower()


# ── Canonical form edge cases ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("agent:fred", True),
        ("agent:george-2", True),
        ("agent:kai_x", True),
        ("agent::fred", False),  # the July bug
        ("agent:", False),
        ("agent: fred", False),
        ("agent:fred:extra", False),
        ("fred", False),
        ("Agent:fred", False),
        ("", False),
    ],
)
def test_canonical_lane_label_form(probe, label, expected):
    assert probe.is_canonical_lane_label(label) is expected


# ── Read-only contract: label lookup must never create labels ──────────────


def test_fetch_team_labels_uses_readonly_query(probe):
    calls = []

    def fake_gql(query, variables=None, *, source=""):
        calls.append({"query": query, "variables": variables, "source": source})
        return {
            "team": {
                "labels": {
                    "nodes": [
                        {"id": "lbl-1", "name": "agent:fred"},
                        {"id": "lbl-2", "name": "agent:george"},
                    ]
                }
            }
        }

    names = probe.fetch_team_label_names(fake_gql, "team-123")
    assert names == ["agent:fred", "agent:george"]
    assert len(calls) == 1
    query = calls[0]["query"]
    assert "GetTeamLabels" in query
    assert "mutation" not in query.lower()
    assert "issueLabelCreate" not in query
    assert calls[0]["variables"] == {"teamId": "team-123"}


# ── Signal emission ────────────────────────────────────────────────────────


def test_signal_emitted_on_blind_lane_and_not_on_success(probe):
    emitted = []
    report, exit_code = probe.evaluate(
        LANES,
        double_colon_era_label_for,
        BOX_LABELS,
        emit_signal_fn=emitted.append,
    )
    assert exit_code == 1
    assert len(emitted) == 1
    assert "agent::fred" in emitted[0]

    emitted.clear()
    report, exit_code = probe.evaluate(
        LANES, current_label_for, BOX_LABELS, emit_signal_fn=emitted.append
    )
    assert exit_code == 0
    assert emitted == []


def test_emit_signal_never_raises(probe, tmp_path, monkeypatch):
    # Point the emit-tool search at an empty dir and away from the real
    # workspace so no tool is found; either way the contract is "never raises".
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = probe.emit_signal("test alert that must not raise")
    assert result in (True, False)


# ── Report shape ───────────────────────────────────────────────────────────


def test_report_is_json_serializable(probe):
    report, _ = probe.evaluate(LANES, current_label_for, BOX_LABELS)
    text = json.dumps(report)
    parsed = json.loads(text)
    assert parsed["lanes_checked"] == 3
    assert parsed["lanes"][0]["agent"] == "fred"
    assert parsed["lanes"][0]["label"] == "agent:fred"
    assert set(parsed) >= {"ok", "lanes_checked", "blind_lanes", "lanes"}


# ── No Linear credentials → clean skip, exit 2, no traceback ───────────────


def test_no_linear_credentials_clean_skip():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("LINEAR_API_KEY", "PRISMATIC_TEAM_ID")
    }
    result = subprocess.run(
        [sys.executable, str(PROBE_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert "SKIP" in combined
    assert "LINEAR_API_KEY" in combined
