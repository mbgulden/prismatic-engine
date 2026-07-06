"""Base contracts for Prismatic Engine agent harness adapters.

Harness adapters translate concrete agent runtimes (AGY CLI, Codex CLI,
Hermes, local vLLM workers, etc.) into one dispatcher-facing interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AgentHarness(ABC):
    """Interface for any agent execution harness."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable registry name for this harness."""
        raise NotImplementedError

    @property
    @abstractmethod
    def models(self) -> list[str]:
        """Model identifiers this harness can dispatch to."""
        raise NotImplementedError

    @abstractmethod
    def dispatch(self, task: dict[str, Any]) -> str:
        """Launch an agent task and return a harness-local run id."""
        raise NotImplementedError

    @abstractmethod
    def status(self, run_id: str) -> dict[str, Any]:
        """Return ``{status, started_at, completed_at, error}`` for a run."""
        raise NotImplementedError

    @abstractmethod
    def cancel(self, run_id: str) -> bool:
        """Request cancellation for a run."""
        raise NotImplementedError

    @abstractmethod
    def logs(self, run_id: str, tail: int = 100) -> list[str]:
        """Return recent log lines for a run."""
        raise NotImplementedError

    @abstractmethod
    def cost(self, run_id: str) -> dict[str, Any]:
        """Return ``{tokens_in, tokens_out, dollars}`` for a run."""
        raise NotImplementedError

    def health(self) -> dict[str, str]:
        """Return harness health. Override for custom checks."""
        return {"status": "ok", "harness": self.name}
