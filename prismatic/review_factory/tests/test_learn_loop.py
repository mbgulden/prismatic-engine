"""Tests for the learn loop + self-tuning risk bands (chunk 4).

Every test asserts a safety property the learn loop claims:
- disabled means inert: nothing read, nothing written, nothing proposed;
- outcomes are mechanical (explicit rows only — silence is pending, not clean);
- tightening is always allowed; loosening needs mechanical evidence AND the
  weekly budget, and beyond the budget it is flagged for Michael, never
  blessed;
- no band change is ever applied — proposals are data for the review pipeline;
- every call emits exactly one audit signal.
"""

import json
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory.learn_loop import (
    LearnBands,
    LearnConfigError,
    LearnInputError,
    LearnLoop,
    LearnPolicy,
    LearnReport,
    load_learn_bands,
    load_learn_policy,
    parse_decision_log,
)

HERE = Path(__file__).resolve()
SPEC_LEARN = HERE.parent.parent / "spec" / "learn_loop_policy_v1.yaml"
SPEC_BANDS = HERE.parent.parent / "spec" / "auto_merge_bands_v1.yaml"

NOW = 1_800_000_000.0
DAY = 86400.0


# ── fixtures ─────────────────────────────────────────────────────────


def _write_policy(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_LEARN.read_text(encoding="utf-8"))
    data.update(overrides)
    path = tmp_path / "learn_loop_policy_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _write_bands(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_BANDS.read_text(encoding="utf-8"))
    data.update(overrides)
    path = tmp_path / "auto_merge_bands_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _loop(
    tmp_path, enabled=False, now=NOW, policy_overrides=None, bands_overrides=None
):
    policy_overrides = dict(policy_overrides or {})
    policy_overrides.setdefault("enabled", enabled)
    return LearnLoop(
        _write_policy(tmp_path, **policy_overrides),
        _write_bands(tmp_path, **dict(bands_overrides or {})),
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
        now_fn=lambda: now,
    )


def _decision(job_id, ts, decision="allowed", tier=0):
    """One authority-style decision row."""
    return {
        "ts": ts,
        "ts_iso": "2026-09-22T00:00:00+00:00",
        "component": "merge_authority",
        "policy_version": "auto-v1",
        "job_id": job_id,
        "repository": "mbgulden/prismatic-engine",
        "head_sha": "abc123",
        "tier": tier,
        "decision": decision,
        "reason": "test",
        "gates": [],
        "jev_score": None,
        "merge_sha": "def456",
    }


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _write_decisions(path, jobs, decision="allowed", start_ts=NOW, step=DAY):
    rows = [
        _decision(job_id, start_ts - i * step, decision)
        for i, job_id in enumerate(jobs)
    ]
    _write_rows(path, rows)
    return rows


def _write_outcomes(path, entries):
    """entries: (job_id, outcome, ts)."""
    rows = [
        {"job_id": job_id, "ts": ts, "outcome": outcome}
        for job_id, outcome, ts in entries
    ]
    _write_rows(path, rows)


