"""Tests for the Phase 0 shadow-mode observer.

Deterministic cases only: the decision function is pure, so every case
below is fully determined by its inputs. No network, no live PRs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.shadow_agreement import (
    ACTUAL_CLOSED_UNMERGED,
    ACTUAL_MERGED,
    ACTUAL_OPEN,
    agreement_rate,
    calls_agree,
    shadow_exit_met,
)
from prismatic.review_factory.shadow_observer import (
    CALL_MERGE,
    CALL_SKIP,
    RiskBands,
    ShadowConfigError,
    ShadowInput,
    ShadowPolicy,
    emit_decision,
    evaluate,
    load_bands,
    load_input_from_dict,
    load_policy,
    observe,
)

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"
TIER_POLICY = SPEC_DIR / "policy_file_v1.yaml"
SHADOW_POLICY = SPEC_DIR / "shadow_merge_policy_v1.yaml"
BANDS_FILE = SPEC_DIR / "shadow_risk_bands_v1.yaml"


@pytest.fixture()
def tier_engine() -> PolicyEngine:
    return PolicyEngine.from_yaml(TIER_POLICY)


@pytest.fixture()
def policy() -> ShadowPolicy:
    return load_policy(SHADOW_POLICY)


@pytest.fixture()
def bands() -> RiskBands:
    return load_bands(BANDS_FILE)


def _input(**overrides) -> ShadowInput:
    base = {
        "pr_number": 478,
        "pr_title": "Docs: fix typo in README",
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "changed_files": ("docs/README.md",),
        "ci_green_self_hosted": True,
        "ruff_clean": True,
        "review_verdict": "CLEAN",
        "merge_conflicts": False,
        "branch_protection_satisfied": True,
    }
    base.update(overrides)
    return ShadowInput(**base)


# ── decision function ────────────────────────────────────────────────


def test_all_green_tier0_calls_merge(policy, bands, tier_engine):
    d = evaluate(_input(), policy, bands, tier_engine)
    assert d.call == CALL_MERGE
    assert d.tier == 0
    assert d.tier_name == "DETERMINISTIC_ONLY"
    assert all(g.passed for g in d.gate_results)
    assert d.policy_version == "shadow-v1"


def test_tier1_standard_code_calls_merge(policy, bands, tier_engine):
    d = evaluate(
        _input(changed_files=("prismatic/some_module.py",)), policy, bands, tier_engine
    )
    assert d.call == CALL_MERGE
    assert d.tier == 1


def test_ci_red_calls_skip(policy, bands, tier_engine):
    d = evaluate(_input(ci_green_self_hosted=False), policy, bands, tier_engine)
    assert d.call == CALL_SKIP
    assert any(
        g.gate == "ci_green_self_hosted" and not g.passed for g in d.gate_results
    )


def test_reject_verdict_calls_skip(policy, bands, tier_engine):
    d = evaluate(_input(review_verdict="REJECT"), policy, bands, tier_engine)
    assert d.call == CALL_SKIP
    assert any("verdict_not_reject" in r for r in d.reasons)


def test_repair_verdict_calls_skip(policy, bands, tier_engine):
    # REPAIR means changes requested: not mergeable as-is.
    d = evaluate(_input(review_verdict="REPAIR"), policy, bands, tier_engine)
    assert d.call == CALL_SKIP


def test_merge_conflicts_call_skip(policy, bands, tier_engine):
    d = evaluate(_input(merge_conflicts=True), policy, bands, tier_engine)
    assert d.call == CALL_SKIP


def test_ruff_dirty_calls_skip(policy, bands, tier_engine):
    d = evaluate(_input(ruff_clean=False), policy, bands, tier_engine)
    assert d.call == CALL_SKIP


def test_branch_protection_unsatisfied_calls_skip(policy, bands, tier_engine):
    d = evaluate(_input(branch_protection_satisfied=False), policy, bands, tier_engine)
    assert d.call == CALL_SKIP


def test_tier3_production_calls_skip(policy, bands, tier_engine):
    d = evaluate(
        _input(changed_files=("deploy/receiver.py",)), policy, bands, tier_engine
    )
    assert d.call == CALL_SKIP
    assert d.tier == 3
    assert any("human-only" in r for r in d.reasons)


def test_tier2_sensitive_calls_skip(policy, bands, tier_engine):
    d = evaluate(
        _input(changed_files=("prismatic/review_factory/queue.py",)),
        policy,
        bands,
        tier_engine,
    )
    assert d.call == CALL_SKIP
    assert d.tier == 2


def test_unknown_gate_fails_closed(policy, bands, tier_engine):
    from dataclasses import replace

    weird = replace(policy, gates=policy.gates + ("not_a_real_gate",))
    d = evaluate(_input(), weird, bands, tier_engine)
    assert d.call == CALL_SKIP
    assert any(g.gate == "not_a_real_gate" and not g.passed for g in d.gate_results)


# ── Jev not built: field present, null, bands recorded not applied ───


def test_jev_score_null_and_bands_not_applied(policy, bands, tier_engine):
    d = evaluate(_input(), policy, bands, tier_engine)
    assert d.jev_score is None
    assert d.bands["applied"] is False
    assert d.bands["version"] == "bands-v1"
    assert d.bands["auto_below"] == 0.2
    assert d.bands["human_above"] == 0.6


# ── default-off ──────────────────────────────────────────────────────


def test_observe_disabled_policy_emits_nothing(tmp_path, tier_engine):
    policy = load_policy(SHADOW_POLICY)
    bands = load_bands(BANDS_FILE)
    sink = tmp_path / "shadow.jsonl"
    result = observe(
        _input(), policy=policy, bands=bands, tier_engine=tier_engine, sink=sink
    )
    assert result is None
    assert not sink.exists()


def test_policy_default_is_disabled():
    assert load_policy(SHADOW_POLICY).enabled is False


# ── emission schema ──────────────────────────────────────────────────


def test_emit_decision_writes_signal_schema(tmp_path, policy, bands, tier_engine):
    sink = tmp_path / "shadow.jsonl"
    d = evaluate(_input(), policy, bands, tier_engine)
    out = emit_decision(d, sink)
    assert out == sink
    lines = sink.read_text().splitlines()
    assert len(lines) == 1
    sig = json.loads(lines[0])
    # agent_signal_stream schema (mirrors prismatic-audit bin/emit)
    for key in (
        "agent",
        "event_type",
        "id",
        "message",
        "metadata",
        "severity",
        "source",
        "status",
        "timestamp",
    ):
        assert key in sig, f"missing signal field: {key}"
    assert sig["agent"] == "prismatic-shadow-observer"
    assert sig["event_type"] == "decision"
    md = sig["metadata"]
    assert md["call"] == "merge"
    assert md["tier"] == 0
    assert md["jev_score"] is None
    assert md["shadow_mode"] is True
    assert md["pr_number"] == 478
    assert len(md["gate_results"]) == 5


def test_emit_appends_not_overwrites(tmp_path, policy, bands, tier_engine):
    sink = tmp_path / "shadow.jsonl"
    emit_decision(evaluate(_input(), policy, bands, tier_engine), sink)
    emit_decision(evaluate(_input(pr_number=479), policy, bands, tier_engine), sink)
    assert len(sink.read_text().splitlines()) == 2


# ── config loading is fail-closed ────────────────────────────────────


def test_missing_policy_raises(tmp_path):
    with pytest.raises(ShadowConfigError):
        load_policy(tmp_path / "nope.yaml")


def test_missing_bands_raises(tmp_path):
    with pytest.raises(ShadowConfigError):
        load_bands(tmp_path / "nope.yaml")


# ── input adapter defaults are fail-closed ───────────────────────────


def test_load_input_defaults_fail_closed():
    inp = load_input_from_dict({"pr_number": 1})
    assert inp.review_verdict == "REJECT"  # gate fails without a verdict
    assert inp.merge_conflicts is True  # gate fails without mergeability
    assert inp.ci_green_self_hosted is False
    assert inp.changed_files == ()


# ── agreement comparison ─────────────────────────────────────────────


def test_calls_agree_matrix():
    assert calls_agree("merge", ACTUAL_MERGED) is True
    assert calls_agree("skip", ACTUAL_CLOSED_UNMERGED) is True
    assert calls_agree("merge", ACTUAL_CLOSED_UNMERGED) is False
    assert calls_agree("skip", ACTUAL_MERGED) is False
    assert calls_agree("merge", ACTUAL_OPEN) is None


def test_agreement_rate_excludes_open():
    records = [
        {"system_call": "merge", "actual_outcome": ACTUAL_MERGED},
        {"system_call": "skip", "actual_outcome": ACTUAL_CLOSED_UNMERGED},
        {"system_call": "merge", "actual_outcome": ACTUAL_CLOSED_UNMERGED},
        {"system_call": "merge", "actual_outcome": ACTUAL_OPEN},
    ]
    stats = agreement_rate(records)
    assert stats["n_decided"] == 3
    assert stats["n_agreed"] == 2
    assert stats["n_pending"] == 1
    assert stats["rate"] == pytest.approx(2 / 3)


def test_agreement_rate_empty_is_none():
    stats = agreement_rate([])
    assert stats["rate"] is None
    assert stats["n_decided"] == 0


def test_shadow_exit_criteria():
    records = [{"system_call": "merge", "actual_outcome": ACTUAL_MERGED}] * 29 + [
        {"system_call": "skip", "actual_outcome": ACTUAL_CLOSED_UNMERGED}
    ]
    result = shadow_exit_met(records, bad_merge_calls=0)
    assert result["exit_met"] is True
    assert result["checks"] == {
        "min_prs": True,
        "min_agreement": True,
        "zero_bad_merges": True,
    }


def test_shadow_exit_fails_on_bad_merge():
    records = [{"system_call": "merge", "actual_outcome": ACTUAL_MERGED}] * 30
    result = shadow_exit_met(records, bad_merge_calls=1)
    assert result["exit_met"] is False
    assert result["checks"]["zero_bad_merges"] is False


def test_shadow_exit_fails_below_threshold():
    records = [{"system_call": "merge", "actual_outcome": ACTUAL_MERGED}] * 10
    result = shadow_exit_met(records, bad_merge_calls=0)
    assert result["exit_met"] is False
    assert result["checks"]["min_prs"] is False
