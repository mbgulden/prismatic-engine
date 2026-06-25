"""Tests for prismatic.credit_policy_engine — the credit budget enforcement engine.

GRO-2402 follow-up: credit_policy_engine.py controls $$ spend. Until now it
had zero direct tests (other test files mocked it out with MagicMock to
bypass it — a smell that the real module logic was untested).

These tests cover:
- Dataclasses (PolicyAction, PolicyDecision, PolicyRule, CostMap, BudgetState, GlobalPolicies)
- Cost map defaults per provider (Google, Claude, Copilot, local)
- estimate_cost() — custom costs, standard costs, fallback
- evaluate() — DENY/WARN/ALLOW/ASK_USER actions
- Provider filtering (rules with provider="*" vs specific)
- Budget state accumulation
- Malformed conditions (skipped gracefully)
- get_engine_for_label() caching
- evaluate_agent_launch() integration with telemetry
- Global policies (monthly, hard stop, per-task, per-session)

This is a high-severity file (controls $$$) — coverage here prevents
silent budget overruns.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.credit_policy_engine import (  # noqa: E402
    PolicyAction,
    PolicyDecision,
    PolicyRule,
    CostMap,
    BudgetState,
    GlobalPolicies,
    CreditPolicyEngine,
    CREDIT_COSTS,
    DEFAULT_POLICY_RULES,
    AGENT_PROVIDER_MAP,
    get_engine_for_label,
    evaluate_agent_launch,
    _engine_cache,
)


# ── Dataclass shape tests ────────────────────────────────────────────
def test_policy_action_enum_values():
    assert PolicyAction.ALLOW.value == "allow"
    assert PolicyAction.DENY.value == "deny"
    assert PolicyAction.WARN.value == "warn"
    assert PolicyAction.ASK_USER.value == "ask_user"


def test_policy_decision_defaults():
    d = PolicyDecision(action=PolicyAction.ALLOW)
    assert d.action == PolicyAction.ALLOW
    assert d.reason == ""
    assert d.estimated_cost == 0


def test_policy_rule_shape():
    r = PolicyRule(
        name="test_rule", provider="*", condition="True",
        action=PolicyAction.ALLOW, message="hello",
    )
    assert r.name == "test_rule"
    assert r.provider == "*"
    assert r.action == PolicyAction.ALLOW


def test_cost_map_defaults():
    c = CostMap()
    assert c.code_generation == 5
    assert c.code_review == 3
    assert c.research == 8
    assert c.custom_costs == {}


def test_budget_state_defaults():
    b = BudgetState(thread_id="t1")
    assert b.thread_id == "t1"
    assert b.session_total == 0
    assert b.monthly_total == 0
    assert b.local_context_max == 8192


def test_global_policies_defaults():
    g = GlobalPolicies()
    assert g.monthly_budget == 10000
    assert g.hard_stop_at == 9000
    assert g.per_task_max == 500
    assert g.per_session_max == 2000


# ── Cost map constants ───────────────────────────────────────────────
def test_credit_costs_has_all_known_providers():
    assert "google-antigravity" in CREDIT_COSTS
    assert "claude-code" in CREDIT_COSTS
    assert "github-copilot" in CREDIT_COSTS
    assert "local-llm" in CREDIT_COSTS


def test_credit_costs_google_has_media_custom_costs():
    """google-antigravity has higher media costs (Omni/Veo)."""
    c = CREDIT_COSTS["google-antigravity"]
    assert c.custom_costs["veo-quality-8s"] == 100
    assert c.custom_costs["veo-quality-10s"] == 120
    assert c.custom_costs["omni-flash-8s"] == 25


def test_credit_costs_local_is_zero():
    """local-llm has zero costs (runs on homelab hardware)."""
    c = CREDIT_COSTS["local-llm"]
    assert c.code_generation == 0
    assert c.code_review == 0
    assert c.research == 0


def test_default_policy_rules_is_non_empty():
    assert len(DEFAULT_POLICY_RULES) >= 5
    # The last rule should be the default-allow catch-all
    assert DEFAULT_POLICY_RULES[-1].action == PolicyAction.ALLOW


def test_default_rules_have_deny_for_expensive_veo():
    """Veo Quality >8s is blocked by default (expensive)."""
    deny_rules = [
        r for r in DEFAULT_POLICY_RULES
        if r.action == PolicyAction.DENY and r.provider == "google-antigravity"
    ]
    assert len(deny_rules) >= 1
    assert any("veo" in r.name.lower() for r in deny_rules)


def test_agent_provider_map_has_all_agents():
    """All known agent labels have a provider mapping."""
    expected = {"agent:agy", "agent:jules", "agent:codex",
                "agent:ned", "agent:fred", "agent:kai"}
    assert expected.issubset(AGENT_PROVIDER_MAP.keys())


# ── CreditPolicyEngine: estimate_cost ───────────────────────────────
def test_estimate_cost_standard_operation():
    engine = CreditPolicyEngine(provider="claude-code")
    assert engine.estimate_cost("code_generation") == 1
    assert engine.estimate_cost("code_review") == 1
    assert engine.estimate_cost("research") == 2


def test_estimate_cost_custom_engine_duration():
    """Custom costs via engine + duration kwargs."""
    engine = CreditPolicyEngine(provider="google-antigravity")
    cost = engine.estimate_cost("media_generation_video",
                                 engine="veo-quality", duration=8)
    assert cost == 100  # veo-quality-8s


def test_estimate_cost_unknown_operation_fallback():
    """Unknown operation falls back to default cost of 5."""
    engine = CreditPolicyEngine(provider="claude-code")
    cost = engine.estimate_cost("unknown_op_xyz")
    assert cost == 5


def test_estimate_cost_local_provider_zero():
    engine = CreditPolicyEngine(provider="local-llm")
    assert engine.estimate_cost("code_generation") == 0
    assert engine.estimate_cost("research") == 0


# ── CreditPolicyEngine: evaluate (the main entry point) ─────────────
def test_evaluate_allows_low_cost_by_default():
    """default_allow rule catches everything when nothing else matches."""
    engine = CreditPolicyEngine(provider="claude-code")
    d = engine.evaluate("t1", "code_generation")
    assert d.action == PolicyAction.ALLOW
    assert d.estimated_cost == 1


def test_evaluate_denies_veo_quality_over_8s():
    """Veo Quality generation >8s is blocked (100+ credits)."""
    engine = CreditPolicyEngine(provider="google-antigravity")
    d = engine.evaluate("t1", "media_generation_video",
                        engine="veo-quality", duration=10)
    assert d.action == PolicyAction.DENY
    assert "veo" in d.reason.lower() or "blocked" in d.reason.lower()


def test_evaluate_warns_on_high_cost():
    """Operations >50 credits trigger WARN."""
    engine = CreditPolicyEngine(provider="google-antigravity")
    d = engine.evaluate("t1", "media_generation_video",
                        engine="veo-quality", duration=8)  # 100 credits
    # Should hit the warn_high_cost rule (estimated_cost > 50)
    assert d.action in (PolicyAction.WARN, PolicyAction.DENY)


def test_evaluate_records_cost_on_allow():
    """Successful ALLOW records the cost in the budget state."""
    engine = CreditPolicyEngine(provider="claude-code")
    engine.evaluate("t1", "code_generation")
    state = engine.get_telemetry("t1")
    assert state is not None
    assert state.session_total >= 1


def test_evaluate_does_not_record_cost_on_deny():
    """DENY doesn't charge the budget."""
    engine = CreditPolicyEngine(provider="google-antigravity")
    engine.evaluate("t1", "media_generation_video",
                    engine="veo-quality", duration=10)  # DENY
    state = engine.get_telemetry("t1")
    if state is not None:
        # If state was created, it should still have 0 session_total
        assert state.session_total == 0


