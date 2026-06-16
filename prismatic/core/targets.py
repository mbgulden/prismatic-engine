"""
prismatic/core/targets.py — Affected target calculation stub.

Determines which downstream repositories and branches are affected by
a given :class:`~prismatic.core.events.PipelineTriggerEvent`, based on
the pipeline definitions in ``pipelines.yaml``.

This is a **stub** — the current implementation performs a simple
pipeline-config lookup.  Future iterations will add dependency-graph
traversal, glob-based path matching, and transitive cascade detection.

Usage::

    from prismatic.core.events import PipelineTriggerEvent, EventType
    from prismatic.core.targets import calculate_affected_targets

    event = PipelineTriggerEvent(
        source_repo="hd-engine",
        source_branch="staging-hd",
        event_type=EventType.MERGE,
    )
    config = {"pipelines": {"my-pipeline": ...}}
    targets = calculate_affected_targets(event, config)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from prismatic.core.events import PipelineTriggerEvent

logger = logging.getLogger("prismatic.core.targets")


# ---------------------------------------------------------------------------
# Affected target data model
# ---------------------------------------------------------------------------


@dataclass
class AffectedTarget:
    """
    A single downstream target that should be triggered by a pipeline event.

    Parameters
    ----------
    target_repo:
        Repository short name (e.g. ``"beyondsaas"``).
    target_branch:
        Branch to run against (e.g. ``"main"``).
    pipeline_name:
        Name of the pipeline that matched this target.
    priority:
        Execution priority (higher = more urgent).  Default ``0``.
    reason:
        Human-readable justification for why this target was selected.
    metadata:
        Additional context propagated from the source event.
    """

    target_repo: str
    target_branch: str
    pipeline_name: str
    priority: int = 0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stub implementation
# ---------------------------------------------------------------------------


def calculate_affected_targets(
    event: PipelineTriggerEvent,
    pipelines_config: dict[str, Any],
) -> list[AffectedTarget]:
    """
    Calculate which downstream targets are affected by *event*.

    This is a **minimal stub** suitable for the Phase-1 scope.  It:

    1. Iterates over every pipeline defined in *pipelines_config*.
    2. Calls :func:`_pipeline_matches_event` to check whether the
       pipeline's source-repo / branch / event-type filter applies.
    3. For matching pipelines, creates one :class:`AffectedTarget` per
       downstream chain step that follows the matching source agent.

    Parameters
    ----------
    event:
        The source trigger event.
    pipelines_config:
        The parsed ``pipelines.yaml`` content (a dict with a top-level
        ``"pipelines"`` key).  Each pipeline entry has a ``chain`` list
        of step objects with ``agent``/``next_label`` fields.

    Returns
    -------
    list[AffectedTarget]
        Zero or more downstream targets to enqueue.
    """
    targets: list[AffectedTarget] = []

    pipelines = pipelines_config.get("pipelines", {})
    if not pipelines:
        logger.debug("No pipelines defined in config — no targets to calculate.")
        return targets

    for pipeline_name, pipeline_def in pipelines.items():
        chain = pipeline_def.get("chain", [])
        if not chain:
            continue

        # Check whether this pipeline matches the event
        if not _pipeline_matches_event(event, pipeline_def):
            continue

        # For matching pipelines, find the downstream step(s) after
        # the source repo's corresponding agent.
        # Stub heuristic: take the *next* step(s) in the chain as the
        # affected targets.
        downstream = _find_downstream_targets(
            event, pipeline_name, chain
        )
        targets.extend(downstream)

    logger.info(
        "calculate_affected_targets(%s/%s) → %d targets",
        event.source_repo,
        event.source_branch,
        len(targets),
    )
    return targets


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _pipeline_matches_event(
    event: PipelineTriggerEvent,
    pipeline_def: dict[str, Any],
) -> bool:
    """
    Determine whether *pipeline_def* matches the given *event*.

    Stub heuristic:
    - If the pipeline's ``triggers`` list contains the event's
      ``source_repo`` name (case-insensitive), it's a match.
    - Otherwise, match by ``event_type`` against any trigger keyword.

    This is intentionally simple — a full implementation would use
    glob patterns, regex, or explicit source-repo filters.
    """
    triggers: list[str] = pipeline_def.get("triggers", [])
    if not triggers:
        # No triggers defined — don't match anything by default
        return False

    # Stub: repo name match
    if event.source_repo.lower() in [t.lower() for t in triggers]:
        return True

    # Stub: event type keyword match
    if event.event_type.value.lower() in [t.lower() for t in triggers]:
        return True

    return False


def _find_downstream_targets(
    event: PipelineTriggerEvent,
    pipeline_name: str,
    chain: list[dict[str, Any]],
) -> list[AffectedTarget]:
    """
    Find downstream targets in *chain* triggered by *event*.

    Stub heuristic:
    - Find the step whose ``agent`` matches ``event.source_repo``
      (or whose ``label`` contains the repo name).
    - Return all *subsequent* chain steps as affected targets.

    If no matching source step is found, return the first chain step
    as a fallback.
    """
    source_idx = _find_source_step_index(event, chain)

    if source_idx is None or source_idx >= len(chain) - 1:
        # No downstream steps — nothing to trigger
        return []

    # Everything after the source step is a downstream target
    downstream: list[AffectedTarget] = []
    for step in chain[source_idx + 1:]:
        agent = step.get("agent", "unknown")
        target = AffectedTarget(
            target_repo=agent,
            target_branch="main",
            pipeline_name=pipeline_name,
            priority=len(chain) - chain.index(step),  # earlier steps = higher priority
            reason=(
                f"Triggered by {event.event_type.value} "
                f"on {event.source_repo}/{event.source_branch}"
            ),
            metadata={"event_id": event.event_id, "step": step},
        )
        downstream.append(target)

    return downstream


def _find_source_step_index(
    event: PipelineTriggerEvent,
    chain: list[dict[str, Any]],
) -> int | None:
    """
    Find the chain step that corresponds to *event*'s source repo.

    Stub: matches ``agent`` field or ``label`` field against the
    source repo name.
    """
    for idx, step in enumerate(chain):
        agent = (step.get("agent") or "").lower()
        label = (step.get("label") or "").lower()
        repo = event.source_repo.lower()
        if repo in agent or repo in label:
            return idx
    return None
