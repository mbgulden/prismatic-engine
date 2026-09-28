"""Tests for scripts/lane_visibility_probe.py (WI-6, cron overhaul plan).

What the probe guards is box-side label drift: a lane label renamed or
deleted in the Linear UI makes that lane's scans silently match nothing.
It does NOT guard the July incident's code-side failure mode — the probe
builds labels with its own hardcoded mirror construction, so fed the July
scenario (dispatcher code building ``agent::<name>`` while the box had
``agent:<name>``) it would still exit 0. Closing that gap needs a shared
constructor or a source-bound check (follow-up, not this PR).

Fail-first contract of this suite:
  * a fixture feeding a non-canonical (double-colon) construction MUST fail
    the probe — this guards the probe's own construction contract (the
    canonical-form spec assertion), not the dispatcher incident;
  * a fixture reproducing the current single-colon construction MUST pass;
  * a label missing from the box's label list MUST fail the probe — this is
    the drift check the probe actually exists for;
  * an error-shaped GraphQL payload MUST raise instead of reading as an
    empty label list (exit 2, not a false exit-1 alarm);
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


# ── Fixtures: injected label constructions ─────────────────────────────────
# NOTE: these fixtures inject a *construction*, not the dispatcher's code.
# They exercise the probe's own canonical-form contract — they do not (and
# cannot) reproduce the July code-side incident, which this probe cannot see.

LANES = ["fred", "george", "kai"]


def double_colon_label_for(agent_name: str) -> str:
    """Non-canonical construction: ``agent::<name>`` (violates the spec)."""
    return f"agent::{agent_name}"


def current_label_for(agent_name: str) -> str:
    """Canonical construction: ``agent:<name>``."""
    return f"agent:{agent_name}"


BOX_LABELS = ["agent:fred", "agent:george", "agent:kai", "dispatch:ready"]


# ── Fail-first: a non-canonical construction must fail the probe ────────────
# This guards the probe's own construction contract (the canonical-form spec
# assertion), not the July dispatcher incident.


def test_noncanonical_construction_fails_probe(probe):
    checks = probe.probe_lanes(
        LANES, double_colon_label_for, BOX_LABELS
    )
    assert len(checks) == 3
    for check in checks:
        assert check.ok is False
        assert check.canonical is False  # non-canonical form is the violation
    report = probe.summarize(checks)
    assert report["ok"] is False
    assert report["blind_lanes"] == LANES


def test_evaluate_returns_nonzero_exit_for_noncanonical_construction(probe):
    report, exit_code = probe.evaluate(
        LANES, double_colon_label_for, BOX_LABELS
    )
    assert exit_code == 1
    assert report["ok"] is False


def test_canonical_check_fires_even_if_box_has_noncanonical_label(probe):
    """Defense in depth: the canonical-form assertion must fire even when
    non-canonical labels happen to exist on the box, so a membership-only
    check could never silently pass a malformed construction."""
    box_with_noncanonical = [f"agent::{name}" for name in LANES]
    checks = probe.probe_lanes(LANES, double_colon_label_for, box_with_noncanonical)
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
        ("agent::fred", False),  # the July incident's label form
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


def test_fetch_team_labels_error_payload_raises(probe):
    """An error-shaped GraphQL payload must raise (exit 2 upstream), not
    read as an empty label list (which would be a false exit-1 alarm)."""

    def error_gql(query, variables=None, *, source=""):
        return {"errors": [{"message": "boom"}]}

    with pytest.raises(probe.ProbeError):
        probe.fetch_team_label_names(error_gql, "team-123")


# ── Signal emission ────────────────────────────────────────────────────────


def test_signal_emitted_on_blind_lane_and_not_on_success(probe):
    emitted = []
    report, exit_code = probe.evaluate(
        LANES,
        double_colon_label_for,
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
