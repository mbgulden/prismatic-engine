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
