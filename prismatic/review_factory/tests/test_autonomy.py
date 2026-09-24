"""Tests for the earned-autonomy tier policy engine (Phase 2).

Covers every fail-closed branch of ``can_auto_merge`` in rule order,
plus the brake, the spec loader, the lazy trust boundary, and the
revocation read-out. Plain asserts.

Lazy-trust boundary: this module never imports
``prismatic.review_factory.trust`` at module level — the phase-1 ledger
must stay out of these tests' import graph so they stay green on main
without phase 1 merged.
"""

import sys
import types

import pytest

from prismatic.review_factory import autonomy


# ─────────────────────────────────────────────────────────────────────
# can_auto_merge — fail-closed rule order
# ─────────────────────────────────────────────────────────────────────


def _clean(**overrides):
    """A change that would be allowed: tier 1, docs, CLEAN, judge CLEAR."""
    kw = {
        "tier": 1,
        "change_class": "docs",
        "deterministic_verdict": "CLEAN",
        "judgment": {"decision": "CLEAR"},
    }
    kw.update(overrides)
    return autonomy.can_auto_merge(**kw)


def test_rule1_brake_engaged_refuses_even_clean_change():
    d = _clean(brake_engaged=True)
    assert d.allowed is False
    assert d.reason == "brake_engaged"


def test_rule2_unknown_tier_refuses():
    d = _clean(tier=7)
    assert d.allowed is False
    assert d.reason == "unknown_tier"


def test_rule3_tier0_refuses():
    d = _clean(tier=0, change_class="docs")
    assert d.allowed is False
    assert d.reason == "t0_no_auto_merge"


def test_rule4_tier3_refuses_by_design():
    d = _clean(tier=3)
    assert d.allowed is False
    assert d.reason == "t3_unbuilt"


def test_rule5_class_not_in_tier_refuses():
    d = _clean(tier=1, change_class="agent_standard")
    assert d.allowed is False
    assert d.reason == "class_not_in_tier"


def test_rule6_deterministic_not_clean_refuses():
    d = _clean(deterministic_verdict="REPAIR")
    assert d.allowed is False
    assert d.reason == "deterministic_not_clean"


def test_rule6_unknown_verdict_refuses():
    d = _clean(deterministic_verdict="ERROR")
    assert d.allowed is False
    assert d.reason == "deterministic_not_clean"


def test_rule7_novelty_flags_refuse():
    d = _clean(novelty_flags=("first_seen_path",))
    assert d.allowed is False
    assert d.reason == "novelty_flagged"


def test_rule8_judgment_pause_refuses():
    d = _clean(judgment={"decision": "PAUSE"})
    assert d.allowed is False
    assert d.reason == "judgment_pause"


def test_rule9_zero_ai_docs_allows_with_note():
    d = _clean(zero_ai=True, judgment=None)
    assert d.allowed is True
    assert d.reason == "auto_merge_allowed"
    assert d.notes == ("zero_ai_docs_only",)


def test_rule9_zero_ai_chore_refuses():
    d = _clean(zero_ai=True, change_class="chore")
    assert d.allowed is False
    assert d.reason == "zero_ai_conservative"


def test_rule9_zero_ai_dep_bump_refuses():
    d = _clean(zero_ai=True, change_class="dep_bump")
    assert d.allowed is False
    assert d.reason == "zero_ai_conservative"


def test_rule10_allows_clean_tier1_docs():
    d = _clean()
    assert d.allowed is True
    assert d.reason == "auto_merge_allowed"
    assert d.tier == 1
    assert d.change_class == "docs"
    assert d.notes == ()


def test_rule10_allows_tier2_agent_standard():
    d = _clean(tier=2, change_class="agent_standard")
    assert d.allowed is True
    assert d.reason == "auto_merge_allowed"


def test_rule10_judgment_none_allowed_with_skipped_note():
    d = _clean(judgment=None)
    assert d.allowed is True
    assert d.reason == "auto_merge_allowed"
    assert d.notes == ("judgment_skipped",)


def test_rule_order_brake_beats_tier3():
    # Rule 1 fires before rule 4: the reason names the brake, not T3.
    d = _clean(tier=3, brake_engaged=True)
    assert d.allowed is False
    assert d.reason == "brake_engaged"


def test_rule_order_class_check_beats_verdict():
    # Rule 5 fires before rule 6.
    d = _clean(tier=1, change_class="agent_standard", deterministic_verdict="REPAIR")
    assert d.reason == "class_not_in_tier"


def test_decision_is_frozen_dataclass():
    d = _clean()
    with pytest.raises(Exception):
        d.allowed = False  # frozen: assignment must fail


# ─────────────────────────────────────────────────────────────────────
# brake_status
# ─────────────────────────────────────────────────────────────────────


def test_brake_unset_is_not_engaged(monkeypatch):
    monkeypatch.delenv("PRISMATIC_AUTONOMY_ENABLED", raising=False)
    status = autonomy.brake_status()
    assert status == {
        "engaged": False,
        "source": "env:PRISMATIC_AUTONOMY_ENABLED",
    }


@pytest.mark.parametrize("value", ["0", "false", "no", "FALSE", " No "])
def test_brake_engaged_values(monkeypatch, value):
    monkeypatch.setenv("PRISMATIC_AUTONOMY_ENABLED", value)
    assert autonomy.brake_status()["engaged"] is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_brake_not_engaged_values(monkeypatch, value):
    monkeypatch.setenv("PRISMATIC_AUTONOMY_ENABLED", value)
    assert autonomy.brake_status()["engaged"] is False


