"""
Prismatic Engine — Base Agent Interface
========================================

Every agent in the swarm implements this interface.  The coordinator
calls ``execute(issue)`` and doesn't care whether the agent runs
locally, in Docker, on a remote VM, or as a Hermes sub-agent.

The ``AGENT_TYPES`` registry lets the factory and bolt-on marketplace
discover available agent implementations at runtime.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from prismatic.providers.tasks.base import Issue


# ── Agent type registry ────────────────────────────────────────
# Populated by agent implementations calling ``AGENT_TYPES[name] = cls``
AGENT_TYPES: dict[str, type] = {}


@dataclass
class AgentConfig:
    """Configuration passed to every agent instance.

    This is the subset of the agent config dict that Prismatic Engine
    understands natively.  Agent-specific options (API keys, model names,
    workspace paths) are passed through the raw ``agent_config`` dict.
    """
    executable: str = "hermes"       # Agent type key (matches AGENT_TYPES)
    mode: str = "signal"             # "signal" | "direct" | "subprocess"
    timeout: int = 300               # Max wall-clock seconds for execute()
    next_label: str | None = None    # Label applied after successful execution
    options: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract agent that can execute a task from an issue tracker.

    Subclasses implement ``execute()`` — the one method the coordinator
    calls.  ``get_id()`` returns a stable identifier for logging, signals,
    and deduplication.
    """

    def __init__(self, config: AgentConfig, agent_config: dict[str, Any] | None = None):
        self._config = config
        self._agent_config = agent_config or {}

    @property
    def config(self) -> AgentConfig:
        """Return the normalized agent configuration."""
        return self._config

    @abstractmethod
    def execute(self, issue: Issue) -> bool:
        """Execute work described by the given issue.

        Args:
            issue: The work item to execute.

        Returns:
            True if the agent completed successfully, False on failure.
        """
        ...

    @abstractmethod
    def get_id(self) -> str:
        """Return a stable unique identifier for this agent instance.

        Used for signal routing, logging, and deduplication.
        """
        ...
