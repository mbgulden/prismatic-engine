"""Tests for the Jev progress meter (read-only level-up rendering).

Every test asserts the meter's read-only contract:
- gate pass/fail always comes from the mechanical exit criteria, never
  from reimplemented logic;
- missing evidence renders as zeros, never raises;
- the command exits 0 in every case.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from prismatic.review_factory.progress import (
    FRESH_INSTALL_LINE,
    build_report,
    build_status,
    default_evidence_pointers,
    load_status_evidence,
    render_status,
    status_json,
)


def _complete_signal(pr_number: int = 1) -> dict:
    return {
        "agent": "prismatic-shadow-observer",
        "event_type": "decision",
        "id": f"shadow-{pr_number}",
        "message": f"shadow call for PR #{pr_number}: MERGE",
        "metadata": {
            "pr_number": pr_number,
            "call": "merge",
            "policy_version": "v2",
        },
        "severity": "info",
        "source": "shadow-observer",
        "status": "ok",
        "timestamp": "2026-09-22T00:00:00Z",
    }


def _agreed_records(n: int, start: int = 1) -> list[dict]:
    return [
        {"system_call": "merge", "actual_outcome": "merged", "pr_number": start + i}
        for i in range(n)
    ]


def _phase0_passing_evidence() -> dict:
    return {
        "shadow_records": _agreed_records(30),
        "bad_merge_calls": 0,
        "shadow_signals": [_complete_signal(1)],
        "watchdog": {"enabled": True, "mode": "monitor-only"},
    }


def _recent_ts(hours_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _phase1_evidence(n_clean: int = 20, n_rolled_back: int = 0) -> dict:
    decisions = [
        {"decision": "allowed", "outcome": "clean", "timestamp": _recent_ts(i + 1)}
        for i in range(n_clean)
    ]
    decisions += [
        {
            "decision": "allowed",
            "outcome": "rolled_back",
            "timestamp": _recent_ts(n_clean + i + 1),
            "pr_number": 900 + i,
        }
        for i in range(n_rolled_back)
    ]
    return {"auto_merge_decisions": decisions}


# ── phase 0→1 ────────────────────────────────────────────────────────


def test_all_gates_pass_renders_ready():
    report = build_report(0, True, _phase0_passing_evidence())
    assert report.ready is True
    text = render_status(report)
    assert "READY" in text
    assert "explicit approval" in text
    assert all(row.met for row in report.rows)


def test_shadow_prs_below_target_shows_progress_bar():
    evidence = _phase0_passing_evidence()
    evidence["shadow_records"] = _agreed_records(12)
    report = build_report(0, True, evidence)
    assert report.ready is False
    row = next(r for r in report.rows if r.key == "min_prs_resolved")
    assert row.met is False
    assert row.current == "12/30"
    assert "12/30" in row.detail


def test_agreement_failure_names_the_disagreeing_prs():
    records = _agreed_records(28)
    records.append({"system_call": "skip", "actual_outcome": "merged", "pr_number": 12})
    records.append({"system_call": "skip", "actual_outcome": "merged", "pr_number": 18})
    evidence = _phase0_passing_evidence()
    evidence["shadow_records"] = records
    report = build_report(0, True, evidence)
    row = next(r for r in report.rows if r.key == "min_agreement")
    assert row.met is False
    assert "#12" in row.detail and "#18" in row.detail
    assert "you merged" in row.detail


def test_bad_merge_call_blocks_and_teaches():
    evidence = _phase0_passing_evidence()
    evidence["bad_merge_calls"] = 2
    report = build_report(0, True, evidence)
    row = next(r for r in report.rows if r.key == "zero_bad_merge_calls")
    assert row.met is False
    assert row.current == "2"
    assert "rollback" in row.detail


def test_watchdog_not_armed_blocks():
    evidence = _phase0_passing_evidence()
    evidence["watchdog"] = {"enabled": False, "mode": ""}
    report = build_report(0, True, evidence)
    row = next(r for r in report.rows if r.key == "watchdog_armed_monitor_only")
    assert row.met is False
    assert row.current == "not armed"
    assert "monitor-only" in row.detail


def test_incomplete_shadow_signals_block():
    evidence = _phase0_passing_evidence()
    evidence["shadow_signals"] = []
    report = build_report(0, True, evidence)
    row = next(r for r in report.rows if r.key == "all_shadow_signals_complete")
    assert row.met is False


def test_ready_but_machinery_disabled_says_so():
    report = build_report(0, False, _phase0_passing_evidence())
    assert report.ready is True
    text = render_status(report)
    assert "disabled by policy" in text
    assert "READY — level up" not in text
    payload = status_json(report)
    assert payload["machinery_disabled"] is True


# ── phase 1→2 ────────────────────────────────────────────────────────


def test_phase1_twenty_clean_merges_ready():
    report = build_report(1, True, _phase1_evidence(20, 0))
    assert report.ready is True
    assert "READY" in render_status(report)


def test_phase1_rollback_breaks_streak_and_names_it():
    evidence = _phase1_evidence(19, 1)
    report = build_report(1, True, evidence)
    assert report.ready is False
    row = next(r for r in report.rows if r.key == "zero_rollbacks_in_streak")
    assert row.met is False
    assert row.current == "1"
    assert "rolled back" in row.detail
    assert "#900" in row.detail


# ── phase 2→3 ────────────────────────────────────────────────────────


def _phase2_evidence(merges: int, rollbacks: int) -> dict:
    decisions = []
    for i in range(merges):
        outcome = "rolled_back" if i < rollbacks else "clean"
        decisions.append(
            {
                "decision": "allowed",
                "outcome": outcome,
                "timestamp": _recent_ts(hours_ago=(i + 1) * 3),
            }
        )
    return {
        "auto_merge_decisions": decisions,
        "novelty_pages": ["np-1"],
        "weekly_reports": [
            {"novelty_pages_justified": [{"id": "np-1", "justification": "ok"}]}
        ],
    }


def test_phase2_clean_window_ready():
    report = build_report(2, True, _phase2_evidence(10, 0))
    assert report.ready is True


def test_phase2_high_rollback_rate_teaches_counts():
    report = build_report(2, True, _phase2_evidence(10, 1))
    assert report.ready is False
    row = next(r for r in report.rows if r.key == "rollback_rate_below_2pct")
    assert row.met is False
    assert row.current == "10.00%"
    assert "10 merges, 1 rollbacks" in row.detail


def test_phase2_unjustified_novelty_page_blocks():
    evidence = _phase2_evidence(10, 0)
    evidence["weekly_reports"] = []
    report = build_report(2, True, evidence)
    row = next(r for r in report.rows if r.key == "every_novelty_page_justified")
    assert row.met is False
    assert "0 justified" in row.detail


# ── no evidence / fresh install ──────────────────────────────────────


def test_no_evidence_renders_zeros_without_crashing(tmp_path):
    report = build_status(
        audit_dir=tmp_path / "audit", spec_dir=tmp_path / "no-such-spec"
    )
    assert report.fresh_install is True
    text = render_status(report)
    assert FRESH_INSTALL_LINE in text
    payload = status_json(report)
    assert payload["fresh_install"] is True
    assert payload["ready"] is False


def test_load_status_evidence_never_raises(tmp_path):
    evidence = load_status_evidence(audit_dir=tmp_path / "missing")
    # only inline empty defaults — nothing observed
    assert evidence.get("bad_merge_calls", 0) == 0
    assert not evidence.get("shadow_records")
    assert not evidence.get("auto_merge_decisions")


def test_max_phase_renders_max_level():
    report = build_report(3, True, {})
    text = render_status(report)
    assert "max level reached" in text
    payload = status_json(report)
    assert payload["next_transition"] is None


# ── json contract ────────────────────────────────────────────────────


def test_json_schema_keys_stable():
    report = build_report(0, True, _phase0_passing_evidence())
    payload = status_json(report)
    assert set(payload) == {
        "level",
        "level_name",
        "next_transition",
        "gates",
        "ready",
        "machinery_disabled",
        "fresh_install",
    }
    assert payload["level"] == 0
    assert payload["level_name"] == "Observer"
    assert payload["next_transition"] == [0, 1]
    for gate in payload["gates"]:
        assert set(gate) == {"key", "label", "current", "target", "met", "detail"}
    assert len(payload["gates"]) == 5


def test_default_evidence_pointers_cover_load_evidence_keys(tmp_path):
    pointers = default_evidence_pointers(tmp_path)
    assert set(pointers) == {
        "shadow_records",
        "shadow_signals",
        "bad_merge_calls",
        "watchdog_policy",
        "auto_merge_decisions",
        "novelty_pages",
        "weekly_reports_dir",
    }


# ── CLI ──────────────────────────────────────────────────────────────


def test_cli_main_exit_zero_with_missing_audit_dir(tmp_path, capsys):
    from prismatic.cli.progress import main

    rc = main(["--audit-dir", str(tmp_path / "nope")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Jev" in out


def test_cli_main_json_is_valid(tmp_path, capsys):
    from prismatic.cli.progress import main

    rc = main(["--audit-dir", str(tmp_path / "nope"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "gates" in payload and "ready" in payload


# ─────────────────────────────────────────────────────────────────────
# T1 deterministic "what blocks the next level" rows (item 5)
# ─────────────────────────────────────────────────────────────────────


def _t1_decision(pr: int, day: int, cls: str = "docs", **over) -> dict:
    decision = {
        "pr": pr,
        "timestamp": f"2026-09-{day:02d}T10:00:00Z",
        "change_class": cls,
        "ci_green": True,
        "novelty_clean": True,
        "no_policy_exclusions": True,
        "deterministic_clean": True,
        "receipt_emitted": True,
    }
    decision.update(over)
    return decision


def _det_evidence(*decisions, **over) -> dict:
    evidence = {
        "exit_path": "deterministic",
        "deterministic_decisions": list(decisions),
    }
    evidence.update(over)
    return evidence


def _rows_by_key(report):
    return {row.key: row for row in report.rows}


def test_deterministic_rows_render_streak_activation_and_checklist():
    evidence = _det_evidence(
        _t1_decision(557, 25), _t1_decision(558, 26), _t1_decision(559, 27)
    )
    report = build_report(0, True, evidence)
    rows = _rows_by_key(report)
    # T1 activation state is read-only and off.
    assert rows["t1_activation"].current == "off — Gate B not activated"
    assert rows["t1_activation"].met is False
    assert "never activates T1" in rows["t1_activation"].detail
    # Streak bar.
    assert rows["min_consecutive_deterministic_clean"].current == "3/20"
    assert rows["min_consecutive_deterministic_clean"].met is False
    # Latest PR checklist: all five pass.
    latest = rows["latest_t1_checklist"]
    assert latest.label == "Latest T1 PR #559"
    assert latest.current.count("✓") == 5
    assert latest.current.count("✗") == 0
    assert latest.met is True
    assert latest.detail == ""


def test_deterministic_rows_name_exact_failing_conditions():
    evidence = _det_evidence(
        _t1_decision(557, 25),
        _t1_decision(558, 26, ci_green=False, receipt_emitted=False),
    )
    report = build_report(0, True, evidence)
    rows = _rows_by_key(report)
    latest = rows["latest_t1_checklist"]
    assert latest.met is False
    assert latest.current.count("✗") == 2
    assert "ci_green" in latest.current and "receipt_emitted" in latest.current
    # The exact failed conditions are named, not a generic "blocked".
    assert "CI green (incl. Review Factory)" in latest.detail
    assert "Signed receipt emitted" in latest.detail
    assert "Novelty-clean" not in latest.detail
    streak = rows["min_consecutive_deterministic_clean"]
    assert streak.current == "0/20"  # newest decision is the breaker
    assert "#558" in streak.detail
    assert "CI green (incl. Review Factory)" in streak.detail


def test_deterministic_rows_streak_counts_only_trailing_run():
    evidence = _det_evidence(
        _t1_decision(555, 23, ci_green=False),  # old breaker
        _t1_decision(556, 24),
        _t1_decision(557, 25),
        _t1_decision(558, 26, cls="agent_standard"),  # non-T1 breaks the run
        _t1_decision(559, 27),
    )
    report = build_report(0, True, evidence)
    rows = _rows_by_key(report)
    assert rows["min_consecutive_deterministic_clean"].current == "1/20"
    assert "#558" in rows["min_consecutive_deterministic_clean"].detail


def test_deterministic_rows_empty_evidence_renders_zeros():
    report = build_report(0, True, _det_evidence())
    rows = _rows_by_key(report)
    assert rows["min_consecutive_deterministic_clean"].current == "0/20"
    assert rows["latest_t1_checklist"].current == "none yet"
    assert "No T1 evidence yet" in rows["min_consecutive_deterministic_clean"].detail


def test_agreement_path_rows_unchanged_without_exit_path():
    evidence = {"shadow_records": _agreed_records(30)}
    report = build_report(0, True, evidence)
    rows = _rows_by_key(report)
    assert "t1_activation" not in rows
    assert "min_prs_resolved" in rows  # legacy agreement row still there


def test_deterministic_report_never_mutates_evidence():
    evidence = _det_evidence(_t1_decision(557, 25), _t1_decision(558, 26))
    snapshot = json.dumps(evidence, sort_keys=True)
    build_report(0, True, evidence)
    render_status(build_report(0, True, evidence))
    status_json(build_report(0, True, evidence))
    assert json.dumps(evidence, sort_keys=True) == snapshot


def test_deterministic_status_json_contract():
    evidence = _det_evidence(_t1_decision(558, 26, novelty_clean=False))
    payload = status_json(build_report(0, True, evidence))
    keys = {gate["key"] for gate in payload["gates"]}
    assert {
        "t1_activation",
        "min_consecutive_deterministic_clean",
        "latest_t1_checklist",
    } <= keys
    by_key = {gate["key"]: gate for gate in payload["gates"]}
    assert by_key["latest_t1_checklist"]["detail"] != ""
    assert "Novelty-clean" in by_key["latest_t1_checklist"]["detail"]


def test_deterministic_render_smoke():
    evidence = _det_evidence(_t1_decision(558, 26, ci_green=False))
    text = render_status(build_report(0, True, evidence))
    assert "T1 auto-merge" in text
    assert "Consecutive clean T1 PRs" in text
    assert "Latest T1 PR #558" in text
