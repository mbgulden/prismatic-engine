"""
Prismatic Engine — agent orchestration hub powered by Linear labels.
=====================================================================

Turn Linear labels into a pipeline orchestrator. Assign a label like
``pipeline::dev-agency`` to an issue and watch agents march through
the pipeline in sequence — each agent picks up, executes, and hands
off via label transitions.

Core subsystems:
  - **dispatcher**  — event loop that polls Linear, routes work, launches agents
  - **router**      — label-based pipeline detection and context formatting
  - **signals**     — transport layer for agent nudges (file, HTTP, Redis)
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__", "main", "SandboxManager", "SandboxConfig", "Sandbox", "get_sandbox_manager"]

from .dispatcher import main
from .sandbox import SandboxManager, SandboxConfig, Sandbox, get_sandbox_manager
