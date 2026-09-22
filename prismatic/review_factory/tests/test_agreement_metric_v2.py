"""Tests for agreement metric v2 (agreement-metric-v2-spec.md).

Covers §9 of the spec:
- unit: every taxonomy branch, the weight table, decay math
- property: decay monotonicity, D3 exclusion
- scenario: the §4.5 bad-week simulation
- golden: the backfill rule (legacy records keep weight 1.0)
- gate: shadow_exit_met_v2 (effective-n floor, bad-merge zero tolerance)
- regression: v1 functions behave exactly as before
- plumbing: the agreement_metric policy flag, the learn_loop correct CLI
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory import learn_loop as learn_loop_mod
from prismatic.review_factory import phase_advancement as pa_mod
from prismatic.review_factory.phase_advancement import (
    APPROVAL_SCHEMA,
    PhaseAdvancement,
    PhaseAdvancementError,
    PhasePolicy,
    check_phase0_exit,
    discover_active_policy,
    evaluate_exit_criteria,
    expected_target,
    render_policy_yaml,
)
from prismatic.review_factory.shadow_agreement import (
    AGREEMENT_D1_WEIGHT,
    AGREEMENT_D2_WEIGHT,
    AGREEMENT_D3_WEIGHT,
    AGREEMENT_HALF_LIFE_DAYS,
    AGREEMENT_PENDING_WEIGHT,
    CLASS_JEV_WAS_RIGHT,
    CLASS_PENDING,
    CLASS_TOO_AGGRESSIVE,
    CLASS_TOO_CAUTIOUS,
    agreement_rate,
    apply_corrections,
    attach_outcomes,
    classify_disagreement,
    decayed_agreement_rate,
    record_weight,
    shadow_exit_met,
    shadow_exit_met_v2,
)

NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


def _rec(system_call, actual_outcome, *, pr=1, days_ago=0, outcome=None, **kw):
    r = {
        "pr_number": pr,
        "system_call": system_call,
        "actual_outcome": actual_outcome,
    }
    if days_ago is not None:
        r["decided_at"] = (NOW - timedelta(days=days_ago)).isoformat()
    if outcome is not None:
        r["outcome"] = outcome
    r.update(kw)
    return r


def _agree(pr, days_ago=0):
    return _rec("merge", "merged", pr=pr, days_ago=days_ago)


def _complete_signal(pr):
    return {
        "agent": "prismatic-shadow-observer",
        "event_type": "decision",
        "id": f"shadow-{pr}",
        "message": f"shadow call for PR #{pr}: MERGE",
        "metadata": {
            "pr_number": pr,
            "call": "merge",
            "policy_version": "shadow-v2",
        },
        "severity": "info",
        "source": "prismatic-engine",
        "status": "completed",
        "timestamp": NOW.isoformat(),
    }


# ── taxonomy (§4.1) ──────────────────────────────────────────────────


def test_classify_d1_too_aggressive():
    assert (
        classify_disagreement(_rec("merge", "closed_unmerged")) == CLASS_TOO_AGGRESSIVE
    )


def test_classify_d2_too_cautious():
    assert (
        classify_disagreement(_rec("skip", "merged", outcome="clean"))
        == CLASS_TOO_CAUTIOUS
    )


def test_classify_d3_on_rollback():
    assert (
        classify_disagreement(_rec("skip", "merged", outcome="rolled_back"))
        == CLASS_JEV_WAS_RIGHT
    )


def test_classify_d3_on_human_correction():
    assert (
        classify_disagreement(_rec("skip", "merged", corrected_by="jev-was-right"))
        == CLASS_JEV_WAS_RIGHT
    )


def test_classify_pending_awaiting_outcome():
    assert classify_disagreement(_rec("skip", "merged", days_ago=2)) == CLASS_PENDING


def test_classify_agreement_returns_none():
    assert classify_disagreement(_rec("merge", "merged")) is None
    assert classify_disagreement(_rec("skip", "closed_unmerged")) is None


def test_classify_open_returns_none():
    assert classify_disagreement(_rec("merge", "open")) is None


def test_classify_d1_never_reclassified():
    # D1 has no outcome to observe; a correction cannot apply to it.
    assert (
        classify_disagreement(
            _rec("merge", "closed_unmerged", corrected_by="jev-was-right")
        )
        == CLASS_TOO_AGGRESSIVE
    )


# ── weight table + decay (§4.3) ──────────────────────────────────────


def test_weight_table_values_fresh():
    # days_ago=0: no decay, so the raw class weights show.
    assert record_weight(_rec("merge", "closed_unmerged"), as_of=NOW) == (
        AGREEMENT_D1_WEIGHT
    )
    assert (
        record_weight(_rec("skip", "merged", outcome="clean"), as_of=NOW)
        == AGREEMENT_D2_WEIGHT
    )
    assert (
        record_weight(_rec("skip", "merged", outcome="rolled_back"), as_of=NOW)
        == AGREEMENT_D3_WEIGHT
    )
    assert record_weight(_rec("merge", "merged"), as_of=NOW) == 1.0
    assert record_weight(_rec("merge", "open"), as_of=NOW) == 0.0


def test_weight_pending_in_window():
    # decided 2 days ago, no outcome yet: pending weight 0.5, barely decayed.
    w = record_weight(_rec("skip", "merged", days_ago=2), as_of=NOW)
    assert w == pytest.approx(AGREEMENT_PENDING_WEIGHT * 2 ** (-2 / 14))


def test_decay_half_life_boundary():
    # At exactly one half-life the weight is exactly half.
    w = record_weight(_agree(1, days_ago=AGREEMENT_HALF_LIFE_DAYS), as_of=NOW)
    assert w == 0.5


def test_decay_monotonicity():
    weights = [record_weight(_agree(1, days_ago=d), as_of=NOW) for d in (0, 14, 28, 60)]
    assert weights == sorted(weights, reverse=True)
    assert len(set(weights)) == 4


def test_d3_exclusion_never_lowers_rate():
    base = [_agree(i, days_ago=1) for i in range(9)] + [
        _rec("skip", "merged", pr=100, days_ago=1, outcome="clean")
    ]
    d3s = [
        _rec("skip", "merged", pr=200 + i, days_ago=1, outcome="rolled_back")
        for i in range(3)
    ]
    with_d3 = decayed_agreement_rate(base + d3s, as_of=NOW)["decayed_rate"]
    without_d3 = decayed_agreement_rate(base, as_of=NOW)["decayed_rate"]
    assert with_d3 == pytest.approx(without_d3)


def test_rate_result_keys():
    stats = decayed_agreement_rate([_agree(1)], as_of=NOW)
    for key in (
        "n_decided",
        "n_open",
        "effective_n",
        "agreed_weight",
        "decayed_rate",
        "n_d1",
        "n_d2",
        "n_d3",
        "n_pending",
        "n_legacy",
        "trend_7d",
    ):
        assert key in stats


def test_trend_7d_shows_recovery_direction():
    # 10 old agreements, then a bad week of 5 fresh D2s: the 7-day trend
    # (before the bad week) is higher than the current rate.
    records = [_agree(i, days_ago=20) for i in range(10)] + [
        _rec("skip", "merged", pr=100 + i, days_ago=1, outcome="clean")
        for i in range(5)
    ]
    stats = decayed_agreement_rate(records, as_of=NOW)
    assert stats["trend_7d"] == pytest.approx(1.0)
    assert stats["decayed_rate"] < stats["trend_7d"]


# ── §4.5 bad-week simulation ─────────────────────────────────────────


def _bad_week_records():
    # Spec §4.5: 28 agreements, then a bad week adds 5 D2 disagreements.
    return [_agree(i) for i in range(28)] + [
        _rec("skip", "merged", pr=100 + i, outcome="clean") for i in range(5)
    ]


def test_bad_week_v1_vs_v2():
    records = _bad_week_records()
    v1 = shadow_exit_met(records)
    assert v1["rate"] == pytest.approx(28 / 33)
    assert v1["checks"]["min_agreement"] is False

    v2 = shadow_exit_met_v2(records, as_of=NOW)
    # 5 D2s contribute 5 * 0.25 = 1.25 effective disagreements.
    assert v2["decayed_rate"] == pytest.approx(28 / 29.25)
    assert v2["checks"]["min_agreement_v2"] is True
    assert v2["checks"]["min_prs"] is True
    assert v2["checks"]["min_effective_n"] is True
    assert v2["exit_met"] is True


def test_bad_week_dent_fades_with_new_evidence():
    records = _bad_week_records()
    rate_now = shadow_exit_met_v2(records, as_of=NOW)["decayed_rate"]

    # Two weeks pass; 10 fresh agreements arrive; the old dent halved.
    later = NOW + timedelta(days=14)
    fresh = [_agree(1000 + i, days_ago=0) for i in range(10)]
    aged = [
        {**r, "decided_at": (NOW - timedelta(days=14)).isoformat()}
        if r.get("decided_at")
        else r
        for r in records
    ]
    rate_later = shadow_exit_met_v2(aged + fresh, as_of=later)["decayed_rate"]
    assert rate_later > rate_now  # climbing back, not stuck

    # v1 is still grinding: 38/43.
    v1_later = shadow_exit_met(aged + fresh)["rate"]
    assert v1_later == pytest.approx(38 / 43)
    assert v1_later < rate_later


def test_bad_week_dent_weight_halves_per_half_life():
    rec = _rec("skip", "merged", outcome="clean", days_ago=0)
    w0 = record_weight(rec, as_of=NOW)
    w1 = record_weight(rec, as_of=NOW + timedelta(days=14))
    assert w1 == pytest.approx(w0 / 2)


# ── backfill golden rule ─────────────────────────────────────────────


def test_backfill_legacy_keeps_weight_one_then_decays():
    # Pre-feed record: no outcome, window long passed -> legacy.
    rec = _rec("skip", "merged", days_ago=60)
    assert classify_disagreement(rec) == CLASS_PENDING
    assert record_weight(rec, as_of=NOW) == pytest.approx(2 ** (-60 / 14))
    stats = decayed_agreement_rate([rec], as_of=NOW)
    assert stats["n_legacy"] == 1


def test_backfill_no_decided_at_is_fully_conservative():
    rec = {"pr_number": 7, "system_call": "skip", "actual_outcome": "merged"}
    assert record_weight(rec, as_of=NOW) == 1.0


# ── the v2 gate ──────────────────────────────────────────────────────


def test_v2_gate_effective_n_floor_blocks_tiny_samples():
    # 30 ancient agreements: raw count passes, decayed sample does not.
    records = [_agree(i, days_ago=120) for i in range(30)]
    gate = shadow_exit_met_v2(records, as_of=NOW)
    assert gate["checks"]["min_prs"] is True
    assert gate["checks"]["min_agreement_v2"] is True
    assert gate["checks"]["min_effective_n"] is False
    assert gate["exit_met"] is False


def test_v2_gate_bad_merges_still_zero_tolerance():
    records = [_agree(i) for i in range(35)]
    gate = shadow_exit_met_v2(records, bad_merge_calls=1, as_of=NOW)
    assert gate["checks"]["zero_bad_merges"] is False
    assert gate["exit_met"] is False


def test_v2_gate_passes_on_clean_evidence():
    records = [_agree(i) for i in range(34)] + [
        _rec("skip", "merged", pr=100, outcome="clean")
    ]
    gate = shadow_exit_met_v2(records, as_of=NOW)
    assert gate["exit_met"] is True
    assert gate["metric"] == "v2"


def test_v2_gate_empty_records():
    gate = shadow_exit_met_v2([], as_of=NOW)
    assert gate["decayed_rate"] is None
    assert gate["exit_met"] is False


# ── v1 regression: untouched behavior ───────────────────────────────


def test_v1_functions_unchanged():
    records = [
        {"system_call": "merge", "actual_outcome": "merged"},
        {"system_call": "skip", "actual_outcome": "closed_unmerged"},
        {"system_call": "merge", "actual_outcome": "closed_unmerged"},
        {"system_call": "merge", "actual_outcome": "open"},
    ]
    stats = agreement_rate(records)
    assert stats == {
        "n_decided": 3,
        "n_agreed": 2,
        "n_pending": 1,
        "rate": pytest.approx(2 / 3),
    }
    gate = shadow_exit_met(records)
    assert gate["checks"] == {
        "min_prs": False,
        "min_agreement": False,
        "zero_bad_merges": True,
    }
    assert gate["exit_met"] is False


# ── outcome join + corrections ───────────────────────────────────────


def test_attach_outcomes_joins_by_pr():
    records = [_rec("skip", "merged", pr=1), _rec("skip", "merged", pr=2)]
    joined = attach_outcomes(records, {1: "clean", 2: "rolled_back", 9: "clean"})
    assert classify_disagreement(joined[0]) == CLASS_TOO_CAUTIOUS
    assert classify_disagreement(joined[1]) == CLASS_JEV_WAS_RIGHT
    # originals untouched, invalid values ignored
    assert "outcome" not in records[0]
    joined2 = attach_outcomes([_rec("skip", "merged", pr=3)], {3: "bogus"})
    assert classify_disagreement(joined2[0]) == CLASS_PENDING


def test_apply_corrections_folds_jev_was_right():
    rec = _rec("skip", "merged", pr=5, days_ago=2)
    assert classify_disagreement(rec) == CLASS_PENDING
    corrections = [
        {
            "pr_number": 5,
            "verdict": "jev-was-right",
            "corrected_by": "mbgulden",
            "corrected_at": NOW.isoformat(),
        },
        {"pr_number": 6, "verdict": "some-future-verdict"},
    ]
    folded = apply_corrections([rec], corrections)
    assert classify_disagreement(folded[0]) == CLASS_JEV_WAS_RIGHT
    assert record_weight(folded[0], as_of=NOW) == 0.0
    assert "corrected_by" not in rec  # input never mutated


# ── policy flag plumbing ─────────────────────────────────────────────


def test_policy_flag_defaults_to_v1():
    policy = PhasePolicy.from_dict(
        {
            "version": "phase-v1",
            "phase": 0,
            "advancements_enabled": False,
            "approver": "mbgulden",
        }
    )
    assert policy.agreement_metric == "v1"


def test_policy_flag_accepts_v2_and_rejects_bogus():
    data = {
        "version": "phase-v2",
        "phase": 0,
        "advancements_enabled": False,
        "approver": "mbgulden",
        "agreement_metric": "v2",
    }
    assert PhasePolicy.from_dict(data).agreement_metric == "v2"
    with pytest.raises(PhaseAdvancementError):
        PhasePolicy.from_dict({**data, "agreement_metric": "v3"})


def test_render_policy_yaml_roundtrips_flag():
    policy = PhasePolicy(
        version="phase-v2",
        phase=0,
        advancements_enabled=False,
        approver="mbgulden",
        agreement_metric="v2",
    )
    restored = PhasePolicy.from_dict(yaml.safe_load(render_policy_yaml(policy)))
    assert restored.agreement_metric == "v2"


def test_discover_picks_highest_versioned_file(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "phase_policy_v1.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "phase-v1",
                "phase": 0,
                "advancements_enabled": False,
                "approver": "mbgulden",
            }
        ),
        encoding="utf-8",
    )
    (spec_dir / "phase_policy_v2.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "phase-v2",
                "phase": 0,
                "advancements_enabled": False,
                "approver": "mbgulden",
                "agreement_metric": "v2",
            }
        ),
        encoding="utf-8",
    )
    policy = discover_active_policy(spec_dir)
    assert policy.version == "phase-v2"
    assert policy.agreement_metric == "v2"


def _v2_evidence():
    # 33 fresh records: v1 fails (28/33 = 84.8%), v2 passes (28/29.25).
    records = [_agree(i) for i in range(28)] + [
        _rec("skip", "merged", pr=100 + i, outcome="clean") for i in range(5)
    ]
    return {
        "shadow_records": records,
        "bad_merge_calls": 0,
        "shadow_signals": [_complete_signal(i) for i in range(33)],
        "watchdog": {"enabled": True, "mode": "monitor-only"},
    }


def test_check_phase0_exit_v2_uses_v2_gate():
    result = check_phase0_exit(_v2_evidence(), agreement_metric="v2")
    assert result.met is True
    assert result.checks["min_agreement"] is True
    assert result.checks["min_effective_sample"] is True
    assert "agreement_metric=v2" in result.detail


def test_check_phase0_exit_defaults_to_v1():
    result = check_phase0_exit(_v2_evidence())
    assert result.met is False
    assert result.checks["min_agreement"] is False
    assert "min_effective_sample" not in result.checks


def test_check_phase0_exit_unknown_metric_fails_closed():
    result = check_phase0_exit(_v2_evidence(), agreement_metric="v3")
    assert result.met is False
    assert result.checks == {"known_agreement_metric": False}


def test_evaluate_threads_evidence_flag():
    assert evaluate_exit_criteria(0, 1, _v2_evidence()).met is False
    flagged = {**_v2_evidence(), "agreement_metric": "v2"}
    assert evaluate_exit_criteria(0, 1, flagged).met is True


def test_main_heartbeat_stamps_active_policy_flag(tmp_path, capsys):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "phase_policy_v1.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "phase-v1",
                "phase": 0,
                "advancements_enabled": False,
                "approver": "mbgulden",
                "agreement_metric": "v2",
            }
        ),
        encoding="utf-8",
    )
    rc = pa_mod.main(
        [
            "--spec-dir",
            str(spec_dir),
            "--log",
            str(tmp_path / "adv-log.jsonl"),
            "--audit-sink",
            str(tmp_path / "adv-audit.jsonl"),
        ]
    )
    assert rc == 0
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "no-request"
    # The v2 gate ran: its extra check key is present.
    assert "min_effective_sample" in state["checks"]


def _write_v2_policy(spec_dir: Path, **overrides):
    data = {
        "version": "phase-v1",
        "phase": 0,
        "advancements_enabled": True,
        "approver": "mbgulden",
        "agreement_metric": "v2",
        "chunks": {},
    }
    data.update(overrides)
    (spec_dir / "phase_policy_v1.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8"
    )


def _approval(request_hash: str) -> dict:
    return {
        "schema": APPROVAL_SCHEMA,
        "phase": 1,
        "target": expected_target(1),
        "approver": "mbgulden",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rationale": "v2 metric verified against backfill",
        "request_hash": request_hash,
    }


def _request_hash(adv: PhaseAdvancement, request) -> str:
    for row in adv.log.read_rows():
        if (
            row.get("type") == "request"
            and row["payload"].get("request_id") == request.request_id
        ):
            return row["hash"]
    raise AssertionError("request row not found")


def test_execute_roundtrips_agreement_metric(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(parents=True)
    _write_v2_policy(spec_dir)
    adv = PhaseAdvancement(
        spec_dir=spec_dir,
        log_path=tmp_path / "adv-log.jsonl",
        audit_sink=tmp_path / "adv-audit.jsonl",
    )
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval(_request_hash(adv, request))
    result = adv.execute(request.request_id, approval, _v2_evidence())
    assert result.executed is True
    written = yaml.safe_load(result.policy_file.read_text(encoding="utf-8"))
    assert written["agreement_metric"] == "v2"
    assert written["phase"] == 1


# ── learn_loop correct CLI ───────────────────────────────────────────


def _write_shadow_records(path: Path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_correct_records_d2(tmp_path, capsys):
    shadow = tmp_path / "shadow-records.jsonl"
    _write_shadow_records(
        shadow, [_rec("skip", "merged", pr=512, days_ago=2, outcome="clean")]
    )
    corrections = tmp_path / "corrections.jsonl"
    rc = learn_loop_mod.main(
        [
            "correct",
            "--pr",
            "512",
            "--verdict",
            "jev-was-right",
            "--note",
            "my mistake, Jev flagged the risk",
            "--shadow-records",
            str(shadow),
            "--corrections-log",
            str(corrections),
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "recorded"
    assert out["previous_class"] == "D2"
    rows = [
        json.loads(line)
        for line in corrections.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["pr_number"] == 512
    assert rows[0]["verdict"] == "jev-was-right"
    assert rows[0]["schema"] == "agreement-correction/v1"


def test_correct_end_to_end_reclassifies(tmp_path, capsys):
    shadow = tmp_path / "shadow-records.jsonl"
    _write_shadow_records(shadow, [_rec("skip", "merged", pr=77, days_ago=2)])
    corrections = tmp_path / "corrections.jsonl"
    assert (
        learn_loop_mod.main(
            [
                "correct",
                "--pr",
                "77",
                "--verdict",
                "jev-was-right",
                "--shadow-records",
                str(shadow),
                "--corrections-log",
                str(corrections),
            ]
        )
        == 0
    )
    capsys.readouterr()
    rows = [
        json.loads(line)
        for line in corrections.read_text(encoding="utf-8").splitlines()
    ]
    folded = apply_corrections(_read_shadow_records(shadow), rows)
    assert classify_disagreement(folded[0]) == CLASS_JEV_WAS_RIGHT


def _read_shadow_records(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_correct_refuses_unknown_pr(tmp_path, capsys):
    shadow = tmp_path / "shadow-records.jsonl"
    _write_shadow_records(shadow, [_agree(1)])
    rc = learn_loop_mod.main(
        [
            "correct",
            "--pr",
            "999",
            "--verdict",
            "jev-was-right",
            "--shadow-records",
            str(shadow),
            "--corrections-log",
            str(tmp_path / "c.jsonl"),
        ]
    )
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_correct_refuses_agreement(tmp_path, capsys):
    shadow = tmp_path / "shadow-records.jsonl"
    _write_shadow_records(shadow, [_agree(512)])
    rc = learn_loop_mod.main(
        [
            "correct",
            "--pr",
            "512",
            "--verdict",
            "jev-was-right",
            "--shadow-records",
            str(shadow),
            "--corrections-log",
            str(tmp_path / "c.jsonl"),
        ]
    )
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_correct_refuses_d1(tmp_path, capsys):
    shadow = tmp_path / "shadow-records.jsonl"
    _write_shadow_records(shadow, [_rec("merge", "closed_unmerged", pr=512)])
    rc = learn_loop_mod.main(
        [
            "correct",
            "--pr",
            "512",
            "--verdict",
            "jev-was-right",
            "--shadow-records",
            str(shadow),
            "--corrections-log",
            str(tmp_path / "c.jsonl"),
        ]
    )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "refused"
    assert "D1" in out["reason"]


def test_correct_refuses_missing_records_file(tmp_path, capsys):
    rc = learn_loop_mod.main(
        [
            "correct",
            "--pr",
            "1",
            "--verdict",
            "jev-was-right",
            "--shadow-records",
            str(tmp_path / "nope.jsonl"),
            "--corrections-log",
            str(tmp_path / "c.jsonl"),
        ]
    )
    assert rc == 1


def test_correct_already_recorded_is_idempotent(tmp_path, capsys):
    shadow = tmp_path / "shadow-records.jsonl"
    _write_shadow_records(
        shadow, [_rec("skip", "merged", pr=512, outcome="rolled_back")]
    )
    corrections = tmp_path / "c.jsonl"
    args = [
        "correct",
        "--pr",
        "512",
        "--verdict",
        "jev-was-right",
        "--shadow-records",
        str(shadow),
        "--corrections-log",
        str(corrections),
    ]
    assert learn_loop_mod.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "already_recorded"
    assert not corrections.exists()  # nothing appended


def test_correct_missing_args_exits_2():
    with pytest.raises(SystemExit) as exc:
        learn_loop_mod.main(["correct"])
    assert exc.value.code == 2


def test_self_review_cli_path_unchanged(tmp_path, capsys):
    # The subcommand addition did not alter the default path: a malformed
    # policy goes through LearnLoop's existing fail-closed disabled path
    # (exit 0, "misconfigured" summary) exactly as before.
    bad = tmp_path / "bad-policy.yaml"
    bad.write_text("{not: [valid: yaml", encoding="utf-8")
    rc = learn_loop_mod.main(["--policy", str(bad)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"] == "learn loop misconfigured; review refused"
