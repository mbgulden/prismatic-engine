"""
Prismatic Engine — Signal Provider Interface
=============================================

Abstract base for all signal backends. The dispatcher calls `send()`
without knowing or caring HOW the signal gets delivered — that's the
provider's job.

File nudge → HTTP webhook → Redis pub/sub → Linear comment → Telegram DM.
Same interface. Swappable backend.

Born from the /tmp/nudge-fred hack documented in:
  docs/architecture/prismatic-engine-plan.md  (Layer 2.5)
"""

from __future__ import annotations

import uuid
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class SignalAction(str, Enum):
    """What the receiving agent should do."""
    WORK = "work"        # Pick up and execute this task
    REVIEW = "review"    # Review output from another agent
    NOTIFY = "notify"    # Informational — no action required
    STOP = "stop"        # Abort current work on this task


@dataclass
class SignalPayload:
    """A unit of work dispatched to an agent.

    This is the envelope that travels across any transport.
    The provider handles serialization for its specific backend
    (JSON for HTTP, raw bytes for Redis, touch-a-file for File).
    """

    target: str                          # "fred", "kai", "agy", "autobot"
    action: SignalAction = SignalAction.WORK
    issue_id: str = ""                   # Linear GRO-xxx, GitHub #123
    title: str = ""                      # Human-readable summary
    priority: int = 3                    # 0=background .. 5=drop-everything
    metadata: dict[str, Any] = field(default_factory=dict)

    # Auto-generated — caller should NOT set these
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a transport-agnostic dict (→ JSON)."""
        d = asdict(self)
        d["action"] = self.action.value if isinstance(self.action, SignalAction) else self.action
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SignalPayload":
        """Deserialize from a transport-agnostic dict (← JSON)."""
        action = d.get("action", "work")
        if isinstance(action, str):
            action = SignalAction(action)
        return cls(
            signal_id=d.get("signal_id", str(uuid.uuid4())),
            target=d["target"],
            action=action,
            issue_id=d.get("issue_id", ""),
            title=d.get("title", ""),
            priority=d.get("priority", 3),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", time.time()),
        )

    def age_seconds(self) -> float:
        """How long ago was this signal created?"""
        return time.time() - self.created_at


class SignalProvider(ABC):
    """Abstract signal backend.

    Subclass this to add a new transport. The dispatcher doesn't know
    or care which subclass is active — it just calls send() / poll().
    """

    @abstractmethod
    def send(self, target: str, payload: SignalPayload) -> bool:
        """Push a work signal to a named agent.

        Returns True if the signal was delivered. False means the
        provider gave up; the caller should try the fallback chain.

        Must be idempotent — sending the same signal_id twice should
        not create duplicate work.
        """
        ...

    @abstractmethod
    def poll(self, target: str, timeout: float = 0) -> SignalPayload | None:
        """Check for pending signals (pull model — for cron-based agents).

        Returns the highest-priority pending signal, or None if nothing
        is waiting. timeout=0 means non-blocking.

        The caller is responsible for calling acknowledge() after
        successfully processing the returned signal.
        """
        ...

    @abstractmethod
    def acknowledge(self, signal_id: str) -> bool:
        """Mark a signal as handled so it won't be re-delivered."""
        ...

    @abstractmethod
    def list_targets(self) -> list[str]:
        """Return all known agent targets this provider can reach."""
        ...

    # ── convenience wrappers ──────────────────────────────────

    def send_work(self, target: str, issue_id: str, title: str,
                  priority: int = 3, **meta) -> bool:
        """Shorthand for the most common signal: WORK."""
        return self.send(target, SignalPayload(
            target=target,
            action=SignalAction.WORK,
            issue_id=issue_id,
            title=title,
            priority=priority,
            metadata=meta,
        ))

    def send_review(self, target: str, issue_id: str, title: str,
                    **meta) -> bool:
        """Shorthand: ask an agent to review another agent's output."""
        return self.send(target, SignalPayload(
            target=target,
            action=SignalAction.REVIEW,
            issue_id=issue_id,
            title=title,
            priority=4,
            metadata=meta,
        ))

    def send_stop(self, target: str, issue_id: str) -> bool:
        """Tell an agent to abort work on a task."""
        return self.send(target, SignalPayload(
            target=target,
            action=SignalAction.STOP,
            issue_id=issue_id,
            title=f"STOP work on {issue_id}",
            priority=5,
        ))
