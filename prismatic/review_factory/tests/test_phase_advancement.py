"""Tests for the phase-advancement machinery (chunk 8).

Every test asserts a safety property the machinery claims:
- no transition without Michael's approval artifact on record;
- an approval never waives the mechanical exit criteria;
- refusals are fail-closed, logged, and audited;
- advancement is the only writer of new phases; revert is by config;
- the append-only log's hash chain detects tampering.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory.phase_advancement import (
    APPROVAL_SCHEMA,
    AdvancementLog,
    AdvancementRequest,
    PhaseAdvancement,
    PhaseAdvancementError,
    PhasePolicy,
    check_phase0_exit,
    check_phase1_exit,
    check_phase2_exit,
    discover_active_policy,
    evaluate_exit_criteria,
    expected_target,
    load_evidence,
    validate_approval,
)

HERE = Path(__file__).resolve()


# ── helpers ──────────────────────────────────────────────────────────


def _write_policy(spec_dir: Path, phase=0, enabled=True, approver="mbgulden"):
    spec_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "phase-v1",
        "phase": phase,
        "advancements_enabled": enabled,
        "approver": approver,
        "chunks": {"shadow_observer": "enabled_observe_only"},
    }
    (spec_dir / "phase_policy_v1.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def _adv(tmp_path: Path, **policy_overrides) -> PhaseAdvancement:
    spec_dir = tmp_path / "spec"
    _write_policy(spec_dir, **policy_overrides)
    return PhaseAdvancement(
        spec_dir=spec_dir,
        log_path=tmp_path / "adv-log.jsonl",
        audit_sink=tmp_path / "adv-audit.jsonl",
    )


def _request_hash(adv: PhaseAdvancement, request: AdvancementRequest) -> str:
    for row in adv.log.read_rows():
        if (
            row.get("type") == "request"
            and row["payload"].get("request_id") == request.request_id
        ):
            return row["hash"]
    raise AssertionError("request row not found in log")


def _approval(request_hash: str, **overrides) -> dict:
    data = {
        "schema": APPROVAL_SCHEMA,
        "phase": 1,
        "target": expected_target(1),
        "approver": "mbgulden",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rationale": "Phase 0 exit criteria verified in the nightly report.",
        "request_hash": request_hash,
    }
    data.update(overrides)
    return data


def _shadow_signal(pr_number: int, call: str = "merge") -> dict:
    return {
        "agent": "prismatic-shadow-observer",
        "event_type": "decision",
        "id": f"shadow-{pr_number}",
        "issue_id": "",
        "log_path": "",
        "message": f"shadow call for PR #{pr_number}: {call.upper()}",
        "metadata": {
            "pr_number": pr_number,
            "call": call,
            "policy_version": "shadow-v2",
        },
        "run_id": "",
        "severity": "info",
        "source": "prismatic-engine",
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transcript": "",
    }


def _phase0_evidence_met(n: int = 30, agreed: int = 30) -> dict:
    records = []
    for i in range(n):
        if i < agreed:
            # agreement: system says merge exactly when Michael merged
            records.append({"system_call": "merge", "actual_outcome": "merged"})
        else:
            # disagreement: system called merge, Michael closed unmerged
            records.append(
                {"system_call": "merge", "actual_outcome": "closed_unmerged"}
            )
    return {
        "shadow_records": records,
        "bad_merge_calls": 0,
        "shadow_signals": [_shadow_signal(i) for i in range(n)],
        "watchdog": {"enabled": True, "mode": "monitor-only"},
    }


def _merge_row(ts: datetime, outcome: str = "clean") -> dict:
    return {
        "decision": "allowed",
        "outcome": outcome,
        "timestamp": ts.isoformat(),
        "pr_number": 1000,
    }


def _audit_events(adv: PhaseAdvancement) -> list[str]:
    events = []
    if adv.audit_sink.exists():
        for line in adv.audit_sink.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line)["event_type"])
    return events


# ── master switch / default-off ──────────────────────────────────────


def test_shipped_policy_is_default_off():
    """The policy file shipped with this PR must not enable advancement."""
    repo_spec = HERE.parent.parent / "spec"
    policy = discover_active_policy(repo_spec)
    assert policy.phase == 0
    assert policy.advancements_enabled is False
    assert policy.approver == "mbgulden"


def test_execute_refused_when_master_switch_off(tmp_path):
    """Even a perfect approval + met evidence cannot advance while the
    master switch is off."""
    adv = _adv(tmp_path, enabled=False)
    request = adv.request(1, {"shadow_records": []}, requester="muse-test")
    approval = _approval(_request_hash(adv, request))
    result = adv.execute(request.request_id, approval, _phase0_evidence_met())
    assert result.executed is False
    assert any("advancements_enabled" in r for r in result.reasons)
    assert adv.current_phase() == 0
    assert "phase_advancement_execution_refused" in _audit_events(adv)


# ── the hard rule: approval required ─────────────────────────────────


def test_execute_with_no_approval_refused(tmp_path):
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    result = adv.execute(request.request_id, {}, _phase0_evidence_met())
    assert result.executed is False
    assert any("approval invalid" in r for r in result.reasons)
    assert adv.current_phase() == 0
    assert "phase_advancement_approval_denied" in _audit_events(adv)
    assert "phase_advancement_execution_refused" in _audit_events(adv)


def test_evidence_met_but_approval_missing_refused(tmp_path):
    """Evidence alone is never enough."""
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    result = adv.execute(request.request_id, {"schema": "nope"}, _phase0_evidence_met())
    assert result.executed is False
    assert adv.current_phase() == 0


def test_routine_signal_is_not_an_approval(tmp_path):
    """A routine audit signal pasted in as an approval is refused: the
    approval schema is distinct and must match exactly."""
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    routine_signal = _shadow_signal(482)  # a real decision-signal shape
    result = adv.execute(request.request_id, routine_signal, _phase0_evidence_met())
    assert result.executed is False
    assert any("schema" in r for r in result.reasons)


def test_approval_wrong_phase_refused(tmp_path):
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval(
        _request_hash(adv, request), phase=2, target=expected_target(2)
    )
    result = adv.execute(request.request_id, approval, _phase0_evidence_met())
    assert result.executed is False
    assert any("does not match requested" in r for r in result.reasons)


def test_approval_wrong_approver_refused(tmp_path):
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval(_request_hash(adv, request), approver="root")
    result = adv.execute(request.request_id, approval, _phase0_evidence_met())
    assert result.executed is False
    assert any("not the authorized approver" in r for r in result.reasons)


def test_approval_wrong_request_hash_refused(tmp_path):
    """An approval bound to a different request/evidence bundle is refused —
    approvals cannot be recycled."""
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval("deadbeef" * 8)
    result = adv.execute(request.request_id, approval, _phase0_evidence_met())
    assert result.executed is False
    assert any("request_hash" in r for r in result.reasons)


def test_approval_empty_rationale_refused(tmp_path):
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval(_request_hash(adv, request), rationale="   ")
    result = adv.execute(request.request_id, approval, _phase0_evidence_met())
    assert result.executed is False
    assert any("rationale" in r for r in result.reasons)


def test_execute_unknown_request_id_refused(tmp_path):
    adv = _adv(tmp_path)
    approval = _approval("00" * 32)
    result = adv.execute("adv-nonexistent", approval, _phase0_evidence_met())
    assert result.executed is False
    assert any("no filed request" in r for r in result.reasons)


# ── approval never waives evidence ────────────────────────────────────


def test_valid_approval_but_evidence_unmet_refused(tmp_path):
    """A valid approval with unmet exit criteria refuses: approval does not
    waive evidence."""
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval(_request_hash(adv, request))
    weak = _phase0_evidence_met(n=10)  # only 10 shadow PRs: min_prs fails
    result = adv.execute(request.request_id, approval, weak)
    assert result.executed is False
    assert any("exit criteria not met" in r for r in result.reasons)
    assert any("min_prs_resolved" in r for r in result.reasons)
    assert adv.current_phase() == 0


def test_no_skipping_rungs(tmp_path):
    adv = _adv(tmp_path)
    with pytest.raises(PhaseAdvancementError):
        adv.request(2, {}, requester="muse-test")
    with pytest.raises(PhaseAdvancementError):
        adv.request(0, {}, requester="muse-test")


# ── the success path ──────────────────────────────────────────────────


def test_valid_approval_and_met_criteria_executes(tmp_path):
    adv = _adv(tmp_path)
    request = adv.request(1, {}, requester="muse-test")
    approval = _approval(_request_hash(adv, request))
    result = adv.execute(request.request_id, approval, _phase0_evidence_met())

    assert result.executed is True
    assert result.new_phase == 1
    assert result.policy_file is not None
    assert result.policy_file.name == "phase_policy_v2.yaml"
    assert result.policy_file.exists()
    assert adv.current_phase() == 1

    # The new file is a NEW version; v1 is untouched (never edit in place).
    v1 = yaml.safe_load(
        (tmp_path / "spec" / "phase_policy_v1.yaml").read_text(encoding="utf-8")
    )
    assert v1["phase"] == 0
    v2 = yaml.safe_load(result.policy_file.read_text(encoding="utf-8"))
    assert v2["phase"] == 1
    assert v2["approver"] == "mbgulden"

    # Logged and audited.
    types = [r["type"] for r in adv.log.read_rows()]
    assert types == ["request", "execution"]
    ok, detail = adv.log.verify()
    assert ok, detail
    events = _audit_events(adv)
    assert "phase_advancement_approval_accepted" in events
    assert "phase_advancement_execution_succeeded" in events


def test_evidence_loaded_from_real_files(tmp_path):
    """load_evidence resolves file pointers; the pure check runs on the
    parsed result. End-to-end through the JSONL audit logs."""
    shadow_records = tmp_path / "records.jsonl"
    signals = tmp_path / "signals.jsonl"
    ev = _phase0_evidence_met()
    shadow_records.write_text(
        "\n".join(json.dumps(r) for r in ev["shadow_records"]),
        encoding="utf-8",
    )
    signals.write_text(
        "\n".join(json.dumps(s) for s in ev["shadow_signals"]),
        encoding="utf-8",
    )
    watchdog_policy = tmp_path / "watchdog.yaml"
    watchdog_policy.write_text("enabled: true\nmode: monitor-only\n", encoding="utf-8")
    loaded = load_evidence(
        {
            "shadow_records": str(shadow_records),
            "shadow_signals": str(signals),
            "watchdog_policy": str(watchdog_policy),
            "bad_merge_calls": 0,
        }
    )
    result = check_phase0_exit(loaded)
    assert result.met is True, result.failed_checks()


# ── revert by config, no deploy ───────────────────────────────────────


def test_revert_by_config_needs_no_code(tmp_path):
    """A newer versioned file with a lower phase reverts the ladder by
    config change alone."""
    adv = _adv(tmp_path)
    assert adv.current_phase() == 0
    # Simulate a config revert: a NEWER versioned file with a LOWER phase.
    (tmp_path / "spec" / "phase_policy_v3.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "phase-v3",
                "phase": 0,
                "advancements_enabled": False,
                "approver": "mbgulden",
                "chunks": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert adv.current_phase() == 0
    row = adv.record_revert(0, reason="watchdog trip during Phase 1", actor="mbgulden")
    assert row["type"] == "revert"
    assert "phase_advancement_reverted" in _audit_events(adv)
    ok, _ = adv.log.verify()
    assert ok


# ── hash-chained log ──────────────────────────────────────────────────


def test_log_hash_chain_detects_tampering(tmp_path):
    log = AdvancementLog(tmp_path / "log.jsonl")
    log.append("request", {"a": 1})
    log.append("execution", {"b": 2})
    ok, _ = log.verify()
    assert ok is True

    # Tamper with the first row's payload in place.
    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["payload"]["a"] = 999
    lines[0] = json.dumps(row)
    (tmp_path / "log.jsonl").write_text("\n".join(lines), encoding="utf-8")

    ok, detail = log.verify()
    assert ok is False
    assert "tampered" in detail or "mismatch" in detail


def test_log_chain_links_rows(tmp_path):
    log = AdvancementLog(tmp_path / "log.jsonl")
    r1 = log.append("request", {"a": 1})
    r2 = log.append("execution", {"b": 2})
    assert r1["prev_hash"] == "GENESIS"
    assert r2["prev_hash"] == r1["hash"]
    assert r1["seq"] == 0 and r2["seq"] == 1


# ── exit criteria: Phase 0 ────────────────────────────────────────────


def test_phase0_exit_agreement_below_threshold(tmp_path):
    ev = _phase0_evidence_met(n=30, agreed=27)  # 90% < 95%
    result = check_phase0_exit(ev)
    assert result.met is False
    assert result.checks["min_agreement"] is False
    assert result.checks["min_prs_resolved"] is True


def test_phase0_exit_bad_merge_call_blocks(tmp_path):
    ev = _phase0_evidence_met()
    ev["bad_merge_calls"] = 1
    result = check_phase0_exit(ev)
    assert result.met is False
    assert result.checks["zero_bad_merge_calls"] is False


def test_phase0_exit_watchdog_not_armed_blocks(tmp_path):
    ev = _phase0_evidence_met()
    ev["watchdog"] = {"enabled": True, "mode": "enforcing"}
    result = check_phase0_exit(ev)
    assert result.met is False
    assert result.checks["watchdog_armed_monitor_only"] is False

    ev["watchdog"] = {"enabled": False, "mode": "monitor-only"}
    result = check_phase0_exit(ev)
    assert result.met is False


def test_phase0_exit_incomplete_signal_blocks(tmp_path):
    ev = _phase0_evidence_met()
    broken = dict(ev["shadow_signals"][0])
    del broken["metadata"]  # incomplete audit signal
    ev["shadow_signals"][0] = broken
    result = check_phase0_exit(ev)
    assert result.met is False
    assert result.checks["all_shadow_signals_complete"] is False


# ── exit criteria: Phase 1 -> 2 ───────────────────────────────────────


def test_phase1_exit_twenty_clean_consecutive_met():
    now = datetime.now(timezone.utc)
    decisions = [_merge_row(now - timedelta(hours=i)) for i in range(20)]
    result = check_phase1_exit({"auto_merge_decisions": decisions})
    assert result.met is True, result.failed_checks()


def test_phase1_exit_rollback_in_streak_blocks():
    now = datetime.now(timezone.utc)
    decisions = [_merge_row(now - timedelta(hours=i)) for i in range(20)]
    decisions[5]["outcome"] = "rolled_back"
    result = check_phase1_exit({"auto_merge_decisions": decisions})
    assert result.met is False
    assert result.checks["zero_rollbacks_in_streak"] is False


def test_phase1_exit_fewer_than_twenty_blocks():
    now = datetime.now(timezone.utc)
    decisions = [_merge_row(now - timedelta(hours=i)) for i in range(19)]
    result = check_phase1_exit({"auto_merge_decisions": decisions})
    assert result.met is False
    assert result.checks["twenty_merges_exist"] is False


def test_phase1_exit_refusals_do_not_break_streak():
    """Refused/escalated decisions never merged, so they don't break the
    20-clean streak — only a rollback does."""
    now = datetime.now(timezone.utc)
    decisions = [_merge_row(now - timedelta(hours=i)) for i in range(20)]
    decisions.append(
        {
            "decision": "refused",
            "outcome": "n/a",
            "timestamp": now.isoformat(),
        }
    )
    result = check_phase1_exit({"auto_merge_decisions": decisions})
    assert result.met is True


# ── exit criteria: Phase 2 -> 3 ───────────────────────────────────────


def _phase2_decisions(n_merges: int, n_rollbacks: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n_merges):
        outcome = "rolled_back" if i < n_rollbacks else "clean"
        rows.append(_merge_row(now - timedelta(days=i % 29), outcome))
    return rows


def _phase2_evidence(n_merges=100, n_rollbacks=1, justified=True) -> dict:
    pages = ["novelty-1", "novelty-2"]
    reports = []
    if justified:
        reports = [
            {
                "novelty_pages_justified": [
                    {"id": p, "justification": "known flake, quarantined"}
                    for p in pages
                ]
            }
        ]
    return {
        "auto_merge_decisions": _phase2_decisions(n_merges, n_rollbacks),
        "novelty_pages": pages,
        "weekly_reports": reports,
    }


def test_phase2_exit_met():
    result = check_phase2_exit(_phase2_evidence(n_merges=100, n_rollbacks=1))
    assert result.met is True, result.failed_checks()


def test_phase2_exit_rollback_rate_blocks():
    result = check_phase2_exit(_phase2_evidence(n_merges=100, n_rollbacks=2))
    assert result.met is False
    assert result.checks["rollback_rate_below_2pct"] is False


def test_phase2_exit_unjustified_novelty_blocks():
    result = check_phase2_exit(_phase2_evidence(justified=False))
    assert result.met is False
    assert result.checks["every_novelty_page_justified"] is False


def test_phase2_exit_quiet_month_is_not_evidence():
    result = check_phase2_exit(_phase2_evidence(n_merges=0, n_rollbacks=0))
    assert result.met is False
    assert result.checks["window_has_merges"] is False


# ── ladder dispatch ───────────────────────────────────────────────────


def test_evaluate_exit_criteria_rejects_non_adjacent():
    result = evaluate_exit_criteria(0, 2, {})
    assert result.met is False
    assert result.checks["adjacent_step"] is False


def test_evaluate_exit_criteria_rejects_unknown_step():
    result = evaluate_exit_criteria(3, 4, {})
    assert result.met is False


def test_validate_approval_rejects_non_mapping():
    policy = PhasePolicy(
        version="phase-v1",
        phase=0,
        advancements_enabled=True,
        approver="mbgulden",
    )
    request = AdvancementRequest(
        request_id="x",
        from_phase=0,
        to_phase=1,
        evidence_pointers={},
        requester="t",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    valid, reasons = validate_approval("not-a-dict", policy, request, "h")
    assert valid is False
    assert reasons


def test_validate_approval_future_timestamp_refused():
    policy = PhasePolicy(
        version="phase-v1",
        phase=0,
        advancements_enabled=True,
        approver="mbgulden",
    )
    request = AdvancementRequest(
        request_id="x",
        from_phase=0,
        to_phase=1,
        evidence_pointers={},
        requester="t",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    approval = _approval("hash", timestamp=future)
    valid, reasons = validate_approval(approval, policy, request, "hash")
    assert valid is False
    assert any("future" in r for r in reasons)


def test_discover_active_policy_picks_highest_version(tmp_path):
    spec_dir = tmp_path / "spec"
    _write_policy(spec_dir, phase=0)
    (spec_dir / "phase_policy_v2.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "phase-v2",
                "phase": 1,
                "advancements_enabled": True,
                "approver": "mbgulden",
                "chunks": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    policy = discover_active_policy(spec_dir)
    assert policy.phase == 1
    assert policy.advancements_enabled is True


def test_discover_active_policy_fail_closed_on_missing(tmp_path):
    with pytest.raises(PhaseAdvancementError):
        discover_active_policy(tmp_path / "empty")


# ─────────────────────────────────────────────────────────────────────
# Phase 0 -> 1 deterministic exit path (T1 deterministic entry)
# ─────────────────────────────────────────────────────────────────────

from prismatic.review_factory.phase_advancement import (
    DETERMINISTIC_CONSECUTIVE_CLEAN,
    EXIT_PATH_AGREEMENT,
    EXIT_PATH_DETERMINISTIC,
    EXIT_PATH_EITHER,
    check_phase0_exit_deterministic,
    deterministic_clean_streak,
)


def _det_decision(i, change_class="docs", clean=True, **overrides):
    d = {
        "pr": 500 + i,
        "change_class": change_class,
        "timestamp": f"2026-09-27T17:{i:02d}:00Z",
        "ci_green": True,
        "novelty_clean": True,
        "no_policy_exclusions": True,
        "deterministic_clean": True,
        "receipt_emitted": True,
    }
    if not clean:
        d["ci_green"] = False
    d.update(overrides)
    return d


def _det_evidence(n=20, **kw):
    return {
        "deterministic_decisions": [_det_decision(i, **kw) for i in range(n)],
        "bad_merge_calls": 0,
        "shadow_signals": [_shadow_signal(900 + i) for i in range(3)],
        "watchdog": {"enabled": True, "mode": "monitor-only"},
    }


def test_deterministic_path_met_on_20_consecutive_clean():
    result = check_phase0_exit_deterministic(_det_evidence(20))
    assert result.met is True
    assert result.checks["min_consecutive_deterministic_clean"] is True
    assert result.checks["zero_bad_merge_calls"] is True
    assert result.checks["watchdog_armed_monitor_only"] is True
    assert result.checks["all_shadow_signals_complete"] is True


def test_deterministic_path_fails_below_streak_bar():
    result = check_phase0_exit_deterministic(_det_evidence(19))
    assert result.met is False
    assert result.checks["min_consecutive_deterministic_clean"] is False


def test_deterministic_streak_breaks_on_unclean_decision():
    ev = _det_evidence(20)
    ev["deterministic_decisions"][10] = _det_decision(10, clean=False)
    assert deterministic_clean_streak(ev["deterministic_decisions"]) == 9
    result = check_phase0_exit_deterministic(ev)
    assert result.met is False


def test_deterministic_streak_ignores_non_t1_classes():
    # agent_standard decisions never count toward the T1 streak.
    ev = _det_evidence(20)
    ev["deterministic_decisions"].append(_det_decision(20, change_class="agent_standard"))
    assert deterministic_clean_streak(ev["deterministic_decisions"]) == 0
    assert check_phase0_exit_deterministic(ev).met is False


def test_deterministic_path_fail_closed_on_missing_evidence():
    result = check_phase0_exit_deterministic({})
    assert result.met is False
    assert result.checks["min_consecutive_deterministic_clean"] is False


def test_deterministic_path_refuses_bad_merge_calls():
    ev = _det_evidence(20)
    ev["bad_merge_calls"] = 1
    result = check_phase0_exit_deterministic(ev)
    assert result.met is False
    assert result.checks["zero_bad_merge_calls"] is False


def test_deterministic_path_requires_monitor_only_watchdog():
    ev = _det_evidence(20)
    ev["watchdog"] = {"enabled": True, "mode": "enforcing"}
    result = check_phase0_exit_deterministic(ev)
    assert result.met is False
    assert result.checks["watchdog_armed_monitor_only"] is False


def test_evaluate_exit_criteria_dispatches_on_exit_path():
    ev = _det_evidence(20)
    ev["exit_path"] = EXIT_PATH_DETERMINISTIC
    result = evaluate_exit_criteria(0, 1, ev)
    assert result.met is True
    assert "exit_path=deterministic" in result.detail


def test_evaluate_exit_criteria_defaults_to_legacy_agreement():
    ev = _phase0_evidence_met()
    # No exit_path key: legacy agreement behavior preserved.
    result = evaluate_exit_criteria(0, 1, ev)
    assert result.met is True
    assert "n_decided=30" in result.detail


def test_evaluate_exit_criteria_rejects_unknown_exit_path():
    ev = _det_evidence(20)
    ev["exit_path"] = "teleport"
    result = evaluate_exit_criteria(0, 1, ev)
    assert result.met is False
    assert result.checks["known_exit_path"] is False


def test_evaluate_exit_criteria_either_path():
    # Agreement fails (2.2%-style), deterministic passes -> either met.
    ev = _phase0_evidence_met(n=30, agreed=1)
    det = _det_evidence(20)
    ev.update(
        {
            "deterministic_decisions": det["deterministic_decisions"],
            "watchdog": det["watchdog"],
            "exit_path": EXIT_PATH_EITHER,
        }
    )
    result = evaluate_exit_criteria(0, 1, ev)
    assert result.met is True
    assert "exit_path=either" in result.detail


def test_phase_policy_rejects_unknown_exit_path():
    with pytest.raises(PhaseAdvancementError):
        PhasePolicy.from_dict(
            {
                "version": "phase-vX",
                "phase": 0,
                "advancements_enabled": False,
                "approver": "mbgulden",
                "exit_path": "teleport",
            }
        )


def test_phase_policy_defaults_exit_path_to_agreement():
    policy = PhasePolicy.from_dict(
        {
            "version": "phase-vX",
            "phase": 0,
            "advancements_enabled": False,
            "approver": "mbgulden",
        }
    )
    assert policy.exit_path == EXIT_PATH_AGREEMENT


def test_phase_policy_v3_selects_deterministic(tmp_path):
    import shutil

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    v3 = HERE.parent.parent / "spec" / "phase_policy_v3.yaml"
    assert v3.exists(), "phase_policy_v3.yaml must ship with this change"
    shutil.copy(v3, spec_dir / "phase_policy_v3.yaml")
    policy = discover_active_policy(spec_dir)
    assert policy.version == "phase-v3"
    assert policy.phase == 0
    assert policy.exit_path == EXIT_PATH_DETERMINISTIC
    assert policy.advancements_enabled is False  # still hard-disabled
    assert policy.approver == "mbgulden"
