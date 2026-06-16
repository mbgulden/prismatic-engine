"""
prismatic/core/events.py — Cross-project pipeline trigger event schema.

Defines the canonical event data model for cross-repository pipeline
trigger events.  When a merge, push, or PR-close occurs on one repo,
a ``PipelineTriggerEvent`` is created and fed into the dispatcher's
trigger interface, which calculates affected targets and enqueues
downstream validation runs.

Usage::

    from prismatic.core.events import PipelineTriggerEvent, EventType

    event = PipelineTriggerEvent(
        source_repo="hd-engine",
        source_branch="staging-hd",
        event_type=EventType.MERGE,
        metadata={"commit_sha": "abc123"},
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Canonical event types recognised by the pipeline trigger system."""

    PUSH = "push"
    """A direct push to a tracked branch."""

    MERGE = "merge"
    """A PR merge into a tracked branch."""

    PR_CLOSED = "pr_closed"
    """A pull request was closed (merged or declined)."""

    WEBHOOK = "webhook"
    """An external webhook notification (generic catch-all)."""

    MANUAL = "manual"
    """A manually initiated trigger (admin CLI, UI button)."""


@dataclass
class PipelineTriggerEvent:
    """
    A cross-project pipeline trigger event.

    Each instance represents a single occurrence of a source-repository
    action that *may* trigger downstream pipeline runs in other repos.

    Parameters
    ----------
    source_repo:
        The repository where the event originated (short name, e.g.
        ``"hd-engine"``).
    source_branch:
        The branch on which the event occurred (e.g. ``"staging-hd"``).
    event_type:
        Canonical type of the event (see :class:`EventType`).
    timestamp:
        When the event occurred.  Defaults to ``datetime.now(timezone.utc)``.
    event_id:
        Unique identifier for deduplication.  Auto-generated as a UUID4
        if not provided.
    metadata:
        Arbitrary key-value payload (commit SHA, PR number, author, …).
    """

    source_repo: str
    source_branch: str
    event_type: EventType

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Computed helpers ──────────────────────────────────────────────

    @property
    def age_seconds(self) -> float:
        """Seconds since this event was created."""
        return (datetime.now(timezone.utc) - self.timestamp).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        return {
            "event_id": self.event_id,
            "source_repo": self.source_repo,
            "source_branch": self.source_branch,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineTriggerEvent:
        """Deserialise from a dictionary (inverse of :meth:`to_dict`)."""
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            source_repo=data["source_repo"],
            source_branch=data["source_branch"],
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
        )
