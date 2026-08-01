"""
Prismatic Engine — Capability Registry
=====================================

Defines the core capability registry and default capability contracts.
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Tuple


class Capability:
    """Represents a registered capability contract in the Prismatic Engine."""

    def __init__(self, name: str, check_fn: Callable[[], Tuple[bool, str]]):
        self.name = name
        self.check_fn = check_fn

    def check_status(self) -> Tuple[bool, str]:
        """Verify if the capability requirements (credentials, config, etc.) are met."""
        try:
            return self.check_fn()
        except Exception as e:
            return False, str(e)


class CapabilityRegistry:
    """Registry for managing and querying engine capabilities."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, Capability] = {}

    def register(self, name: str, check_fn: Callable[[], Tuple[bool, str]]) -> None:
        """Register a new capability with a validation function."""
        self._capabilities[name] = Capability(name, check_fn)

    def get(self, name: str) -> Capability | None:
        """Retrieve a registered capability by name."""
        return self._capabilities.get(name)

    def list_all(self) -> list[Capability]:
        """List all registered capabilities."""
        return list(self._capabilities.values())


# Global registry instance
registry = CapabilityRegistry()


# ── Default Capability Check Functions ─────────────────────────────────

def check_linear() -> Tuple[bool, str]:
    if os.environ.get("LINEAR_API_KEY"):
        return True, "ok"
    return False, "missing LINEAR_API_KEY env var"


def check_vcs_github() -> Tuple[bool, str]:
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("PRISMATIC_GITHUB_TOKEN"):
        return True, "ok"
    return False, "missing GITHUB_TOKEN / GH_TOKEN env var"


def check_agy() -> Tuple[bool, str]:
    if os.environ.get("AGY_TOKEN"):
        return True, "ok"
    return False, "missing AGY_TOKEN env var"


def check_jules() -> Tuple[bool, str]:
    # Jules CLI review/handoff capability - default to ok for skeleton
    return True, "ok"


def check_telegram() -> Tuple[bool, str]:
    if os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("PRISMATIC_TELEGRAM_BOT_TOKEN"):
        return True, "ok"
    return False, "missing TELEGRAM_BOT_TOKEN env var"


def check_schedule() -> Tuple[bool, str]:
    # SQLite/JSON schedule tracking is engine-local, default to ok
    return True, "ok"

def check_chat_agy() -> Tuple[bool, str]:
    """Check whether the AGY chat capability is reachable.

    Delegates to ``ChatAGYCapability.check_status()`` so the registry
    contract stays consistent with the live probe. We lazy-import the
    capability module here to avoid an import cycle: chat_agy.py does
    not import from registry, so this is the safe direction.
    """
    try:
        from prismatic.capabilities.chat_agy import ChatAGYCapability
        cap = ChatAGYCapability()
        return cap.check_status()
    except Exception as exc:
        return False, f"chat.agy probe failed: {exc}"

def check_artifact() -> Tuple[bool, str]:
    # Artifact publishing capability, default to ok
    return True, "ok"


# Register default capabilities
registry.register("linear", check_linear)
registry.register("vcs.github", check_vcs_github)
registry.register("agy", check_agy)
registry.register("jules", check_jules)
registry.register("telegram", check_telegram)
registry.register("schedule", check_schedule)
registry.register("chat.agy", check_chat_agy)
registry.register("artifact", check_artifact)
