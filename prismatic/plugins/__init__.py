"""
prismatic/plugins/__init__.py — Plugin runtime package.

Provides the sandbox pod management layer and lifecycle state machine
for running plugins in isolated environments (Docker or k3s).

Contents
--------
* **sandbox_pod_manager.py** — SandboxPodManager: launch/stop/health for plugin pods
* **lifecycle_manager.py** — PluginLifecycleSandboxManager: state machine + lifecycle commands
"""

from __future__ import annotations

__all__ = [
    "PodState",
    "PodManagerError",
    "SandboxPodManager",
    "PluginState",
    "StateTransitionError",
    "PluginLifecycleSandboxManager",
]

from .sandbox_pod_manager import (
    PodState,
    PodManagerError,
    SandboxPodManager,
)
from .lifecycle_manager import (
    PluginState,
    StateTransitionError,
    PluginLifecycleSandboxManager,
)