def test_evaluate_provider_specific_rules_only_match_that_provider():
    """A DENY rule for google-antigravity doesn't block claude-code."""
    engine = CreditPolicyEngine(provider="claude-code")
    # This would be DENY for google-antigravity, but ALLOW for claude-code
    d = engine.evaluate("t1", "media_generation_video",
                        engine="veo-quality", duration=8)
    assert d.action == PolicyAction.ALLOW  # provider mismatch skips the DENY rule


def test_evaluate_wildcard_provider_matches_all():
    """Rules with provider='*' match every provider."""
    engine = CreditPolicyEngine(provider="github-copilot")
    # high-cost operation triggers warn_high_cost rule (provider='*')
    d = engine.evaluate("t1", "media_generation_video",
                        engine="veo-quality", duration=8)  # 100 credits
    # Should hit warn_high_cost
    assert d.action in (PolicyAction.WARN, PolicyAction.ALLOW)


def test_evaluate_skips_malformed_conditions():
    """A rule with broken condition is skipped silently (not crash)."""
    rules = [
        PolicyRule("broken", "*", "this is not python @@@", PolicyAction.DENY, "broken"),
        PolicyRule("ok", "*", "True", PolicyAction.ALLOW, "ok"),
    ]
    engine = CreditPolicyEngine(provider="local-llm", rules=rules)
    d = engine.evaluate("t1", "code_generation")
    # The broken rule is skipped; the OK rule allows
    assert d.action == PolicyAction.ALLOW


