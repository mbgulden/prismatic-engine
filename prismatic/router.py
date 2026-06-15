"""
Prismatic Engine — Label-based Routing Engine
===============================================

Generic pipeline routing: detect pipeline type from Linear issue labels,
apply pipeline labels, and format pipeline context. No hardcoded paths
or agent-specific logic — this is the pure routing layer.

Pipeline templates are loaded from a YAML or JSON file and describe
agent chains as ordered lists of label transitions.

Example pipeline template (YAML):

.. code-block:: yaml

    pipelines:
      dev-agency:
        name: "Dev Agency"
        description: "Full agency pipeline: Fred → Kai → AGY → Codex"
        chain:
          - agent: fred
            label: "agent::fred"
            next_label: "agent::kai"
          - agent: kai
            label: "agent::kai"
            next_label: "agent::agy"
          - agent: agy
            label: "agent::agy"
            next_label: "agent::codex"
          - agent: codex
            label: "agent::codex"
            next_label: ""  # terminal
        triggers:
          - "pipeline::dev-agency"
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ── Pipeline template loading ──────────────────────────────────


def load_pipeline_templates(config_path: str | Path) -> dict[str, Any]:
    """Load pipeline definitions from a YAML or JSON file.

    Args:
        config_path: Path to the pipeline config file (.yaml, .yml, or .json).

    Returns:
        A dict with at least a ``"pipelines"`` key mapping pipeline IDs
        (e.g. ``"dev-agency"``) to their template definitions.

    Raises:
        FileNotFoundError: If *config_path* does not exist.
        ValueError: If the file format is not recognised.
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {path}")

    raw = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        import yaml  # lazy import — pyyaml is an optional dep at runtime

        pipelines = yaml.safe_load(raw)

    elif path.suffix == ".json":
        pipelines = json.loads(raw)

    else:
        raise ValueError(
            f"Unsupported pipeline config format: {path.suffix}. "
            "Use .yaml, .yml, or .json."
        )

    if not isinstance(pipelines, dict) or "pipelines" not in pipelines:
        # Support both {"pipelines": {...}} and {...} directly
        if isinstance(pipelines, dict) and any(
            isinstance(v, dict) and "chain" in v for v in pipelines.values()
        ):
            pipelines = {"pipelines": pipelines}
        else:
            pipelines = {"pipelines": pipelines.get("pipelines", {})}

    return pipelines


# ── Pipeline detection ─────────────────────────────────────────


def detect_pipeline_type(
    issue: dict[str, Any],
    pipelines: dict[str, Any],
) -> str | None:
    """Auto-detect the pipeline type for an issue.

    Detection order:
      1. Pipeline-trigger labels (``pipeline::<name>``) on the issue.
      2. Keyword match on the issue title or description against a
         pipeline's ``triggers`` list (case-insensitive).

    Args:
        issue: Linear issue dict. Expected keys:
            ``labels`` (list of label-name strings),
            ``title`` (str),
            ``description`` (str or None).
        pipelines: Pipeline config dict (from
            :func:`load_pipeline_templates`).

    Returns:
        Pipeline ID (e.g. ``"dev-agency"``) or ``None`` if no match.
    """
    pipes = pipelines.get("pipelines", {})

    if not pipes:
        return None

    labels = [
        lab.get("name", lab) if isinstance(lab, dict) else lab
        for lab in issue.get("labels", [])
    ]
    title = (issue.get("title") or "") + " " + (issue.get("description") or "")

    # 1. Direct label trigger (e.g. label "pipeline::dev-agency")
    for label in labels:
        match = re.match(r"^pipeline::(\S+)$", label, re.IGNORECASE)
        if match:
            pid = match.group(1).lower()
            if pid in pipes:
                return pid

    # 2. Keyword triggers in title/description
    title_lower = title.lower()
    for pid, pipe in pipes.items():
        for trigger in pipe.get("triggers", []):
            if trigger.lower() in title_lower:
                return pid

    return None


# ── Pipeline application ───────────────────────────────────────