# ─────────────────────────────────────────────────────────────────────
# load_tier_policy
# ─────────────────────────────────────────────────────────────────────


def test_load_tier_policy_missing_file_returns_disabled_default():
    policy = autonomy.load_tier_policy("/tmp/definitely-missing-autonomy.yaml")
    assert policy == {
        "version": "autonomy-v1",
        "enabled": False,
        "tiers": {},
    }


def test_load_tier_policy_unparseable_file_returns_disabled_default(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(":\n: not yaml : [", encoding="utf-8")
    assert autonomy.load_tier_policy(bad)["enabled"] is False


def test_load_tier_policy_non_mapping_returns_disabled_default(tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    policy = autonomy.load_tier_policy(bad)
    assert policy == {
        "version": "autonomy-v1",
        "enabled": False,
        "tiers": {},
    }


def test_load_tier_policy_reads_shipped_spec():
    policy = autonomy.load_tier_policy()
    assert policy["version"] == "autonomy-v1"
    assert policy["enabled"] is False  # fail-closed default
    tiers = policy["tiers"]
    assert set(tiers) == {0, 1, 2, 3}
    assert tiers[1]["classes"] == ["docs", "chore", "dep_bump"]
    assert tiers[2]["classes"] == [
        "docs",
        "chore",
        "dep_bump",
        "agent_standard",
    ]
    assert tiers[0]["auto_merge"] is False
    assert tiers[3]["auto_merge"] is False  # unbuilt
    assert tiers[1]["graduation"]["consecutive_clean"] == 20
    assert tiers[2]["graduation"]["consecutive_clean"] == 30
    assert tiers[3]["graduation"]["consecutive_clean"] == 50


# ─────────────────────────────────────────────────────────────────────
# check_graduation — lazy trust boundary
# ─────────────────────────────────────────────────────────────────────


def test_check_graduation_delegates_to_explicit_ledger():
    class StubLedger:
        def check_graduation(self):
            return {"proposed_tier": 2, "from_tier": 1}

    assert autonomy.check_graduation(StubLedger()) == {
        "proposed_tier": 2,
        "from_tier": 1,
    }


def test_check_graduation_lazy_imports_trust(monkeypatch):
    # Phase 1 present (stubbed): the lazy import must delegate.
    stub = types.ModuleType("prismatic.review_factory.trust")

    class StubLedger:
        def check_graduation(self):
            return "stub-proposal"

    stub.TrustLedger = StubLedger
    monkeypatch.setitem(sys.modules, "prismatic.review_factory.trust", stub)
    assert autonomy.check_graduation() == "stub-proposal"


def test_check_graduation_none_when_trust_absent(monkeypatch):
    # Phase 1 unmerged: real trust module absent from sys.modules.
    monkeypatch.delitem(sys.modules, "prismatic.review_factory.trust", raising=False)
    assert "prismatic.review_factory.trust" not in sys.modules
    assert autonomy.check_graduation() is None


def test_check_graduation_none_when_ledger_construction_fails(monkeypatch):
    stub = types.ModuleType("prismatic.review_factory.trust")

    class BrokenLedger:
        def __init__(self):
            raise RuntimeError("db unavailable")

    stub.TrustLedger = BrokenLedger
    monkeypatch.setitem(sys.modules, "prismatic.review_factory.trust", stub)
    assert autonomy.check_graduation() is None


# ─────────────────────────────────────────────────────────────────────
# revocation_triggers — pure read-out
# ─────────────────────────────────────────────────────────────────────


def test_revocation_triggers_empty_status():
    assert autonomy.revocation_triggers({}) == []


def test_revocation_triggers_rollback():
    status = {
        "rollback_count_30d": 1,
        "pause_precision": 0.99,
        "pauses_trailing_30": 25,
    }
    assert autonomy.revocation_triggers(status) == ["rollback_in_30d"]


def test_revocation_triggers_precision_collapse():
    status = {
        "rollback_count_30d": 0,
        "pause_precision": 0.49,
        "pauses_trailing_30": 20,
    }
    assert autonomy.revocation_triggers(status) == ["precision_collapse"]


def test_revocation_triggers_both():
    status = {
        "rollback_count_30d": 2,
        "pause_precision": 0.10,
        "pauses_trailing_30": 30,
    }
    assert autonomy.revocation_triggers(status) == [
        "rollback_in_30d",
        "precision_collapse",
    ]


def test_revocation_triggers_precision_none_no_collapse():
    status = {
        "rollback_count_30d": 0,
        "pause_precision": None,
        "pauses_trailing_30": 50,
    }
    assert autonomy.revocation_triggers(status) == []


def test_revocation_triggers_few_pauses_no_collapse():
    status = {
        "rollback_count_30d": 0,
        "pause_precision": 0.10,
        "pauses_trailing_30": 19,
    }
    assert autonomy.revocation_triggers(status) == []


def test_revocation_triggers_healthy_no_triggers():
    status = {
        "rollback_count_30d": 0,
        "pause_precision": 0.92,
        "pauses_trailing_30": 25,
    }
    assert autonomy.revocation_triggers(status) == []