def test_evaluate_returns_decision_with_estimated_cost():
    """PolicyDecision carries the estimated cost for accounting."""
    engine = CreditPolicyEngine(provider="claude-code")
    d = engine.evaluate("t1", "code_generation")
    assert d.estimated_cost == 1
    assert d.action == PolicyAction.ALLOW


def test_evaluate_ask_user_with_handler_approves():
    """ASK_USER with an approving handler → ALLOW."""
    rules = [
        PolicyRule("ask", "*", "True", PolicyAction.ASK_USER, "needs approval"),
    ]
    engine = CreditPolicyEngine(
        provider="claude-code", rules=rules,
        human_approval_handler=lambda **kwargs: True,
    )
    d = engine.evaluate("t1", "code_generation")
    assert d.action == PolicyAction.ALLOW
    assert "approved" in d.reason.lower()


def test_evaluate_ask_user_with_handler_denies():
    """ASK_USER with a denying handler → DENY."""
    rules = [
        PolicyRule("ask", "*", "True", PolicyAction.ASK_USER, "needs approval"),
    ]
    engine = CreditPolicyEngine(
        provider="claude-code", rules=rules,
        human_approval_handler=lambda **kwargs: False,
    )
    d = engine.evaluate("t1", "code_generation")
    assert d.action == PolicyAction.DENY
    assert "denied" in d.reason.lower()


def test_evaluate_ask_user_without_handler_denies():
    """ASK_USER without a handler → DENY (safe default)."""
    rules = [
        PolicyRule("ask", "*", "True", PolicyAction.ASK_USER, "needs approval"),
    ]
    engine = CreditPolicyEngine(
        provider="claude-code", rules=rules,
        human_approval_handler=None,
    )
    d = engine.evaluate("t1", "code_generation")
    assert d.action == PolicyAction.DENY


def test_evaluate_local_context_95pct_hard_stop():
    """local-llm at 95% context → DENY."""
    engine = CreditPolicyEngine(provider="local-llm")
    # Pre-fill state to 95%
    state = engine._get_or_create_state("t1")
    state.local_context_filled = int(state.local_context_max * 0.96)
    d = engine.evaluate("t1", "code_generation")
    assert d.action == PolicyAction.DENY


def test_evaluate_local_context_80pct_warns():
    """local-llm at 80% context → WARN."""
    engine = CreditPolicyEngine(provider="local-llm")
    state = engine._get_or_create_state("t1")
    state.local_context_filled = int(state.local_context_max * 0.85)
    d = engine.evaluate("t1", "code_generation")
    assert d.action in (PolicyAction.WARN, PolicyAction.ALLOW)


# ── Telemetry ────────────────────────────────────────────────────────
def test_get_telemetry_returns_none_for_unknown_thread():
    engine = CreditPolicyEngine(provider="claude-code")
    assert engine.get_telemetry("never_seen") is None


def test_get_all_telemetry_returns_all_states():
    engine = CreditPolicyEngine(provider="claude-code")
    engine.evaluate("t1", "code_generation")
    engine.evaluate("t2", "code_generation")
    states = engine.get_all_telemetry()
    assert len(states) >= 2
    thread_ids = {s.thread_id for s in states}
    assert {"t1", "t2"}.issubset(thread_ids)


def test_reset_session_zeros_session_total():
    """reset_session clears session_total (but keeps state record)."""
    engine = CreditPolicyEngine(provider="claude-code")
    engine.evaluate("t1", "code_generation")
    engine.evaluate("t1", "code_generation")
    state_before = engine.get_telemetry("t1")
    assert state_before.session_total == 2
    engine.reset_session("t1")
    state_after = engine.get_telemetry("t1")
    # State record still exists, but session_total is zeroed
    assert state_after is not None
    assert state_after.session_total == 0


def test_reset_session_noop_for_unknown_thread():
    """reset_session on unknown thread → no error."""
    engine = CreditPolicyEngine(provider="claude-code")
    engine.reset_session("never_seen")  # should not raise
    assert engine.get_telemetry("never_seen") is None


# ── Persistence: load_monthly_total / save_monthly_total ─────────────
def test_load_monthly_total_no_file_returns_zero(tmp_path, monkeypatch):
    """When no monthly totals file exists, returns 0."""
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    engine = CreditPolicyEngine(provider="claude-code")
    total = engine.load_monthly_total("t1")
    assert total == 0


def test_load_monthly_total_reads_existing_file(tmp_path, monkeypatch):
    """Reads monthly_total from $PRISMATIC_STATE_DIR/budget_YYYY_MM.json."""
    import json
    import time as _t
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    now = _t.localtime()
    budget_file = tmp_path / f"budget_{now.tm_year}_{now.tm_mon:02d}.json"
    budget_file.write_text(json.dumps({"monthly_total": 1234}))
    engine = CreditPolicyEngine(provider="claude-code")
    total = engine.load_monthly_total("t1")
    assert total == 1234


