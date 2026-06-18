"""
Prismatic Engine — Chat (typed session shapes)
==============================================

Provides the typed ``ChatSession`` dataclass used by the AGY chat
capability and the gateway. This package contains only typing
helpers; no engine behavior lives here.

Future panes (local AI GPU agents, Hermes agents) that need the same
``ChatSession`` shape import from this module. The dataclass is the
contract; implementation is per-provider in the capabilities package.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Optional


@dataclass
class ChatSession:
    """Typed shape for a chat session.

    Shared across panes. Provider-specific subclasses (e.g.
    ``AGYChatSession``) can extend this with provider-specific
    fields without breaking the base contract.
    """

    id: str
    agent: str
    status: str
    started_at: str
    last_event_at: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in ("id", "agent", "status", "started_at")}


__all__ = ["ChatSession"]
