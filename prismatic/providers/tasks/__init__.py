"""
Prismatic Engine — Task Providers
==================================

Task providers bridge the issue tracker (Linear, GitHub, Jira, etc.)
with the Prismatic Engine.  The coordinator calls `get_issues_with_label()`
to discover new work, then dispatches signals to the appropriate agent.

Usage:
    from prismatic.providers.tasks import create_task_provider
    provider = create_task_provider({"type": "linear", "team_id": "..."})
    issues = provider.get_issues_with_label("pipeline:hermes")
"""
from __future__ import annotations

from typing import Any

from .base import TaskProvider, Issue
from .linear import LinearTaskProvider

__all__ = [
    "TaskProvider",
    "Issue",
    "LinearTaskProvider",
    "create_task_provider",
]


def create_task_provider(config: dict[str, Any]) -> TaskProvider:
    """Factory: instantiate the right TaskProvider from a config dict.

    Config shape:
        { "type": "linear", "team_id": "GRO" }

    Raises ValueError for unknown provider types.
    """
    provider_type = config.get("type", "linear")

    if provider_type == "linear":
        return LinearTaskProvider(
            team_id=config.get("team_id"),
        )

    raise ValueError(f"Unknown task provider type: {provider_type}")
