"""Tests for the v2 agreement-metric rendering in `prismatic progress`.

Covers the plan's three test groups:
- v2 rate math: half-life decay boundary, the 0.25 too-cautious weight,
  and Jev-was-right (D3) exclusion;
- the gate readiness line with synthetic record sets below / at / above
  the thresholds;
- the CLI: `prismatic progress` output contains both the v1 and v2
  sections.

The meter stays read-only throughout: nothing here advances a phase,
changes the active metric (v1), or counts replayed backfill records.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from prismatic.review_factory.progress import (
    agreement_section,
    build_report,
    render_status,
)
from prismatic.review_factory.shadow_agreement import (
    decayed_agreement_rate,
    record_weight,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _agreed(n: int, days_ago: float | None = None) -> list[dict]:
    records = [
        {"system_call": "merge", "actual_outcome": "merged", "pr_number": i}
        for i in range(n)
    ]
    if days_ago is not None:
        for r in records:
            r["decided_at"] = _ts(days_ago)
    return records


def _d2(n: int, days_ago: float = 0.0, start: int = 1000) -> list[dict]:
    """Too-cautious disagreements: Jev said skip, human merged, outcome clean."""
    return [
        {
            "system_call": "skip",
            "actual_outcome": "merged",
            "outcome": "clean",
            "decided_at": _ts(days_ago),
            "pr_number": start + i,
        }
        for i in range(n)
    ]


def _d3(n: int, days_ago: float = 0.0, start: int = 2000) -> list[dict]:
    """Jev-was-right disagreements: Jev said skip, human merged, rolled back."""
    return [
        {
            "system_call": "skip",
            "actual_outcome": "merged",
            "outcome": "rolled_back",
            "decided_at": _ts(days_ago),
            "pr_number": start + i,
        }
        for i in range(n)
    ]


def _pending(n: int, days_ago: float, start: int = 3000) -> list[dict]:
    """Skip/merged disagreements with no joined outcome yet."""
    return [
        {
            "system_call": "skip",
            "actual_outcome": "merged",
            "decided_at": _ts(days_ago),
            "pr_number": start + i,
        }
        for i in range(n)
    ]


def _evidence(records: list[dict], bad_merge_calls: int = 0) -> dict:
    return {"shadow_records": records, "bad_merge_calls": bad_merge_calls}


# ── v2 rate math ───────────────────────────────────────────────────────


def test_record_weight_half_life_boundary():
    """At exactly one half-life (14d) an agreement weighs exactly 0.5."""
    record = _agreed(1, days_ago=14.0)[0]
    assert record_weight(record, as_of=NOW) == pytest.approx(0.5)


def test_record_weight_too_cautious_quarter_weight():
    """A too-cautious (D2) disagreement weighs 0.25 before decay."""
    record = _d2(1, days_ago=0.0)[0]
    assert record_weight(record, as_of=NOW) == pytest.approx(0.25)


def test_d3_exclusion_never_lowers_rate():
    """Jev-was-right records contribute zero weight: adding them cannot
    lower the decayed rate."""
    base = _agreed(10, days_ago=1.0)
    with_d3 = base + _d3(5, days_ago=1.0)
    rate_base = decayed_agreement_rate(base, as_of=NOW)["decayed_rate"]
    rate_with = decayed_agreement_rate(with_d3, as_of=NOW)["decayed_rate"]
    assert rate_base == pytest.approx(1.0)
    assert rate_with == pytest.approx(rate_base)
    assert decayed_agreement_rate(with_d3, as_of=NOW)["n_d3"] == 5


def test_decay_fades_old_disagreements():
    """A D1 disagreement 28 days old (two half-lives) weighs 0.25."""
    record = {
        "system_call": "merge",
        "actual_outcome": "closed_unmerged",
        "decided_at": _ts(28.0),
        "pr_number": 1,
    }
    assert record_weight(record, as_of=NOW) == pytest.approx(0.25)


# ── readiness line ───────────────────────────────────────────────────


def test_agreement_section_below_thresholds():
    lines = agreement_section(_evidence(_agreed(5)), now=NOW)
    text = "\n".join(lines)
    assert "Agreement — v1 active · v2 shadow:" in text
    assert "v1: 100.0% over 5 decided PRs (active metric)" in text
    assert "v2: 100.0% decayed · 5 decided PRs · effective sample 5.0" in text
    assert "PRs≥30 ✗" in text
    assert "effective sample≥10 ✗" in text
    assert "zero bad merges ✓" in text
    assert "observation windows complete ✓" in text
    assert "v2 shown for readiness only; v1 remains the active metric." in text


def test_agreement_section_at_thresholds_all_ready():
    lines = agreement_section(_evidence(_agreed(30, days_ago=0.0)), now=NOW)
    text = "\n".join(lines)
    assert "PRs≥30 ✓" in text
    assert "effective sample≥10 ✓" in text
    assert "zero bad merges ✓" in text
    assert "observation windows complete ✓" in text


def test_agreement_section_d2_dents_v2_rate_but_stays_ready():
    """30 agreements + 5 too-cautious: v2 = 30/31.25 = 96.0%, still ready."""
    records = _agreed(30, days_ago=0.0) + _d2(5, days_ago=0.0)
    lines = agreement_section(_evidence(records), now=NOW)
    text = "\n".join(lines)
    assert "v2: 96.0% decayed · 35 decided PRs · effective sample 31.2" in text
    assert "PRs≥30 ✓" in text
    assert "effective sample≥10 ✓" in text


def test_open_observation_window_blocks_readiness():
    """A skip/merged disagreement decided 1 day ago (no outcome yet)
    leaves its 7-day window open."""
    records = _agreed(30, days_ago=0.0) + _pending(1, days_ago=1.0)
    lines = agreement_section(_evidence(records), now=NOW)
    text = "\n".join(lines)
    assert "observation windows complete ✗" in text
    # The other gates are unaffected by the open window.
    assert "PRs≥30 ✓" in text
    assert "zero bad merges ✓" in text


def test_legacy_pending_does_not_block_readiness():
    """A pending-class record with no decided_at is legacy: no outcome
    will ever arrive, so it must not hold the window open."""
    legacy = [
        {
            "system_call": "skip",
            "actual_outcome": "merged",
            "pr_number": 999,
        }
    ]
    lines = agreement_section(_evidence(_agreed(30) + legacy), now=NOW)
    assert "observation windows complete ✓" in "\n".join(lines)


def test_bad_merge_calls_fail_readiness():
    lines = agreement_section(_evidence(_agreed(30, days_ago=0.0), 2), now=NOW)
    assert "zero bad merges ✗" in "\n".join(lines)


def test_v2_active_hides_readiness_note():
    lines = agreement_section(_evidence(_agreed(5)), now=NOW, active_metric="v2")
    text = "\n".join(lines)
    assert "Agreement — v2 active · v1 shadow:" in text
    assert "v2: 100.0% decayed · 5 decided PRs · effective sample 5.0" in text
    assert "(active metric)" in text.splitlines()[2]
    assert "(active metric)" not in text.splitlines()[1]
    assert "readiness only" not in text


def test_empty_evidence_renders_zeros():
    lines = agreement_section(_evidence([]), now=NOW)
    text = "\n".join(lines)
    assert "v1: n/a over 0 decided PRs (active metric)" in text
    assert "v2: n/a decayed · 0 decided PRs · effective sample 0.0" in text


# ── report + CLI wiring ──────────────────────────────────────────────


def test_build_report_includes_agreement_lines():
    report = build_report(0, False, _evidence(_agreed(30, days_ago=0.0)), now=NOW)
    assert report.agreement_lines
    rendered = render_status(report)
    assert "Agreement — v1 active · v2 shadow:" in rendered
    assert "v2 readiness:" in rendered
    assert "v1 remains the active metric." in rendered


def test_cli_progress_shows_both_sections(tmp_path, capsys):
    from prismatic.cli.progress import main

    audit = tmp_path / "audit"
    audit.mkdir()
    with open(audit / "shadow-records.jsonl", "w", encoding="utf-8") as f:
        for record in _agreed(3):
            f.write(json.dumps(record) + "\n")

    rc = main(["--audit-dir", str(audit)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Agreement — v1 active · v2 shadow:" in out
    assert "v1: 100.0% over 3 decided PRs (active metric)" in out
    assert "v2: 100.0% decayed · 3 decided PRs · effective sample 3.0" in out
    assert "v2 readiness:" in out
    assert "v1 remains the active metric." in out
