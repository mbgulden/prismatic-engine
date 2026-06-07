"""
Prismatic Engine — Agents Package
==================================

Agent abstractions and the factory that instantiates them.
Each agent type wraps a specific runtime (Hermes, CLI, Docker, etc.)
and presents a uniform ``execute(issue) → bool`` interface.

Usage:
    from prismatic.agents import create_agent
    agent = create_agent({"executable": "hermes", "mode": "signal"})
    success = agent.execute(issue)
"""
from __future__ import annotations

from typing import Any

from .base import BaseAgent, AgentConfig, AGENT_TYPES
from .hermes import HermesAgent

# Register built-in agent types
AGENT_TYPES["hermes"] = HermesAgent

__all__ = [
    "BaseAgent",
    "AgentConfig",
    "HermesAgent",
    "AGENT_TYPES",
    "create_agent",
]


def create_agent(config: dict[str, Any]) -> BaseAgent:
    """Factory: instantiate the right agent type from a config dict.

    Config shape::

        {
            "executable": "hermes",          # Agent type key
            "mode": "signal",                # "signal" | "direct" | "subprocess"
            "timeout": 300,                  # Max execution seconds
            "next_label": "pipeline:review", # Label applied after completion
            # ... type-specific options
        }

    The ``executable`` field maps to a class registered in ``AGENT_TYPES``.

    Raises ValueError for unknown agent types.
    """
    agent_type = config.get("executable", "hermes")
    cls = AGENT_TYPES.get(agent_type)

    if cls is None:
        raise ValueError(
            f"Unknown agent type: {agent_type!r}. "
            f"Known types: {list(AGENT_TYPES.keys())}"
        )

    agent_config = AgentConfig(
        executable=agent_type,
        mode=config.get("mode", "signal"),
        timeout=config.get("timeout", 300),
        next_label=config.get("next_label"),
    )

    return cls(config=agent_config, agent_config=config)