def apply_pipeline(
    issue_id: str,
    pipeline_type: str,
    pipelines: dict[str, Any],
    *,
    linear_api: Any = None,
) -> dict[str, Any] | None:
    """Set the first agent label on an issue and append pipeline
    context to the description.

    Args:
        issue_id: Linear issue ID (UUID, not number).
        pipeline_type: Pipeline ID (e.g. ``"dev-agency"``).
        pipelines: Pipeline config dict.
        linear_api: Optional Linear API helper with a
            ``mutation(query, variables)`` method. If provided, the
            label mutation and description update are applied remotely.
            If ``None``, returns the mutation payload as a dict for
            the caller to apply.

    Returns:
        Dict of the mutations to apply, or the Linear API response
        if *linear_api* was provided.
    """
    pipes = pipelines.get("pipelines", {})
    pipe = pipes.get(pipeline_type)

    if not pipe:
        raise ValueError(f"Unknown pipeline type: {pipeline_type!r}")

    chain = pipe.get("chain", [])
    if not chain:
        raise ValueError(f"Pipeline {pipeline_type!r} has an empty chain")

    first_label = chain[0].get("label", "")
    pipeline_label = f"pipeline::{pipeline_type}"

    # Build pipeline context block to append to description
    context_block = format_pipeline_context(chain)
    context_mutation = (
        f"\\n\\n---\\n**Pipeline: {pipe.get('name', pipeline_type)}**\\n"
        f"{context_block}"
    )

    mutation_payload = {
        "query": """
            mutation ApplyPipeline(
                $issueId: String!,
                $labelId: String!,
                $pipelineLabelId: String!,
                $description: String!
            ) {
                issueUpdate(
                    id: $issueId,
                    input: {
                        labelIds: {set: [$labelId, $pipelineLabelId]},
                        description: $description
                    }
                ) {
                    success
                    issue { id title }
                }
            }
        """,
        "variables": {
            "issueId": issue_id,
            "labelId": first_label,
            "pipelineLabelId": pipeline_label,
            "description": context_mutation,
        },
    }

    if linear_api is not None:
        return linear_api.mutation(**mutation_payload)

    return mutation_payload


# ── Context formatting ─────────────────────────────────────────


def format_pipeline_context(chain: list[dict[str, Any]]) -> str:
    """Format a pipeline chain as Markdown for embedding in an issue
    description.

    Args:
        chain: Ordered list of pipeline steps. Each step is a dict
            with keys ``agent``, ``label``, ``next_label`` (optional),
            and optionally ``name``.

    Returns:
        Markdown string showing the pipeline flow.
    """
    if not chain:
        return "_Empty pipeline_"

    lines: list[str] = []
    for i, step in enumerate(chain):
        agent = step.get("agent", f"step-{i}")
        name = step.get("name", agent.capitalize())
        label = step.get("label", "")
        next_label = step.get("next_label", "")

        arrow = (
            f" → `{next_label}`"
            if next_label
            else " → ✅ *Done*"
        )

        lines.append(
            f"{i + 1}. **{name}** (`{label}`){arrow}"
        )

    return "\n" + "\n".join(lines) + "\n"


# ── Dynamic Fallback Router ──────────────────────────────────


