"""
Prismatic Engine — Routing Rules Engine
========================================

Declarative rules engine that evaluates an incoming :class:`TaskContext`
against a configurable rule set and returns the optimal target agent or
queue, factoring in capability, priority, current load, and cost.

The engine is **pure** — no I/O, no Linear API calls, no dispatcher state.
Callers (the dispatcher, a webhook handler, a CLI tool, or a test) feed it
a :class:`TaskContext`, a snapshot of :class:`AgentState`, and a list of
:class:`Rule` objects, and get back a :class:`RoutingDecision`.

Rule schema
-----------
Rules are loaded from YAML or JSON and follow this shape::

    rules:
      - id: "high-priority-code"
        priority: 100                # Higher wins on ties
        when:
          capabilities_any: ["code"]
          priority_min: 4
        target:
          agent: "codex"
          queue: "fast"
          reason: "Premium agent for high-priority code tasks"
      - id: "cost-cap"
        priority: 50
        when:
          estimated_cost_usd_max: 0.10
        target:
          agent: "gpt-oss-120b"
          queue: "budget"
          reason: "Cost-capped routing"

The ``when`` block supports these predicates (all AND-combined; a missing
key means "no constraint on this dimension"):

* ``capabilities_any``   — task capability tags; rule matches if ANY tag overlaps
* ``capabilities_all``   — task capability tags; rule matches only if ALL present
* ``priority_min``       — task priority >= N (0-5 scale, 5 = urgent)
* ``priority_max``       — task priority <= N
* ``source_any``         — task source identifier (e.g. "linear", "github")
* ``tag_any``            — task tags (case-insensitive)
* ``estimated_cost_usd_max`` — task estimated cost ceiling
* ``load_factor_max``    — rule applies only when target agent's load ≤ N (0..1)
* ``require_agent_available`` — bool, default True; rule skips if target is down

The ``target`` block describes what to dispatch to:

* ``agent``   — agent identifier (e.g. "codex", "fred", "agy", "gpt-oss-120b")
* ``queue``   — queue identifier (e.g. "fast", "budget", "default")
* ``reason``  — human-readable explanation, included in the decision trace

Usage
-----
::

    from prismatic.core.rules_engine import (
        RulesEngine, Rule, TaskContext, AgentState, load_rules,
    )

    rules = load_rules("config/routing_rules.yaml")
    engine = RulesEngine(rules)

    task = TaskContext(
        task_id="GRO-547",
        capabilities={"code", "review"},
        priority=3,
        source="linear",
        tags={"backend"},
        estimated_cost_usd=0.05,
    )
    agents = [
        AgentState(agent="codex", available=True, load_factor=0.2),
        AgentState(agent="gpt-oss-120b", available=True, load_factor=0.7),
    ]
    decision = engine.evaluate(task, agents)
    print(decision.target_agent, decision.matched_rule, decision.trace)

Design constraints
------------------
* Pure function — same input → same output. Caller controls load sampling.
* No global state. Multiple ``RulesEngine`` instances can co-exist (e.g. one
  per pipeline). This is intentional: the dispatcher spins up a fresh engine
  per eval cycle so rule edits take effect on the next cycle without locks.
* Validation at load time, not eval time. Malformed rules raise
  :class:`RuleValidationError` once, not per-task.
* ``trace`` is a list of strings for human-readable decision audits. The
  dispatcher can attach the trace to a Linear comment for forensics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ═══════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════


class RuleValidationError(ValueError):
    """Raised when a rule definition is malformed (at load time)."""


# ═══════════════════════════════════════════════════════════════
# Inputs
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TaskContext:
    """Snapshot of an incoming task the engine is asked to route.

    All fields are optional except ``task_id`` — the engine treats absent
    fields as "no constraint on that dimension" (rules with that predicate
    just won't match unless the task happens to satisfy it).
    """

    task_id: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0
    source: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class AgentState:
    """Runtime snapshot of a candidate target agent.

    The dispatcher populates this from health checks, queue depth, and
    recent fallback telemetry. The engine only consumes ``available`` and
    ``load_factor``; richer state lives elsewhere.
    """

    agent: str
    available: bool = True
    load_factor: float = 0.0  # 0.0 = idle, 1.0 = saturated


# ═══════════════════════════════════════════════════════════════
# Rule + Decision
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Rule:
    """A single declarative routing rule.

    Instances are constructed by :func:`load_rules` from YAML/JSON, but
    callers can also build them directly for tests or programmatic rules.
    """

    id: str
    priority: int
    when: dict[str, Any]
    target: dict[str, Any]
    source_path: str = ""  # For error messages; "" if built in-memory

    def __post_init__(self) -> None:
        if not self.id:
            raise RuleValidationError("rule missing 'id'")
        if not isinstance(self.target, dict) or not self.target.get("agent"):
            raise RuleValidationError(
                f"rule {self.id!r} missing 'target.agent'"
            )
        if not isinstance(self.when, dict):
            raise RuleValidationError(
                f"rule {self.id!r}: 'when' must be a dict"
            )


@dataclass(frozen=True)
class RoutingDecision:
    """The engine's verdict on where to send a task.

    Attributes:
        target_agent: The chosen agent identifier, or ``""`` if no rule
            matched AND no default was configured.
        target_queue: The chosen queue identifier, or ``""``.
        matched_rule: ID of the rule that won, or ``""`` for default.
        reason: Human-readable explanation (from the rule's ``reason``).
        trace: Ordered list of audit strings (one per rule considered).
            Useful for attaching to a Linear comment as forensics.
    """

    target_agent: str = ""
    target_queue: str = ""
    matched_rule: str = ""
    reason: str = ""
    trace: tuple[str, ...] = ()


# ═══════════════════════════════════════════════════════════════
# Load + validate
# ═══════════════════════════════════════════════════════════════


def load_rules(config_path: str | Path) -> list[Rule]:
    """Load routing rules from a YAML or JSON file.

    Expected file shape::

        default:
          agent: "gpt-oss-120b"
          queue: "default"
          reason: "Fallback when no specific rule matches"
        rules:
          - id: "..."
            priority: 100
            when: {...}
            target: {...}

    The optional ``default`` block is the fallback target when no rule
    matches. If absent, :meth:`RulesEngine.evaluate` returns a decision
    with empty ``target_agent`` and a trace noting "no match".

    Raises:
        FileNotFoundError: if *config_path* does not exist.
        RuleValidationError: if any rule is malformed.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Routing rules config not found: {path}")

    raw = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuleValidationError(
                f"PyYAML is required to load YAML rules ({path})"
            ) from exc
        parsed = yaml.safe_load(raw)
    elif path.suffix == ".json":
        parsed = json.loads(raw)
    else:
        raise RuleValidationError(
            f"Unsupported rules config format: {path.suffix} (use .yaml/.yml/.json)"
        )

    return parse_rules(parsed, source_path=str(path))


def parse_rules(parsed: Any, source_path: str = "") -> list[Rule]:
    """Parse a dict/list of rules (as loaded from YAML/JSON) into :class:`Rule` objects.

    Split out from :func:`load_rules` so callers can construct rules from
    an in-memory dict (tests, programmatic config) without writing a file.

    The top-level dict may contain a ``default`` key (engine fallback)
    and a ``rules`` key (list of rule dicts). The ``rules`` key can also
    be a bare list — that's the common programmatic shape.
    """
    default_block: dict[str, Any] = {}
    rules_list: list[dict[str, Any]]

    if isinstance(parsed, list):
        rules_list = parsed
    elif isinstance(parsed, dict):
        default_block = parsed.get("default", {}) or {}
        rules_list = parsed.get("rules", []) or []
        if not rules_list and any(
            isinstance(v, dict) and ("when" in v or "target" in v)
            for v in parsed.values()
        ):
            # Top-level dict IS the rules list (no "rules" wrapper).
            rules_list = [parsed]
    else:
        raise RuleValidationError(
            f"Rules config must be a list or dict (got {type(parsed).__name__})"
        )

    if not isinstance(rules_list, list):
        raise RuleValidationError("'rules' must be a list")

    rules: list[Rule] = []
    for idx, raw in enumerate(rules_list):
        if not isinstance(raw, dict):
            raise RuleValidationError(
                f"rule #{idx} is not a dict (got {type(raw).__name__})"
            )
        rid = raw.get("id") or f"rule-{idx}"
        try:
            rules.append(
                Rule(
                    id=str(rid),
                    priority=int(raw.get("priority", 0)),
                    when=dict(raw.get("when", {}) or {}),
                    target=dict(raw.get("target", {}) or {}),
                    source_path=source_path,
                )
            )
        except RuleValidationError:
            raise
        except Exception as exc:
            raise RuleValidationError(
                f"rule {rid!r}: {exc}"
            ) from exc

    return rules


def parse_default(parsed: Any) -> dict[str, Any]:
    """Extract the optional ``default`` block from a parsed rules file.

    Returns an empty dict if no default is configured.
    """
    if isinstance(parsed, dict):
        return dict(parsed.get("default", {}) or {})
    return {}


# ═══════════════════════════════════════════════════════════════
# Engine
# ═══════════════════════════════════════════════════════════════


class RulesEngine:
    """Pure routing rules engine.

    Construct with a list of :class:`Rule` (from :func:`load_rules` or
    built directly). Call :meth:`evaluate` with a task and a snapshot of
    agent states to get a :class:`RoutingDecision`.

    The engine does not mutate the rules list — pass a fresh engine per
    evaluation cycle to pick up config changes without locks.
    """

    def __init__(
        self,
        rules: Iterable[Rule],
        default_target: dict[str, Any] | None = None,
    ) -> None:
        self.rules: tuple[Rule, ...] = tuple(rules)
        # Higher priority wins. Stable sort on (priority desc, original_index)
        # so ties keep file order.
        self._sorted_indices: tuple[int, ...] = tuple(
            sorted(
                range(len(self.rules)),
                key=lambda i: (-self.rules[i].priority, i),
            )
        )
        self.default_target: dict[str, Any] = dict(default_target or {})
        # If a rule's target agent isn't in the supplied agent states, the
        # evaluator skips it (no agent == no routing). Set this False to
        # dispatch anyway (caller is responsible for spawning the agent).
        self.require_known_agent: bool = True

    # ── Public API ──────────────────────────────────────────

    def evaluate(
        self,
        task: TaskContext,
        agents: Iterable[AgentState],
    ) -> RoutingDecision:
        """Evaluate *task* against the configured rules + *agents* snapshot.

        Returns a :class:`RoutingDecision` with the winning rule (or the
        default if no rule matched). The ``trace`` lists every rule
        considered with its match outcome — attach this to a Linear
        comment for forensic audits.
        """
        agent_states = {a.agent: a for a in agents}
        trace: list[str] = []

        for idx in self._sorted_indices:
            rule = self.rules[idx]
            ok, why = self._match(rule, task, agent_states)
            trace.append(
                f"{'✓' if ok else '✗'} rule[{rule.id}] priority={rule.priority}: {why}"
            )
            if ok:
                target_agent = rule.target.get("agent", "")
                target_queue = rule.target.get("queue", "")
                return RoutingDecision(
                    target_agent=target_agent,
                    target_queue=target_queue,
                    matched_rule=rule.id,
                    reason=rule.target.get("reason", ""),
                    trace=tuple(trace),
                )

        # No rule matched — fall back to default if configured.
        if self.default_target:
            trace.append(
                f"→ default: agent={self.default_target.get('agent', '')!r}"
            )
            return RoutingDecision(
                target_agent=self.default_target.get("agent", ""),
                target_queue=self.default_target.get("queue", ""),
                matched_rule="",
                reason=self.default_target.get(
                    "reason", "Default fallback (no rule matched)"
                ),
                trace=tuple(trace),
            )

        trace.append("→ no match, no default — task unrouteable")
        return RoutingDecision(trace=tuple(trace))

    # ── Matching ────────────────────────────────────────────

    @staticmethod
    def _match(
        rule: Rule,
        task: TaskContext,
        agents: dict[str, AgentState],
    ) -> tuple[bool, str]:
        """Return (matched, reason). Reason is a short audit string."""
        w = rule.when

        # capabilities_any: rule matches if any rule tag is in task capabilities
        if "capabilities_any" in w:
            required = set(w["capabilities_any"])
            if not (required & task.capabilities):
                return (
                    False,
                    f"capabilities_any={sorted(required)} ∩ task={sorted(task.capabilities)} = ∅",
                )

        # capabilities_all: rule matches only if every required cap is present
        if "capabilities_all" in w:
            required = set(w["capabilities_all"])
            missing = required - task.capabilities
            if missing:
                return (
                    False,
                    f"capabilities_all missing: {sorted(missing)}",
                )

        # priority_min / priority_max
        if "priority_min" in w and task.priority < int(w["priority_min"]):
            return (
                False,
                f"priority {task.priority} < min {w['priority_min']}",
            )
        if "priority_max" in w and task.priority > int(w["priority_max"]):
            return (
                False,
                f"priority {task.priority} > max {w['priority_max']}",
            )

        # source_any
        if "source_any" in w:
            allowed = set(w["source_any"])
            if task.source and task.source not in allowed:
                return (
                    False,
                    f"source {task.source!r} not in {sorted(allowed)}",
                )

        # tag_any
        if "tag_any" in w:
            required = set(w["tag_any"])
            if not (required & {t.lower() for t in task.tags}):
                return (
                    False,
                    f"tag_any={sorted(required)} ∩ task_tags={sorted(task.tags)} = ∅",
                )

        # estimated_cost_usd_max
        if "estimated_cost_usd_max" in w:
            cap = float(w["estimated_cost_usd_max"])
            if task.estimated_cost_usd > cap:
                return (
                    False,
                    f"cost ${task.estimated_cost_usd:.4f} > cap ${cap:.4f}",
                )

        # Target-agent availability + load (read from agents snapshot)
        target_agent = rule.target.get("agent", "")
        if target_agent:
            state = agents.get(target_agent)
            if state is None:
                # Unknown agent — skip unless caller opted out of the gate.
                return (
                    False,
                    f"target agent {target_agent!r} not in agents snapshot",
                )
            if not state.available and w.get("require_agent_available", True):
                return (
                    False,
                    f"target agent {target_agent!r} unavailable",
                )
            if "load_factor_max" in w:
                cap = float(w["load_factor_max"])
                if state.load_factor > cap:
                    return (
                        False,
                        f"target {target_agent!r} load {state.load_factor:.2f} > max {cap:.2f}",
                    )

        return (True, "matched")


# ═══════════════════════════════════════════════════════════════
# Helper — public rule-match predicate (used by tests + introspection)
# ═══════════════════════════════════════════════════════════════


def rule_matches(
    rule: Rule,
    task: TaskContext,
    agents: dict[str, AgentState] | None = None,
) -> bool:
    """Public predicate: does *rule* match *task* against *agents*?

    Provided for callers that want to introspect rule applicability without
    building a full :class:`RulesEngine`. Same semantics as the engine's
    internal matcher.
    """
    ok, _ = RulesEngine._match(rule, task, agents or {})
    return ok


__all__ = [
    "AgentState",
    "Rule",
    "RuleValidationError",
    "RoutingDecision",
    "RulesEngine",
    "TaskContext",
    "load_rules",
    "parse_default",
    "parse_rules",
    "rule_matches",
]