def _read_audit(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _clean_scenario(tmp_path, n, now=NOW):
    """n allowed merges, all explicitly clean. Returns the loop."""
    loop = _loop(tmp_path, enabled=True, now=now)
    jobs = [f"job-{i}" for i in range(n)]
    _write_decisions(loop.decision_log, jobs, start_ts=now)
    _write_outcomes(loop.outcome_log, [(j, "clean", now - 10) for j in jobs])
    return loop


# ── policy loading (6) ───────────────────────────────────────────────


def test_shipped_policy_disabled_report_only():
    policy = load_learn_policy(SPEC_LEARN)
    assert isinstance(policy, LearnPolicy)
    assert policy.enabled is False
    assert policy.mode == "report-only"
    assert policy.review_window_days == 30
    assert policy.good_after_days == 7
    assert policy.clean_outcomes_for_loosen == 20
    assert policy.max_loosen_per_week == 0.05


def test_shipped_bands_parse():
    bands = load_learn_bands(SPEC_BANDS)
    assert isinstance(bands, LearnBands)
    assert bands.version == "auto-bands-v1"
    assert bands.values["human_above"] == 0.60
    assert bands.values["auto_below"] == 0.20


def test_missing_policy_file_is_disabled(tmp_path):
    loop = LearnLoop(
        tmp_path / "no-such-policy.yaml",
        _write_bands(tmp_path),
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
    )
    result = loop.record_outcome("job-1", "clean")
    assert result["status"] == "refused"
    assert "learn_loop_disabled" in result["reason"]
    assert loop.self_review(NOW).state == "disabled"


def test_malformed_policy_raises_on_load(tmp_path):
    bad = tmp_path / "bad-policy.yaml"
    bad.write_text("{not: [valid: yaml", encoding="utf-8")
    with pytest.raises(LearnConfigError):
        load_learn_policy(bad)


def test_malformed_bands_raises_on_load(tmp_path):
    bad = tmp_path / "bad-bands.yaml"
    bad.write_text("just: [a, string", encoding="utf-8")
    with pytest.raises(LearnConfigError):
        load_learn_bands(bad)


def test_missing_bands_file_fails_closed(tmp_path):
    loop = LearnLoop(
        _write_policy(tmp_path, enabled=True),
        tmp_path / "no-such-bands.yaml",
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
    )
    result = loop.record_outcome("job-1", "clean")
    assert result["status"] == "refused"
    assert "bands_config_error" in result["reason"]


# ── disabled inertness (3) ───────────────────────────────────────────


def test_self_review_disabled_reads_nothing(tmp_path):
    loop = _loop(tmp_path, enabled=False)
    report = loop.self_review(NOW)
    assert isinstance(report, LearnReport)
    assert report.state == "disabled"
    assert report.proposals == ()
    assert report.summary == "learn loop disabled"
    rows = _read_audit(loop.audit_log)
    assert len(rows) == 1
    assert rows[0]["action"] == "self_review"
    assert rows[0]["status"] == "disabled"


def test_record_outcome_disabled_refused(tmp_path):
    loop = _loop(tmp_path, enabled=False)
    result = loop.record_outcome("job-1", "clean")
    assert result["status"] == "refused"
    assert result["reason"] == "learn_loop_disabled"
    assert not loop.outcome_log.exists()  # nothing appended


def test_propose_disabled_refused(tmp_path):
    loop = _loop(tmp_path, enabled=False)
    result = loop.propose_band_change("human_above", 0.65)
    assert result["status"] == "refused"
    assert result["reason"] == "learn_loop_disabled"


# ── log parsing (5) ──────────────────────────────────────────────────


def test_decision_rows_parse(tmp_path):
    path = tmp_path / "decisions.jsonl"
    rows = [
        _decision("job-1", NOW),
        _decision("job-2", NOW - DAY, decision="refused"),
    ]
    rows[0]["band_values"] = {"human_above": 0.60}  # forward row w/ bands
    _write_rows(path, rows)
    records, gaps = parse_decision_log(path)
    assert gaps == 0
    assert len(records) == 2
    assert records[0].band_values == {"human_above": 0.60}
    assert records[1].band_values is None  # chunk-1 rows predate the field
    assert records[0].jev_score is None  # Jev not built: parses as data


def test_corrupt_line_skipped(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        json.dumps(_decision("job-1", NOW))
        + "\n"
        + "this is not json\n"
        + json.dumps(_decision("job-2", NOW))
        + "\n",
        encoding="utf-8",
    )
    records, gaps = parse_decision_log(path)
    assert len(records) == 2
    assert gaps == 0


def test_row_missing_field_is_coverage_gap(tmp_path):
    path = tmp_path / "decisions.jsonl"
    good = _decision("job-1", NOW)
    bad = _decision("job-2", NOW)
    del bad["policy_version"]  # required field missing
    _write_rows(path, [good, bad])
    records, gaps = parse_decision_log(path)
    assert gaps == 1
    assert [r.job_id for r in records] == ["job-1"]


def test_missing_decision_log_is_empty(tmp_path):
    records, gaps = parse_decision_log(tmp_path / "no-such-log.jsonl")
    assert records == []
    assert gaps == 0


def test_row_with_non_numeric_ts_is_coverage_gap(tmp_path):
    path = tmp_path / "decisions.jsonl"
    bad = _decision("job-1", NOW)
    bad["ts"] = "yesterday"
    _write_rows(path, [bad])
    records, gaps = parse_decision_log(path)
    assert gaps == 1
    assert records == []


# ── outcome recording (6) ────────────────────────────────────────────


def test_record_outcome_ok(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    _write_decisions(loop.decision_log, ["job-1"])
    result = loop.record_outcome("job-1", "clean")
    assert result == {"status": "ok", "job_id": "job-1", "outcome": "clean"}
    outcomes = [
        json.loads(line)
        for line in loop.outcome_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(outcomes) == 1
    assert outcomes[0]["job_id"] == "job-1"
    assert outcomes[0]["outcome"] == "clean"
    rows = _read_audit(loop.audit_log)
    assert len(rows) == 1
    assert rows[0]["action"] == "record_outcome"
    assert rows[0]["status"] == "ok"


def test_record_outcome_unknown_job_refused(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    _write_decisions(loop.decision_log, ["job-1"])
    result = loop.record_outcome("job-zzz", "clean")
    assert result["status"] == "refused"
    assert "unknown_job" in result["reason"]
    assert not loop.outcome_log.exists()


def test_record_outcome_bad_value_raises(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    _write_decisions(loop.decision_log, ["job-1"])
    with pytest.raises(LearnInputError):
        loop.record_outcome("job-1", "mystery")
    assert not loop.outcome_log.exists()  # fail-closed: nothing written


def test_record_outcome_duplicate_refused(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    _write_decisions(loop.decision_log, ["job-1"])
    assert loop.record_outcome("job-1", "clean")["status"] == "ok"
    second = loop.record_outcome("job-1", "rolled_back")
    assert second["status"] == "refused"
    assert "outcome_already_recorded" in second["reason"]
    # First outcome preserved — the log is append-only.
    outcomes = [
        json.loads(line)
        for line in loop.outcome_log.read_text(encoding="utf-8").splitlines()
    ]
    assert [o["outcome"] for o in outcomes] == ["clean"]


@pytest.mark.parametrize("outcome", ["clean", "rolled_back", "escalated"])
def test_record_outcome_each_enum_value(tmp_path, outcome):
    loop = _loop(tmp_path, enabled=True)
    _write_decisions(loop.decision_log, ["job-1"])
    result = loop.record_outcome("job-1", outcome)
    assert result["status"] == "ok"
    assert result["outcome"] == outcome


def test_record_outcome_malformed_policy_refused(tmp_path):
    bad = tmp_path / "bad-policy.yaml"
    bad.write_text("version: [unclosed", encoding="utf-8")
    loop = LearnLoop(
        bad,
        _write_bands(tmp_path),
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
    )
    result = loop.record_outcome("job-1", "clean")
    assert result["status"] == "refused"
    assert "policy_config_error" in result["reason"]


# ── self-review aggregates (7) ───────────────────────────────────────


def _mixed_scenario(tmp_path, now=NOW):
    """5 allowed (2 rolled back, 1 escalated, 2 clean) + 3 refused."""
    loop = _loop(tmp_path, enabled=True, now=now)
    allowed_jobs = [f"a-{i}" for i in range(5)]
    refused_jobs = [f"r-{i}" for i in range(3)]
    rows = [
        _decision(j, now - i * DAY, "allowed") for i, j in enumerate(allowed_jobs)
    ] + [
        _decision(j, now - i * DAY, "refused")
        for i, j in enumerate(refused_jobs, start=5)
    ]
    _write_rows(loop.decision_log, rows)
    _write_outcomes(
        loop.outcome_log,
        [
            ("a-0", "rolled_back", now),
            ("a-1", "rolled_back", now),
            ("a-2", "escalated", now),
            ("a-3", "clean", now),
            ("a-4", "clean", now),
        ],
    )
    return loop


def test_review_totals_and_rates(tmp_path):
    report = _mixed_scenario(tmp_path).self_review(NOW)
    assert report.state == "reviewed"
    assert report.auto_merges == 5
    assert report.refusals == 3
    assert report.rollbacks == 2
    assert report.escalations == 1
    assert report.cleans == 2
    assert report.rollback_rate == pytest.approx(0.4)
    assert report.escalation_rate == pytest.approx(0.2)


def test_rows_outside_window_excluded(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    rows = [_decision("old", NOW - 31 * DAY), _decision("new", NOW)]
    _write_rows(loop.decision_log, rows)
    report = loop.self_review(NOW)
    assert report.auto_merges == 1


def test_zero_allowed_rates_zero(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    _write_decisions(loop.decision_log, ["r-1", "r-2"], decision="refused")
    report = loop.self_review(NOW)
    assert report.auto_merges == 0
    assert report.rollback_rate == 0.0
    assert report.escalation_rate == 0.0
    assert report.proposals == ()


def test_coverage_gaps_forbid_loosen(tmp_path):
    loop = _clean_scenario(tmp_path, 25)
    gap = _decision("gap-1", NOW)
    del gap["policy_version"]
    with open(loop.decision_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(gap) + "\n")
    report = loop.self_review(NOW)
    assert report.coverage_gaps == 1
    assert not [p for p in report.proposals if p.loosen]


def test_coverage_gap_noted_in_summary(tmp_path):
    loop = _clean_scenario(tmp_path, 25)
    gap = _decision("gap-1", NOW)
    del gap["ts"]
    with open(loop.decision_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(gap) + "\n")
    report = loop.self_review(NOW)
    assert "loosening forbidden" in report.summary


def test_one_audit_row_per_review(tmp_path):
    loop = _mixed_scenario(tmp_path)
    report = loop.self_review(NOW)
    rows = _read_audit(loop.audit_log)
    assert len(rows) == 1
    row = rows[0]
    assert row["component"] == "learn_loop"
    assert row["action"] == "self_review"
    assert row["auto_merges"] == report.auto_merges
    assert row["rollbacks"] == report.rollbacks
    assert row["proposals"] == len(report.proposals)


def test_refused_decisions_do_not_feed_rates(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    rows = [_decision("a-1", NOW, "allowed")]
    rows += [_decision(f"r-{i}", NOW - i * DAY, "refused") for i in range(10)]
    _write_rows(loop.decision_log, rows)
    _write_outcomes(loop.outcome_log, [("a-1", "rolled_back", NOW)])
    report = loop.self_review(NOW)
    assert report.rollback_rate == pytest.approx(1.0)  # 1/1, not 1/11


# ── tighten proposals (5) ────────────────────────────────────────────


def test_rollback_rate_triggers_tighten(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(100)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [(f"j-{i}", "rolled_back", NOW) for i in range(4)]
        + [(f"j-{i}", "clean", NOW) for i in range(4, 100)],
    )
    report = loop.self_review(NOW)
    tighten = [p for p in report.proposals if p.direction == "tighten"]
    assert len(tighten) == 1
    proposal = tighten[0]
    assert proposal.band_key == "human_above"
    assert proposal.old_value == 0.60
    assert proposal.new_value == 0.65
    assert proposal.requires_michael is False
    assert proposal.evidence["rollback_rate"] == pytest.approx(0.04)


def test_escalation_rate_triggers_tighten(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(100)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [(f"j-{i}", "escalated", NOW) for i in range(40)]
        + [(f"j-{i}", "clean", NOW) for i in range(40, 100)],
    )
    report = loop.self_review(NOW)
    tighten = [p for p in report.proposals if p.direction == "tighten"]
    assert len(tighten) == 1
    assert tighten[0].new_value == 0.65


def test_no_trigger_no_proposals(tmp_path):
    # 10 clean newest, then pending merges: streak too short to loosen,
    # rates too low to tighten.
    loop = _loop(tmp_path, enabled=True)
    clean_jobs = [f"c-{i}" for i in range(10)]
    pending_jobs = [f"p-{i}" for i in range(5)]
    _write_decisions(loop.decision_log, clean_jobs + pending_jobs)
    _write_outcomes(loop.outcome_log, [(j, "clean", NOW - 10) for j in clean_jobs])
    report = loop.self_review(NOW)
    assert report.consecutive_clean == 10
    assert report.proposals == ()


def test_tighten_never_requires_michael(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(100)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [(f"j-{i}", "rolled_back", NOW) for i in range(10)]
        + [(f"j-{i}", "clean", NOW) for i in range(10, 100)],
    )
    report = loop.self_review(NOW)
    assert report.proposals
    assert all(p.requires_michael is False for p in report.proposals)


def test_proposal_carries_spec_text(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(100)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [(f"j-{i}", "rolled_back", NOW) for i in range(4)]
        + [(f"j-{i}", "clean", NOW) for i in range(4, 100)],
    )
    (proposal,) = [
        p for p in loop.self_review(NOW).proposals if p.direction == "tighten"
    ]
    assert "version: auto-bands-v2" in proposal.proposed_spec_text
    assert "human_above: 0.65" in proposal.proposed_spec_text
    assert "auto_below: 0.2" in proposal.proposed_spec_text


# ── loosen rate-limit (8) ────────────────────────────────────────────


def test_loosen_when_all_conditions_met(tmp_path):
    report = _clean_scenario(tmp_path, 25).self_review(NOW)
    loosen = [p for p in report.proposals if p.loosen]
    assert len(loosen) == 1
    proposal = loosen[0]
    assert proposal.band_key == "human_above"
    assert proposal.old_value == 0.60
    assert proposal.new_value == 0.55
    assert proposal.requires_michael is False


def test_loosen_needs_20_consecutive(tmp_path):
    report = _clean_scenario(tmp_path, 19).self_review(NOW)
    assert report.consecutive_clean == 19
    assert not [p for p in report.proposals if p.loosen]


def test_loosen_blocked_by_rollback(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(26)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [("j-0", "rolled_back", NOW)]
        + [(f"j-{i}", "clean", NOW) for i in range(1, 26)],
    )
    report = loop.self_review(NOW)
    assert report.rollback_rate > 0
    assert not [p for p in report.proposals if p.loosen]


def test_loosen_budget_exceeded_needs_michael(tmp_path):
    loop = _clean_scenario(tmp_path, 25)
    # An applied loosening 0.65 -> 0.60 earlier this week: budget spent.
    _write_rows(
        loop.band_change_log,
        [
            {
                "ts": NOW - 1000,
                "band_key": "human_above",
                "band_version": "auto-bands-v1",
                "direction": "loosen",
                "old_value": 0.65,
                "new_value": 0.60,
                "applied_by": "mbgulden",
            }
        ],
    )
    report = loop.self_review(NOW)
    loosen = [p for p in report.proposals if p.loosen]
    assert len(loosen) == 1
    assert loosen[0].requires_michael is True
    assert "rate limit" in loosen[0].reason
    assert "Michael" in loosen[0].reason


def test_loosen_floor_no_proposal(tmp_path):
    loop = _loop(tmp_path, enabled=True, bands_overrides={"human_above": 0.40})
    jobs = [f"j-{i}" for i in range(25)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(loop.outcome_log, [(j, "clean", NOW - 10) for j in jobs])
    report = loop.self_review(NOW)
    assert not [p for p in report.proposals if p.loosen]


def test_manual_loosen_beyond_budget_needs_michael(tmp_path):
    loop = _clean_scenario(tmp_path, 25)
    result = loop.propose_band_change("human_above", 0.50)
    assert result["status"] == "ok"
    proposal = result["proposal"]
    assert proposal.direction == "loosen"
    assert proposal.requires_michael is True
    assert "Michael" in proposal.reason


def test_manual_loosen_insufficient_evidence_refused(tmp_path):
    loop = _clean_scenario(tmp_path, 5)
    result = loop.propose_band_change("human_above", 0.55)
    assert result["status"] == "refused"
    assert "loosen_evidence_insufficient" in result["reason"]


def test_manual_tighten_anytime(tmp_path):
    loop = _clean_scenario(tmp_path, 5)  # thin evidence, no matter
    result = loop.propose_band_change("human_above", 0.80)
    assert result["status"] == "ok"
    proposal = result["proposal"]
    assert proposal.direction == "tighten"
    assert proposal.old_value == 0.60
    assert proposal.new_value == 0.80
    assert proposal.requires_michael is False


# ── consecutive clean (4) ────────────────────────────────────────────


def test_trailing_clean_counted(tmp_path):
    report = _clean_scenario(tmp_path, 3).self_review(NOW)
    assert report.consecutive_clean == 3


def test_rollback_resets_streak(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(6)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [("j-0", "rolled_back", NOW)]  # newest merge rolled back
        + [(f"j-{i}", "clean", NOW) for i in range(1, 6)],
    )
    report = loop.self_review(NOW)
    assert report.consecutive_clean == 0


def test_pending_breaks_streak(tmp_path):
    # 3 clean newest, 1 pending (no outcome row), 5 clean older:
    # silence is pending, not clean — streak stops at 3.
    loop = _loop(tmp_path, enabled=True)
    clean_new = [f"n-{i}" for i in range(3)]
    pending = ["pend-1"]
    clean_old = [f"o-{i}" for i in range(5)]
    _write_decisions(loop.decision_log, clean_new + pending + clean_old)
    _write_outcomes(
        loop.outcome_log,
        [(j, "clean", NOW - 10) for j in clean_new + clean_old],
    )
    report = loop.self_review(NOW)
    assert report.consecutive_clean == 3


def test_escalated_breaks_streak(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(4)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [("j-0", "escalated", NOW)]  # newest escalated
        + [(f"j-{i}", "clean", NOW) for i in range(1, 4)],
    )
    report = loop.self_review(NOW)
    assert report.consecutive_clean == 0


# ── report text (3) ──────────────────────────────────────────────────


def test_summary_names_totals_and_proposal(tmp_path):
    loop = _loop(tmp_path, enabled=True)
    jobs = [f"j-{i}" for i in range(100)]
    _write_decisions(loop.decision_log, jobs, step=3600)
    _write_outcomes(
        loop.outcome_log,
        [(f"j-{i}", "rolled_back", NOW) for i in range(4)]
        + [(f"j-{i}", "clean", NOW) for i in range(4, 100)],
    )
    summary = loop.self_review(NOW).summary
    assert "100 auto-merges" in summary
    assert "4 rollbacks" in summary
    assert "proposal: tighten human_above 0.60 → 0.65" in summary


def test_summary_no_proposals(tmp_path):
    loop = _clean_scenario(tmp_path, 10)
    summary = loop.self_review(NOW).summary
    assert "no band changes proposed" in summary


def test_summary_disabled(tmp_path):
    assert _loop(tmp_path, enabled=False).self_review(NOW).summary == (
        "learn loop disabled"
    )


# ── unknown band key / limits (2) ────────────────────────────────────


def test_unknown_band_key_refused(tmp_path):
    loop = _clean_scenario(tmp_path, 25)
    result = loop.propose_band_change("nope", 0.5)
    assert result["status"] == "refused"
    assert "unknown_band_key" in result["reason"]
    rows = _read_audit(loop.audit_log)
    assert len(rows) == 1  # a refusal row, not a proposal row
    assert rows[0]["action"] == "propose_band_change"
    assert rows[0]["status"] == "refused"


def test_band_limit_exceeded_refused(tmp_path):
    loop = _clean_scenario(tmp_path, 25)
    result = loop.propose_band_change("human_above", 0.95)
    assert result["status"] == "refused"
    assert "band_limit_exceeded" in result["reason"]
