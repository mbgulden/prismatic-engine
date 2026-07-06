"""Base contracts for Prismatic Engine runtime plugins.

Plugins are intentionally small Python objects. The engine owns discovery,
instantiation, and isolation; plugin classes expose metadata, lifecycle hooks,
event subscriptions, optional FastAPI routes, and Hub panel metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PrismaticPlugin(ABC):
    """Abstract base class every Prismatic runtime plugin must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable machine-readable plugin name."""
        raise NotImplementedError

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic plugin version string."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable summary shown in operator surfaces."""
        raise NotImplementedError

    def on_install(self, engine: Any) -> None:
        """Run once when the plugin is installed into an engine instance."""
        return None

    def on_enable(self, engine: Any) -> None:
        """Run when the plugin is enabled."""
        return None

    def on_disable(self, engine: Any) -> None:
        """Run when the plugin is disabled."""
        return None

    def subscriptions(self) -> list[str]:
        """Return event names this plugin wants to receive."""
        return []

    def handle_event(self, event: dict[str, Any]) -> None:
        """Handle an engine event delivered through the plugin bus."""
        return None

    def routes(self) -> list[Any]:
        """Return FastAPI routers or route callables exposed by this plugin."""
        return []

    def hub_panel(self) -> dict[str, Any]:
        """Return optional Hub UI panel metadata."""
        return {}