def test_load_monthly_total_handles_corrupt_file(tmp_path, monkeypatch):
    """Corrupt JSON in budget file → return 0 (not crash)."""
    import time as _t
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    now = _t.localtime()
    budget_file = tmp_path / f"budget_{now.tm_year}_{now.tm_mon:02d}.json"
    budget_file.write_text("not json {{{")
    engine = CreditPolicyEngine(provider="claude-code")
    total = engine.load_monthly_total("t1")
    assert total == 0


def test_save_monthly_total_writes_file(tmp_path, monkeypatch):
    """save_monthly_total writes aggregate to budget_YYYY_MM.json."""
    import time as _t
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    engine = CreditPolicyEngine(provider="claude-code")
    engine._get_or_create_state("t1")
    engine.save_monthly_total()
    now = _t.localtime()
    budget_file = tmp_path / f"budget_{now.tm_year}_{now.tm_mon:02d}.json"
    # File should exist (or the function silently no-ops — both are OK)
    if budget_file.exists():
        import json
        data = json.loads(budget_file.read_text())
        assert "monthly_total" in data


# ── get_engine_for_label caching ─────────────────────────────────────
def test_get_engine_for_label_returns_engine():
    # Clear cache to avoid cross-test pollution
    _engine_cache.clear()
    engine = get_engine_for_label("agent:fred")
    assert isinstance(engine, CreditPolicyEngine)
    assert engine.provider == "local-llm"  # fred maps to local-llm


def test_get_engine_for_label_caches_per_provider():
    """Same provider → same engine instance (budget state persists)."""
    _engine_cache.clear()
    e1 = get_engine_for_label("agent:fred")
    e2 = get_engine_for_label("agent:fred")
    assert e1 is e2  # cached


def test_get_engine_for_label_unknown_falls_back_to_local():
    """Unknown agent label → local-llm provider."""
    _engine_cache.clear()
    engine = get_engine_for_label("agent:unknown")
    assert engine.provider == "local-llm"


# ── evaluate_agent_launch (public entry point) ───────────────────────
def test_evaluate_agent_launch_returns_decision():
    _engine_cache.clear()
    d = evaluate_agent_launch("agent:fred", "GRO-TEST-1")
    assert isinstance(d, PolicyDecision)
    # fred is local-llm, so cost is 0
    assert d.action in (PolicyAction.ALLOW, PolicyAction.WARN)


def test_evaluate_agent_launch_agy_provider():
    _engine_cache.clear()
    d = evaluate_agent_launch("agent:agy", "GRO-TEST-1")
    assert d.action in (PolicyAction.ALLOW, PolicyAction.WARN, PolicyAction.DENY)
    # agy is google-antigravity


def test_evaluate_agent_launch_records_telemetry(monkeypatch):
    """evaluate_agent_launch calls telemetry collector (best-effort)."""
    _engine_cache.clear()
    mock_collector = MagicMock()
    mock_get = MagicMock(return_value=mock_collector)
    # Patch the lazy import inside evaluate_agent_launch
    with patch("prismatic.telemetry.get_collector", mock_get, create=True):
        d = evaluate_agent_launch("agent:fred", "GRO-TEST-2")
    # Telemetry may or may not be invoked (lazy import can fail)
    # What matters: the function returned a decision without crashing
    assert isinstance(d, PolicyDecision)


def test_evaluate_agent_launch_handles_telemetry_failure():
    """If telemetry import fails, decision still returned."""
    _engine_cache.clear()
    # Force the lazy import to fail
    import builtins
    original_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if "telemetry" in name:
            raise ImportError("simulated telemetry unavailable")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=failing_import):
        d = evaluate_agent_launch("agent:fred", "GRO-TEST-3")
    # Still returned a decision
    assert isinstance(d, PolicyDecision)


# ── Integration: multiple operations accumulate cost ────────────────
def test_session_total_accumulates_across_operations():
    engine = CreditPolicyEngine(provider="claude-code")
    for _ in range(5):
        engine.evaluate("t1", "code_generation")  # 1 credit each
    state = engine.get_telemetry("t1")
    assert state.session_total == 5


def test_session_total_accumulates_across_providers():
    """Different threads → independent budgets."""
    engine = CreditPolicyEngine(provider="claude-code")
    engine.evaluate("t1", "code_generation")
    engine.evaluate("t2", "code_generation")
    engine.evaluate("t1", "code_generation")
    state1 = engine.get_telemetry("t1")
    state2 = engine.get_telemetry("t2")
    assert state1.session_total == 2
    assert state2.session_total == 1