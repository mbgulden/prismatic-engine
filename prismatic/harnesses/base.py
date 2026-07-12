"""Base contracts for Prismatic Engine agent harness adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HarnessStatus(str, Enum):
    """Normalized lifecycle states returned by harness adapters."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HarnessCapabilities:
    """Machine-readable capabilities for a harness adapter."""

    streaming_logs: bool = False
    cost_tracking: bool = False
    concurrent_runs: int = 1
    supports_cancel: bool = True
    supports_timeout: bool = True
    max_context_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AgentHarness(ABC):
    """Abstract base class for agent-runtime adapters."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable harness adapter name."""
        raise NotImplementedError

    @property
    @abstractmethod
    def models(self) -> list[str]:
        """Model/runtime identifiers supported by this harness."""
        raise NotImplementedError

    @abstractmethod
    def dispatch(self, task: dict[str, Any]) -> str:
        """Submit a task and return a harness-local run id."""
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
        """Return a conservative default health payload."""
        return {"status": "ok", "harness": self.name}

    def capabilities(self) -> HarnessCapabilities:
        """Return conservative default capabilities."""
        return HarnessCapabilities()
