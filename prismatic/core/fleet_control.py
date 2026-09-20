"""Fleet Control & Bidirectional Steering Manager for Prismatic Engine.

Manages fleet-wide and per-agent pause states, emergency evictions, and operator steering channels.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.core.fleet_control")


@dataclass
class FleetControlState:
    fleet_paused: bool = False
    paused_agents: set[str] = field(default_factory=set)
    active_nudges: list[dict[str, Any]] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fleet_paused": self.fleet_paused,
            "paused_agents": sorted(list(self.paused_agents)),
            "active_nudges": self.active_nudges[-20:],
            "last_updated": self.last_updated,
        }


class FleetControlManager:
    _instance: FleetControlManager | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.state = FleetControlState()

    @classmethod
    def get_instance(cls) -> FleetControlManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def pause(self, agent_id: str | None = None, reason: str = "operator_pause") -> dict[str, Any]:
        """Pause the entire fleet or a specific agent."""
        with self._lock:
            if agent_id and agent_id != "all":
                self.state.paused_agents.add(agent_id.lower())
            else:
                self.state.fleet_paused = True
            self.state.last_updated = time.time()
            return self.state.to_dict()

    def resume(self, agent_id: str | None = None) -> dict[str, Any]:
        """Resume the entire fleet or a specific agent."""
        with self._lock:
            if agent_id and agent_id != "all":
                self.state.paused_agents.discard(agent_id.lower())
            else:
                self.state.fleet_paused = False
                self.state.paused_agents.clear()
            self.state.last_updated = time.time()
            return self.state.to_dict()

    def is_paused(self, agent_id: str | None = None) -> bool:
        """Check if an agent or the fleet is currently paused."""
        with self._lock:
            if self.state.fleet_paused:
                return True
            if agent_id and agent_id.lower() in self.state.paused_agents:
                return True
            return False

    def add_nudge(self, agent: str, message: str, source: str = "operator") -> dict[str, Any]:
        """Record an operator guidance prompt."""
        with self._lock:
            nudge = {
                "agent": agent,
                "message": message,
                "source": source,
                "timestamp": time.time(),
                "time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.state.active_nudges.append(nudge)
            self.state.last_updated = time.time()
            return nudge

    def get_status(self) -> dict[str, Any]:
        """Return snapshot of control state."""
        with self._lock:
            return self.state.to_dict()
