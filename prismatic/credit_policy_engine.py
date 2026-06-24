#!/usr/bin/env python3
"""Credit Policy Engine — evaluates tool call requests against budget policies.

Platform-agnostic credit budget enforcement. Adapted from:
- Google Antigravity SDK policy hooks (policy.deny, policy.ask_user, policy.allow)
- Existing Prismatic Engine BudgetManager pattern
- Pipeline global credit policies from PRISMATIC_ENGINE.yaml

Architecture:
    Tool Call Request → Cost Estimation → Policy Evaluation → Allow/Deny/Warn/AskUser
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════


class PolicyAction(Enum):
    ALLOW = "allow"
    DENY = "deny"
    WARN = "warn"
    ASK_USER = "ask_user"


@dataclass
class PolicyDecision:
    action: PolicyAction
    reason: str = ""
    estimated_cost: int = 0


@dataclass
class PolicyRule:
    name: str
    provider: str  # "*" for all
    condition: str  # Python expression evaluated in context
    action: PolicyAction
    message: str = ""


@dataclass
class CostMap:
    """Per-provider credit costs for each operation type."""

    code_generation: int = 5
    code_review: int = 3
    research: int = 8
    custom_costs: dict = field(default_factory=dict)


@dataclass
class BudgetState:
    """Running budget totals."""

    thread_id: str
    session_total: int = 0
    monthly_total: int = 0
    local_context_filled: int = 0
    local_context_max: int = 8192


@dataclass
class GlobalPolicies:
    monthly_budget: int = 10000
    hard_stop_at: int = 9000
    per_task_max: int = 500
    per_session_max: int = 2000


# ═══════════════════════════════════════════════════════════════
# Credit Cost Maps
# ═══════════════════════════════════════════════════════════════

CREDIT_COSTS: dict[str, CostMap] = {
    "google-antigravity": CostMap(
        code_generation=5,
        code_review=3,
        research=8,
        custom_costs={
            "omni-flash-4s": 15,
            "omni-flash-6s": 20,
            "omni-flash-8s": 25,
            "omni-flash-10s": 30,
            "veo-fast-any": 10,
            "veo-quality-8s": 100,
            "veo-quality-10s": 120,
        },
    ),
    "claude-code": CostMap(
        code_generation=1,
        code_review=1,
        research=2,
    ),
    "github-copilot": CostMap(
        code_generation=0,
        code_review=0,
        research=0,
    ),
    "local-llm": CostMap(
        code_generation=0,
        code_review=0,
        research=0,
    ),
}

# ═══════════════════════════════════════════════════════════════
# Default Policy Rules
# ═══════════════════════════════════════════════════════════════

DEFAULT_POLICY_RULES: list[PolicyRule] = [
    PolicyRule(
        name="block_veo_quality_over_8s",
        provider="google-antigravity",
        condition="engine == 'veo-quality' and duration > 8",
        action=PolicyAction.DENY,
        message="Veo Quality generation over 8 seconds blocked (100+ credits). Use shorter duration.",
    ),
    PolicyRule(
        name="human_approval_veo_quality",
        provider="google-antigravity",
        condition="engine == 'veo-quality'",
        action=PolicyAction.ASK_USER,
        message="Agent requested veo-quality generation (100 credits). Approve?",
    ),
    PolicyRule(
        name="block_over_budget_task",
        provider="*",
        condition="estimated_cost > global.per_task_max",
        action=PolicyAction.DENY,
        message="Task exceeds per-task credit limit.",
    ),
    PolicyRule(
        name="block_over_budget_session",
        provider="*",
        condition="state.session_total + estimated_cost > global.per_session_max",
        action=PolicyAction.DENY,
        message="Session credit limit reached.",
    ),
    PolicyRule(
        name="hard_stop_monthly",
        provider="*",
        condition="state.monthly_total >= global.hard_stop_at",
        action=PolicyAction.DENY,
        message="Monthly budget exhausted. Emergency reserve only.",
    ),
    PolicyRule(
        name="warn_high_cost",
        provider="*",
        condition="estimated_cost > 50",
        action=PolicyAction.WARN,
        message="High-cost operation ({estimated_cost} credits).",
    ),
    PolicyRule(
        name="warn_local_context_80pct",
        provider="local-llm",
        condition="state.local_context_filled >= state.local_context_max * 0.8",
        action=PolicyAction.WARN,
        message="Local model context at 80%. Risk of silent truncation.",
    ),
    PolicyRule(
        name="hard_stop_local_context_95pct",
        provider="local-llm",
        condition="state.local_context_filled >= state.local_context_max * 0.95",
        action=PolicyAction.DENY,
        message="Local model context at 95%. Hard stop to prevent corrupted output.",
    ),
    PolicyRule(
        name="default_allow",
        provider="*",
        condition="True",
        action=PolicyAction.ALLOW,
    ),
]

# Per-agent provider mapping (label → provider name)
AGENT_PROVIDER_MAP: dict[str, str] = {
    "agent:agy": "google-antigravity",
    "agent:jules": "claude-code",
    "agent:codex": "github-copilot",
    "agent:ned": "local-llm",
    "agent:ned-code": "local-llm",
    "agent:ned-infra": "local-llm",
    "agent:ned-audit": "local-llm",
    "agent:ned-review": "local-llm",
    "agent:fred": "local-llm",
    "agent:kai": "local-llm",
}


# ═══════════════════════════════════════════════════════════════
# Policy Engine
# ═══════════════════════════════════════════════════════════════


class CreditPolicyEngine:
    """Evaluates tool call requests against budget policies."""

    def __init__(
        self,
        provider: str,
        rules: list[PolicyRule] | None = None,
        global_policies: GlobalPolicies | None = None,
        cost_map: CostMap | None = None,
        human_approval_handler: Callable | None = None,
    ):
        self.provider = provider
        self.rules = rules or DEFAULT_POLICY_RULES.copy()
        self.global_policies = global_policies or GlobalPolicies()
        self.cost_map = cost_map or CREDIT_COSTS.get(provider, CostMap())
        self.human_approval_handler = human_approval_handler
        self._budget_state: dict[str, BudgetState] = {}

    # ── Public API ──────────────────────────────────────────

    def estimate_cost(self, operation: str, **kwargs) -> int:
        """Estimate credit cost for an operation."""
        # Check custom costs using engine + duration (e.g., "veo-quality-8s")
        engine = kwargs.get("engine", "")
        duration = kwargs.get("duration", "any")
        if engine and duration:
            custom_key = f"{engine}-{duration}s" if isinstance(duration, int) else f"{engine}-{duration}"
            if custom_key in self.cost_map.custom_costs:
                return self.cost_map.custom_costs[custom_key]
        # Also check operation-based custom keys
        custom_key_op = f"{operation}-{duration}" if duration != "any" else f"{operation}-any"
        if custom_key_op in self.cost_map.custom_costs:
            return self.cost_map.custom_costs[custom_key_op]

        # Fall back to standard operation costs
        op_costs = {
            "code_generation": self.cost_map.code_generation,
            "code_review": self.cost_map.code_review,
            "research": self.cost_map.research,
        }
        return op_costs.get(operation, 5)  # default 5 if unknown

    def evaluate(
        self,
        thread_id: str,
        operation: str,
        **kwargs,
    ) -> PolicyDecision:
        """Evaluate whether an operation should be allowed.

        Args:
            thread_id: Unique identifier for the session/issue.
            operation: Operation type (code_generation, code_review, research, etc.).
            **kwargs: Additional context for rule conditions (engine, duration, etc.).

        Returns:
            PolicyDecision with action and reason.
        """
        estimated_cost = self.estimate_cost(operation, **kwargs)
        state = self._get_or_create_state(thread_id)

        # Build evaluation context for rule conditions
        context = {
            "estimated_cost": estimated_cost,
            "global": self.global_policies,
            "state": state,
            "provider": self.provider,
            "operation": operation,
            **kwargs,
        }

        # First-match-wins policy evaluation
        for rule in self.rules:
            # Skip rules for other providers (unless wildcard)
            if rule.provider != "*" and rule.provider != self.provider:
                continue

            # Evaluate condition
            try:
                condition_met = eval(rule.condition, {"__builtins__": {}}, context)
            except Exception:
                continue  # skip malformed conditions

            if not condition_met:
                continue

            # Rule matched — return decision
            message = rule.message.format(**context) if rule.message else ""

            if rule.action == PolicyAction.ALLOW:
                self._record_cost(state, estimated_cost)
                return PolicyDecision(PolicyAction.ALLOW, message, estimated_cost)

            elif rule.action == PolicyAction.WARN:
                self._record_cost(state, estimated_cost)
                print(f"  ⚠️  [POLICY WARN] {rule.name}: {message}")
                return PolicyDecision(PolicyAction.WARN, message, estimated_cost)

            elif rule.action == PolicyAction.DENY:
                return PolicyDecision(PolicyAction.DENY, message, estimated_cost)

            elif rule.action == PolicyAction.ASK_USER:
                if self.human_approval_handler:
                    approved = self.human_approval_handler(
                        tool_name=operation,
                        args=kwargs,
                        estimated_cost=estimated_cost,
                        message=message,
                    )
                    if approved:
                        self._record_cost(state, estimated_cost)
                        return PolicyDecision(
                            PolicyAction.ALLOW,
                            f"Human approved: {message}",
                            estimated_cost,
                        )
                return PolicyDecision(
                    PolicyAction.DENY,
                    f"Human denied: {message}",
                    estimated_cost,
                )

        # Should never reach here (default_allow catches everything)
        return PolicyDecision(PolicyAction.DENY, "No matching policy rule")

    # ── Telemetry ───────────────────────────────────────────

    def get_telemetry(self, thread_id: str) -> BudgetState | None:
        """Get budget state for a thread."""
        return self._budget_state.get(thread_id)

    def get_all_telemetry(self) -> list[BudgetState]:
        """Get all tracked budget states."""
        return list(self._budget_state.values())

    def reset_session(self, thread_id: str):
        """Reset session totals for a thread."""
        if thread_id in self._budget_state:
            self._budget_state[thread_id].session_total = 0

    def load_monthly_total(self, thread_id: str) -> int:
        """Load the monthly budget total from persistent storage."""
        now = time.localtime()
        budget_file = Path(
            os.environ.get(
                "PRISMATIC_STATE_DIR", "./prismatic_state"
            )
        ) / f"budget_{now.tm_year}_{now.tm_mon:02d}.json"
        if budget_file.exists():
            try:
                data = json.loads(budget_file.read_text())
                return data.get("monthly_total", 0)
            except (json.JSONDecodeError, OSError):
                return 0
        return 0

    def save_monthly_total(self):
        """Save the aggregate monthly total to persistent storage."""
        now = time.localtime()
        budget_dir = Path(
            os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
        )
        budget_dir.mkdir(parents=True, exist_ok=True)
        budget_file = budget_dir / f"budget_{now.tm_year}_{now.tm_mon:02d}.json"

        total = sum(s.monthly_total for s in self._budget_state.values())
        budget_file.write_text(json.dumps({"monthly_total": total, "updated": time.time()}))

    # ── Internal ────────────────────────────────────────────

    def _get_or_create_state(self, thread_id: str) -> BudgetState:
        if thread_id not in self._budget_state:
            monthly = self.load_monthly_total(thread_id)
            state = BudgetState(thread_id=thread_id, monthly_total=monthly)
            self._budget_state[thread_id] = state
        return self._budget_state[thread_id]

    def _record_cost(self, state: BudgetState, cost: int):
        state.session_total += cost
        state.monthly_total += cost


# ═══════════════════════════════════════════════════════════════
# Convenience — per-agent engine factory
# ═══════════════════════════════════════════════════════════════

_engine_cache: dict[str, CreditPolicyEngine] = {}


def get_engine_for_label(agent_label: str) -> CreditPolicyEngine:
    """Get or create a policy engine for a given Linear agent label.

    Caches engines per provider so budget state persists across
    multiple dispatcher cycles within the same process.
    """
    provider = AGENT_PROVIDER_MAP.get(agent_label, "local-llm")
    if provider not in _engine_cache:
        _engine_cache[provider] = CreditPolicyEngine(provider=provider)
    return _engine_cache[provider]


def evaluate_agent_launch(
    agent_label: str,
    issue_id: str,
    operation: str = "code_generation",
    **kwargs,
) -> PolicyDecision:
    """Evaluate whether launching an agent should be allowed.

    Args:
        agent_label: Linear agent label (e.g., "agent:agy").
        issue_id: Issue identifier used as the budget thread ID.
        operation: Type of work (code_generation, code_review, research).
        **kwargs: Additional context for policy rules.

    Returns:
        PolicyDecision with the verdict.
    """
    engine = get_engine_for_label(agent_label)
    decision = engine.evaluate(issue_id, operation, **kwargs)
    # ── Telemetry: record credit evaluation ──────────────────
    try:
        from .telemetry import get_collector
        collector = get_collector()
        collector.record_credit(
            run_id=f"policy-{agent_label}-{issue_id}",
            agent=agent_label,
            provider=engine.provider,
            credits_spent=decision.estimated_cost,
            operation=operation,
        )
    except Exception:
        pass  # Telemetry is best-effort
    # ── End telemetry ─────────────────────────────────────────
    return decision
