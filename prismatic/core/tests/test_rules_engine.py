"""Tests for prismatic.core.rules_engine — declarative Routing Rules Engine.

Coverage matrix:
- Rule schema validation (load time + direct construction)
- TaskContext / AgentState construction (frozen dataclasses)
- Matchers: capabilities_any/all, priority_min/max, source_any, tag_any,
  cost cap, load_factor_max, require_agent_available
- Engine: rule ordering by priority, tie-break by file order, trace shape
- Default fallback: present vs absent, reason propagation
- parse_rules: list / dict / "rules" wrapper / "default" wrapper / bare dict
- load_rules: YAML + JSON + missing file + unsupported ext + bad shape
- Edge cases: empty rules, empty agents, no matches anywhere, unknown agent
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from prismatic.core.rules_engine import (
    AgentState,
    Rule,
    RuleValidationError,
    RoutingDecision,
    RulesEngine,
    TaskContext,
    load_rules,
    parse_rules,
    rule_matches,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def healthy_agents() -> dict[str, AgentState]:
    """Three healthy agents at varying load levels."""
    return {
        "codex": AgentState(agent="codex", available=True, load_factor=0.2),
        "agy": AgentState(agent="agy", available=True, load_factor=0.5),
        "gpt-oss-120b": AgentState(
            agent="gpt-oss-120b", available=True, load_factor=0.7
        ),
    }


@pytest.fixture
def base_rule() -> Rule:
    """A baseline rule used by tests that only modify `when`/`target`."""
    return Rule(
        id="base",
        priority=0,
        when={},
        target={"agent": "codex", "queue": "default", "reason": "fallback"},
    )


# ═══════════════════════════════════════════════════════════════
# Rule construction + validation
# ═══════════════════════════════════════════════════════════════


class TestRuleConstruction:
    def test_minimal_rule_validates(self) -> None:
        r = Rule(id="r1", priority=10, when={}, target={"agent": "codex"})
        assert r.id == "r1"
        assert r.priority == 10

    def test_missing_id_rejected(self) -> None:
        with pytest.raises(RuleValidationError, match="missing 'id'"):
            Rule(id="", priority=0, when={}, target={"agent": "codex"})

    def test_missing_target_agent_rejected(self) -> None:
        with pytest.raises(RuleValidationError, match="missing 'target.agent'"):
            Rule(id="r1", priority=0, when={}, target={})

    def test_when_must_be_dict(self) -> None:
        with pytest.raises(RuleValidationError, match="'when' must be a dict"):
            Rule(
                id="r1",
                priority=0,
                when="not-a-dict",  # type: ignore[arg-type]
                target={"agent": "codex"},
            )

    def test_rule_is_frozen(self) -> None:
        r = Rule(id="r1", priority=0, when={}, target={"agent": "codex"})
        with pytest.raises(Exception):  # FrozenInstanceError subclasses
            r.id = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════
# TaskContext / AgentState construction
# ═══════════════════════════════════════════════════════════════


class TestContextDataclasses:
    def test_task_context_defaults(self) -> None:
        t = TaskContext(task_id="GRO-547")
        assert t.task_id == "GRO-547"
        assert t.capabilities == frozenset()
        assert t.priority == 0
        assert t.source == ""
        assert t.tags == frozenset()
        assert t.estimated_cost_usd == 0.0

    def test_task_context_with_caps(self) -> None:
        t = TaskContext(
            task_id="GRO-547",
            capabilities=frozenset({"code", "review"}),
            priority=3,
            source="linear",
            tags=frozenset({"backend"}),
            estimated_cost_usd=0.05,
        )
        assert "code" in t.capabilities
        assert t.priority == 3
        assert t.estimated_cost_usd == 0.05

    def test_agent_state_defaults(self) -> None:
        a = AgentState(agent="codex")
        assert a.available is True
        assert a.load_factor == 0.0

    def test_dataclasses_are_frozen(self) -> None:
        t = TaskContext(task_id="GRO-547")
        with pytest.raises(Exception):
            t.task_id = "different"  # type: ignore[misc]
        a = AgentState(agent="codex")
        with pytest.raises(Exception):
            a.load_factor = 0.5  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════
# Matchers
# ═══════════════════════════════════════════════════════════════


class TestMatchers:
    def test_capabilities_any_matches_overlap(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"capabilities_any": ["code", "design"]},
            target=base_rule.target,
        )
        task = TaskContext(task_id="t", capabilities=frozenset({"code"}))
        assert rule_matches(rule, task) is True

    def test_capabilities_any_rejects_disjoint(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"capabilities_any": ["code", "design"]},
            target=base_rule.target,
        )
        task = TaskContext(task_id="t", capabilities=frozenset({"writing"}))
        assert rule_matches(rule, task) is False

    def test_capabilities_all_requires_every_tag(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"capabilities_all": ["code", "review"]},
            target=base_rule.target,
        )
        assert (
            rule_matches(
                rule,
                TaskContext(task_id="t", capabilities=frozenset({"code", "review"})),
            )
            is True
        )
        assert (
            rule_matches(
                rule, TaskContext(task_id="t", capabilities=frozenset({"code"}))
            )
            is False
        )

    def test_priority_min_boundary(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"priority_min": 3},
            target=base_rule.target,
        )
        assert rule_matches(rule, TaskContext(task_id="t", priority=3)) is True
        assert rule_matches(rule, TaskContext(task_id="t", priority=2)) is False

    def test_priority_max_boundary(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"priority_max": 2},
            target=base_rule.target,
        )
        assert rule_matches(rule, TaskContext(task_id="t", priority=2)) is True
        assert rule_matches(rule, TaskContext(task_id="t", priority=3)) is False

    def test_source_any_filters(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"source_any": ["linear", "github"]},
            target=base_rule.target,
        )
        assert rule_matches(rule, TaskContext(task_id="t", source="linear")) is True
        assert rule_matches(rule, TaskContext(task_id="t", source="slack")) is False
        # Empty source = no constraint (matches)
        assert rule_matches(rule, TaskContext(task_id="t")) is True

    def test_tag_any_is_case_insensitive(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"tag_any": ["backend"]},
            target=base_rule.target,
        )
        assert (
            rule_matches(rule, TaskContext(task_id="t", tags=frozenset({"Backend"})))
            is True
        )
        assert (
            rule_matches(rule, TaskContext(task_id="t", tags=frozenset({"frontend"})))
            is False
        )

    def test_cost_max(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"estimated_cost_usd_max": 0.10},
            target=base_rule.target,
        )
        assert (
            rule_matches(rule, TaskContext(task_id="t", estimated_cost_usd=0.05))
            is True
        )
        assert (
            rule_matches(rule, TaskContext(task_id="t", estimated_cost_usd=0.50))
            is False
        )

    def test_load_factor_max_skips_saturated_agent(
        self, healthy_agents: dict[str, AgentState], base_rule: Rule
    ) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={"load_factor_max": 0.3},
            target={"agent": "codex"},
        )
        # codex has load 0.2 → matches
        assert (
            rule_matches(
                rule,
                TaskContext(task_id="t"),
                healthy_agents,
            )
            is True
        )
        # Switch target to agy (load 0.5) → no longer matches
        rule_agy = Rule(
            id="r",
            priority=0,
            when={"load_factor_max": 0.3},
            target={"agent": "agy"},
        )
        assert (
            rule_matches(
                rule_agy,
                TaskContext(task_id="t"),
                healthy_agents,
            )
            is False
        )

    def test_unavailable_agent_rejected_by_default(
        self, healthy_agents: dict[str, AgentState], base_rule: Rule
    ) -> None:
        healthy_agents["codex"] = AgentState(
            agent="codex", available=False, load_factor=0.0
        )
        rule = Rule(
            id="r",
            priority=0,
            when={},
            target={"agent": "codex"},
        )
        assert rule_matches(rule, TaskContext(task_id="t"), healthy_agents) is False

    def test_require_agent_available_false_disables_gate(
        self, healthy_agents: dict[str, AgentState], base_rule: Rule
    ) -> None:
        healthy_agents["codex"] = AgentState(
            agent="codex", available=False, load_factor=0.0
        )
        rule = Rule(
            id="r",
            priority=0,
            when={"require_agent_available": False},
            target={"agent": "codex"},
        )
        assert rule_matches(rule, TaskContext(task_id="t"), healthy_agents) is True

    def test_unknown_target_agent_rejected(
        self, healthy_agents: dict[str, AgentState], base_rule: Rule
    ) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={},
            target={"agent": "nonexistent"},
        )
        assert rule_matches(rule, TaskContext(task_id="t"), healthy_agents) is False

    def test_multiple_predicates_are_anded(self, base_rule: Rule) -> None:
        rule = Rule(
            id="r",
            priority=0,
            when={
                "capabilities_any": ["code"],
                "priority_min": 3,
                "estimated_cost_usd_max": 0.10,
            },
            target={"agent": "codex"},
        )
        # All three satisfied
        assert (
            rule_matches(
                rule,
                TaskContext(
                    task_id="t",
                    capabilities=frozenset({"code"}),
                    priority=4,
                    estimated_cost_usd=0.05,
                ),
            )
            is True
        )
        # Priority too low
        assert (
            rule_matches(
                rule,
                TaskContext(
                    task_id="t",
                    capabilities=frozenset({"code"}),
                    priority=2,
                    estimated_cost_usd=0.05,
                ),
            )
            is False
        )


# ═══════════════════════════════════════════════════════════════
# Engine.evaluate
# ═══════════════════════════════════════════════════════════════


class TestEngineEvaluate:
    def test_first_matching_rule_wins(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        rules = [
            Rule(
                id="low",
                priority=1,
                when={},
                target={"agent": "agy", "queue": "fallback"},
            ),
            Rule(
                id="high",
                priority=10,
                when={},
                target={"agent": "codex", "queue": "fast"},
            ),
        ]
        engine = RulesEngine(rules)
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert decision.target_agent == "codex"
        assert decision.matched_rule == "high"
        assert decision.target_queue == "fast"

    def test_priority_descending_order(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        # Insert in wrong order; engine should still pick highest priority first.
        rules = [
            Rule(id="low", priority=1, when={}, target={"agent": "agy"}),
            Rule(id="mid", priority=5, when={}, target={"agent": "codex"}),
            Rule(id="high", priority=10, when={}, target={"agent": "gpt-oss-120b"}),
        ]
        engine = RulesEngine(rules)
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert decision.matched_rule == "high"
        assert decision.target_agent == "gpt-oss-120b"

    def test_ties_break_by_file_order(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        rules = [
            Rule(id="first", priority=5, when={}, target={"agent": "codex"}),
            Rule(id="second", priority=5, when={}, target={"agent": "agy"}),
        ]
        engine = RulesEngine(rules)
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        # Stable sort: equal priorities keep insertion order → first wins.
        assert decision.matched_rule == "first"

    def test_no_match_falls_back_to_default(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        rules = [
            Rule(
                id="specific",
                priority=100,
                when={"capabilities_any": ["unicorn"]},
                target={"agent": "codex"},
            ),
        ]
        engine = RulesEngine(
            rules,
            default_target={
                "agent": "gpt-oss-120b",
                "queue": "default",
                "reason": "Fallback when no rule matches",
            },
        )
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert decision.target_agent == "gpt-oss-120b"
        assert decision.matched_rule == ""
        assert decision.reason == "Fallback when no rule matches"

    def test_no_match_no_default_returns_empty_decision(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        rules = [
            Rule(
                id="nope",
                priority=100,
                when={"capabilities_any": ["unicorn"]},
                target={"agent": "codex"},
            ),
        ]
        engine = RulesEngine(rules)
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert decision.target_agent == ""
        assert decision.target_queue == ""
        assert decision.matched_rule == ""
        # Trace should record the no-match outcome
        assert any("no match" in line for line in decision.trace)

    def test_trace_records_every_rule(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        # Make codex unavailable so the first rule is rejected, forcing the
        # engine to evaluate the second rule too. Trace should record both.
        healthy_agents["codex"] = AgentState(
            agent="codex", available=False, load_factor=0.0
        )
        rules = [
            Rule(id="r1", priority=10, when={}, target={"agent": "codex"}),
            Rule(
                id="r2",
                priority=5,
                when={"capabilities_any": ["unicorn"]},
                target={"agent": "agy"},
            ),
        ]
        engine = RulesEngine(
            rules, default_target={"agent": "gpt-oss-120b", "queue": "default"}
        )
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert len(decision.trace) == 3  # r1 reject, r2 reject, default fallback
        assert "r1" in decision.trace[0]
        assert "r2" in decision.trace[1]
        assert "default" in decision.trace[2]

    def test_empty_rules_uses_default(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        engine = RulesEngine([], default_target={"agent": "codex", "queue": "default"})
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert decision.target_agent == "codex"

    def test_skips_unavailable_target(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        # codex is unavailable; agy is healthy. First rule targets codex → skip.
        healthy_agents["codex"] = AgentState(
            agent="codex", available=False, load_factor=0.0
        )
        rules = [
            Rule(id="codex-rule", priority=10, when={}, target={"agent": "codex"}),
            Rule(id="agy-rule", priority=5, when={}, target={"agent": "agy"}),
        ]
        engine = RulesEngine(rules)
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert decision.matched_rule == "agy-rule"
        assert decision.target_agent == "agy"


# ═══════════════════════════════════════════════════════════════
# parse_rules / load_rules
# ═══════════════════════════════════════════════════════════════


class TestParseRules:
    def test_parse_bare_list(self) -> None:
        parsed = [
            {"id": "r1", "priority": 5, "when": {}, "target": {"agent": "codex"}},
        ]
        rules, default = parse_rules(parsed)
        assert len(rules) == 1
        assert rules[0].id == "r1"
        assert default == {}

    def test_parse_dict_with_rules_key(self) -> None:
        parsed = {
            "rules": [
                {"id": "r1", "when": {}, "target": {"agent": "codex"}},
            ],
        }
        rules, default = parse_rules(parsed)
        assert len(rules) == 1
        assert default == {}

    def test_parse_dict_with_default(self) -> None:
        parsed = {
            "default": {"agent": "gpt-oss-120b", "queue": "default"},
            "rules": [{"id": "r1", "when": {}, "target": {"agent": "codex"}}],
        }
        rules, default = parse_rules(parsed)
        assert default["agent"] == "gpt-oss-120b"
        assert default["queue"] == "default"

    def test_parse_top_level_dict_as_single_rule(self) -> None:
        parsed = {"when": {}, "target": {"agent": "codex"}}
        rules, _default = parse_rules(parsed)
        assert len(rules) == 1
        assert rules[0].target["agent"] == "codex"

    def test_parse_rejects_non_list_non_dict(self) -> None:
        with pytest.raises(RuleValidationError, match="must be a list or dict"):
            parse_rules("not-a-config")  # type: ignore[arg-type]

    def test_parse_rejects_non_dict_rule(self) -> None:
        with pytest.raises(RuleValidationError, match="not a dict"):
            parse_rules(["not-a-dict"])

    def test_parse_handles_missing_when(self) -> None:
        # Missing `when` defaults to {}; missing `target.agent` is rejected
        # (validated by `test_load_rules_bad_rule_shape`).
        rules, _ = parse_rules([{"id": "r1", "target": {"agent": "codex"}}])
        assert rules[0].when == {}
        assert rules[0].target == {"agent": "codex"}

    def test_parse_assigns_synthetic_id_when_missing(self) -> None:
        rules, _ = parse_rules([{"when": {}, "target": {"agent": "codex"}}])
        assert rules[0].id == "rule-0"

    def test_load_rules_yaml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "rules.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                default:
                  agent: gpt-oss-120b
                  queue: default
                rules:
                  - id: high-pri-code
                    priority: 100
                    when:
                      capabilities_any: [code]
                      priority_min: 4
                    target:
                      agent: codex
                      queue: fast
                """
            ).strip()
        )
        rules = load_rules(cfg)
        assert len(rules) == 1
        assert rules[0].id == "high-pri-code"
        assert rules[0].when["capabilities_any"] == ["code"]
        assert rules[0].when["priority_min"] == 4
        assert rules[0].target["agent"] == "codex"

    def test_load_rules_json(self, tmp_path: Path) -> None:
        cfg = tmp_path / "rules.json"
        cfg.write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "id": "json-rule",
                            "priority": 5,
                            "when": {"priority_min": 2},
                            "target": {"agent": "agy"},
                        }
                    ]
                }
            )
        )
        rules = load_rules(cfg)
        assert len(rules) == 1
        assert rules[0].id == "json-rule"

    def test_load_rules_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_rules(tmp_path / "nope.yaml")

    def test_load_rules_unsupported_ext(self, tmp_path: Path) -> None:
        cfg = tmp_path / "rules.toml"
        cfg.write_text("not = 'valid'")
        with pytest.raises(RuleValidationError, match="Unsupported"):
            load_rules(cfg)

    def test_load_rules_bad_rule_shape(self, tmp_path: Path) -> None:
        cfg = tmp_path / "rules.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                rules:
                  - id: bad-rule
                    when: {}
                    target: {}
                """
            ).strip()
        )
        with pytest.raises(RuleValidationError, match="missing 'target.agent'"):
            load_rules(cfg)


# ═══════════════════════════════════════════════════════════════
# Integration / end-to-end
# ═══════════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_yaml_loaded_engine_routes_correctly(
        self, tmp_path: Path, healthy_agents: dict[str, AgentState]
    ) -> None:
        cfg = tmp_path / "rules.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                default:
                  agent: gpt-oss-120b
                  queue: default
                  reason: "Cost-capped fallback"
                rules:
                  - id: high-priority-code
                    priority: 100
                    when:
                      capabilities_any: [code]
                      priority_min: 4
                    target:
                      agent: codex
                      queue: fast
                      reason: "Premium codex for urgent code work"
                  - id: budget
                    priority: 50
                    when:
                      estimated_cost_usd_max: 0.05
                    target:
                      agent: gpt-oss-120b
                      queue: budget
                """
            ).strip()
        )
        rules = load_rules(cfg)
        engine = RulesEngine(
            rules,
            default_target={"agent": "gpt-oss-120b", "queue": "default"},
        )

        # High-priority code task → codex (rule 1 wins)
        d1 = engine.evaluate(
            TaskContext(
                task_id="GRO-A",
                capabilities=frozenset({"code"}),
                priority=5,
                estimated_cost_usd=0.02,
            ),
            healthy_agents.values(),
        )
        assert d1.matched_rule == "high-priority-code"
        assert d1.target_agent == "codex"

        # Low-priority code task within budget → budget rule
        d2 = engine.evaluate(
            TaskContext(
                task_id="GRO-B",
                capabilities=frozenset({"code"}),
                priority=2,
                estimated_cost_usd=0.02,
            ),
            healthy_agents.values(),
        )
        assert d2.matched_rule == "budget"
        assert d2.target_agent == "gpt-oss-120b"
        assert d2.target_queue == "budget"

        # Writing task outside cost cap → default fallback
        d3 = engine.evaluate(
            TaskContext(
                task_id="GRO-C",
                capabilities=frozenset({"writing"}),
                priority=1,
                estimated_cost_usd=1.00,
            ),
            healthy_agents.values(),
        )
        assert d3.matched_rule == ""
        assert d3.target_agent == "gpt-oss-120b"

    def test_engine_is_pure_no_mutation(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        """Same inputs → same output. No global state pollution."""
        rules = [Rule(id="r1", priority=10, when={}, target={"agent": "codex"})]
        engine = RulesEngine(rules)
        task = TaskContext(task_id="t")
        d1 = engine.evaluate(task, healthy_agents.values())
        d2 = engine.evaluate(task, healthy_agents.values())
        assert d1 == d2

    def test_engine_does_not_mutate_agents(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        before = {a.agent: a.load_factor for a in healthy_agents.values()}
        rules = [Rule(id="r1", priority=10, when={}, target={"agent": "codex"})]
        engine = RulesEngine(rules)
        engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        after = {a.agent: a.load_factor for a in healthy_agents.values()}
        assert before == after

    def test_engine_accepts_generator_agents(
        self, healthy_agents: dict[str, AgentState]
    ) -> None:
        """agents can be a one-shot generator (consumed once)."""
        rules = [Rule(id="r1", priority=10, when={}, target={"agent": "codex"})]
        engine = RulesEngine(rules)
        decision = engine.evaluate(
            TaskContext(task_id="t"),
            iter(healthy_agents.values()),  # type: ignore[arg-type]
        )
        assert decision.target_agent == "codex"


# ═══════════════════════════════════════════════════════════════
# RoutingDecision shape
# ═══════════════════════════════════════════════════════════════


class TestRoutingDecision:
    def test_default_construction(self) -> None:
        d = RoutingDecision()
        assert d.target_agent == ""
        assert d.target_queue == ""
        assert d.matched_rule == ""
        assert d.reason == ""
        assert d.trace == ()

    def test_trace_is_tuple(self, healthy_agents: dict[str, AgentState]) -> None:
        rules = [Rule(id="r1", priority=10, when={}, target={"agent": "codex"})]
        engine = RulesEngine(rules)
        decision = engine.evaluate(TaskContext(task_id="t"), healthy_agents.values())
        assert isinstance(decision.trace, tuple)