class DynamicFallbackRouter:
    """Circuit-breaker-aware dynamic fallback router.

    Wraps the TelemetryCollector circuit breaker to make routing decisions.
    When a model/provider has a tripped breaker, the router selects the
    next working fallback from a prioritized chain.

    Fallback chains are defined as ordered lists — the first entry is the
    primary model, subsequent entries are fallbacks tried in order.
    """

    # ── Default fallback chains ──────────────────────────────
    # Format: {agent: {role_or_model: [fallback1, fallback2, ...]}}
    DEFAULT_CHAINS: dict[str, dict[str, list[str]]] = {
        "agy": {
            "researcher": ["deepseek-v4-pro", "deepseek-v4-flash", "gpt-4o-mini"],
            "builder":   ["deepseek-v4-flash", "gpt-4o-mini"],
        },
        "fred": {
            "primary":   ["deepseek-v4-flash", "gpt-4o-mini"],
        },
        "kai": {
            "developer": ["deepseek-v4-flash", "gpt-4o-mini"],
            "css":       ["deepseek-v4-flash"],
            "js":        ["deepseek-v4-flash"],
            "content":   ["deepseek-v4-flash"],
        },
        "jules": {
            "primary":   ["deepseek-v4-flash", "gpt-4o-mini"],
        },
        "codex": {
            "primary":   ["gpt-4o", "gpt-4o-mini"],
        },
        "claude-code": {
            "primary":   ["claude-sonnet-4-20250514", "claude-haiku-3-20250301"],
        },
    }

    def __init__(
        self,
        telemetry_collector,
        fallback_chains: dict[str, dict[str, list[str]]] | None = None,
    ):
        """Initialize the fallback router.

        Args:
            telemetry_collector: Instance of TelemetryCollector for
                circuit breaker checks.
            fallback_chains: Optional override of fallback chains.
                Falls back to DEFAULT_CHAINS if not provided.
        """
        self._telemetry = telemetry_collector
        if fallback_chains is None:
            self._chains = dict(self.DEFAULT_CHAINS)
        else:
            self._chains = fallback_chains
        self._fallback_count: int = 0
        self._total_checks: int = 0

    # ── Public routing API ───────────────────────────────────

    def select_route(
        self,
        issue_id: str,
        agent: str,
        role: str = "primary",
        *,
        micro_cost: int = 1,
        macro_cost: int = 0,
    ) -> dict[str, str | None | bool]:
        """Select a working route (agent + model) for an issue.

        Checks the circuit breaker for the primary model of *agent*'s
        *role*. If tripped, walks the fallback chain until a working
        model is found or the chain is exhausted.

        Args:
            issue_id: Linear issue ID for breaker tracking.
            agent: Agent name (e.g. ``"agy"``).
            role: Role within the agent (e.g. ``"researcher"``,
                ``"primary"``). Defaults to ``"primary"``.
            micro_cost: Micro-failure increment (default 1 — one
                dispatch attempt).
            macro_cost: Macro-failure increment (default 0).

        Returns:
            A dict with keys ``"agent"``, ``"model"``, and
            ``"fallback"`` (bool). ``"model"`` is ``None`` if all
            fallbacks are exhausted.
        """
        chain = self._get_chain(agent, role)
        if not chain:
            return {"agent": agent, "model": None, "fallback": False}

        for model in chain:
            self._total_checks += 1
            tripped = self._telemetry.check_circuit(
                issue_id=issue_id,
                agent=agent,
                micro_count=micro_cost,
                macro_count=macro_cost,
            )
            if not tripped:
                is_fallback = model != chain[0]
                if is_fallback:
                    self._fallback_count += 1
                return {
                    "agent": agent,
                    "model": model,
                    "fallback": is_fallback,
                }

        # All fallbacks tripped — report exhausted
        self._telemetry.record_loop(
            run_id=f"exhausted-{issue_id}",
            issue_id=issue_id,
            agent=agent,
            loop_type="fallback_exhausted",
            trigger=f"all-fallbacks-exhausted-for-{role}",
            resolved=False,
        )
        return {"agent": agent, "model": None, "fallback": False}

    def report_success(
        self,
        issue_id: str,
        agent: str,
    ) -> None:
        """Report a successful dispatch, resetting the circuit breaker.

        Call this when an agent completes its work successfully for an
        issue. It resets the breaker so future dispatches start clean.

        Args:
            issue_id: Linear issue ID.
            agent: Agent name.
        """
        self._telemetry.reset_breaker(issue_id)
        self._telemetry.record_loop(
            run_id=f"reset-{issue_id}",
            issue_id=issue_id,
            agent=agent,
            loop_type="breaker_reset",
            trigger="dispatch_success",
            resolved=True,
        )

    def report_failure(
        self,
        issue_id: str,
        agent: str,
        model: str,
        *,
        micro_cost: int = 1,
        macro_cost: int = 0,
    ) -> dict[str, str | None | bool]:
        """Report a dispatch failure and attempt fallback.

        Records the failure via the circuit breaker, then automatically
        attempts fallback to the *next* model in the chain (skipping the
        one that just failed).

        Args:
            issue_id: Linear issue ID.
            agent: Agent name.
            model: The model that failed.
            micro_cost: Micro-failure increment.
            macro_cost: Macro-failure increment (default 0 — use 1 for
                catastrophic failures like 5xx errors).

        Returns:
            A fallback route dict (same shape as :meth:`select_route`),
            or ``{"model": None}`` if no fallback is available.
        """
        # Record the failure via circuit breaker
        self._total_checks += 1
        tripped = self._telemetry.check_circuit(
            issue_id=issue_id,
            agent=agent,
            micro_count=micro_cost,
            macro_count=macro_cost,
        )

        chain = self._get_chain(agent, "primary")

        if tripped and chain:
            # Find the failed model's position and try the next one
            for i, m in enumerate(chain):
                if m == model:
                    # Try remaining models in the chain
                    for next_model in chain[i + 1:]:
                        self._total_checks += 1
                        next_tripped = self._telemetry.check_circuit(
                            issue_id=issue_id,
                            agent=agent,
                            micro_count=0,  # already counted
                            macro_count=0,
                        )
                        if not next_tripped:
                            self._fallback_count += 1
                            return {
                                "agent": agent,
                                "model": next_model,
                                "fallback": True,
                            }
                    break

            # No fallback found — report exhausted
            self._telemetry.record_loop(
                run_id=f"exhausted-{issue_id}",
                issue_id=issue_id,
                agent=agent,
                loop_type="fallback_exhausted",
                trigger=f"report_failure-no-fallback-after-{model}",
                resolved=False,
            )
            return {"agent": agent, "model": None, "fallback": False}

        # Model not yet tripped — retry is still safe
        return {"agent": agent, "model": model, "fallback": False}

    # ── Status / diagnostics ────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        """Return routing statistics."""
        return {
            "total_checks": self._total_checks,
            "fallbacks_activated": self._fallback_count,
        }

    def get_chain(
        self,
        agent: str,
        role: str = "primary",
    ) -> list[str]:
        """Get the fallback chain for an agent role.

        Returns:
            Ordered list of model names, or empty list if no chain
            is configured.
        """
        return list(self._get_chain(agent, role))

    # ── Private ──────────────────────────────────────────────

    def _get_chain(self, agent: str, role: str) -> list[str]:
        """Resolve the fallback chain for *agent* and *role*."""
        agent_chains = self._chains.get(agent)
        if not agent_chains:
            return []
        chain = agent_chains.get(role) or agent_chains.get("primary")
        return chain or []

    def __repr__(self) -> str:
        return (
            f"<DynamicFallbackRouter "
            f"checks={self._total_checks} "
            f"fallbacks={self._fallback_count}>"
        )
