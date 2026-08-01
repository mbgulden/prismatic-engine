"""
Prismatic Engine — Hermes Agent Adapter
========================================

Default agent runtime when Prismatic Engine runs as a sub-agent inside
Hermes.  Uses ``SignalProvider`` to send nudge files to the local
Hermes process, then waits for completion.

Workspace context is resolved from the filesystem — the agent checks
common Hermes workspace locations so sub-agents can find their context
files without hard-coded paths.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from prismatic.providers.signals import (
    SignalProvider,
    SignalPayload,
    SignalAction,
    create_signal_provider,
)
from prismatic.providers.tasks.base import Issue
from .base import BaseAgent, AgentConfig


class HermesAgent(BaseAgent):
    """Agent that delegates execution to a local Hermes process.

    Signal flow::

        1. Engine calls execute(issue)
        2. HermesAgent creates a SignalPayload with WORK action
        3. Sends it via the configured SignalProvider (default: file)
        4. Polls for completion (timeout-based)
        5. Returns True/False

    The agent name defaults to ``"hermes"`` but can be overridden in
    the agent config via the ``target`` key.
    """

    def __init__(
        self,
        config: AgentConfig,
        agent_config: dict[str, Any] | None = None,
        signal_provider: SignalProvider | None = None,
    ):
        super().__init__(config, agent_config)
        self._signal_provider = signal_provider or self._default_signal_provider()
        self._target = agent_config.get("target", "hermes") if agent_config else "hermes"

    @staticmethod
    def _default_signal_provider() -> SignalProvider:
        """Build a FileSignalProvider pointing at the default nudge directory."""
        return create_signal_provider({
            "type": "file",
            "directory": os.environ.get("PRISMATIC_NUDGE_DIR", "/tmp/prismatic"),
        })

    def execute(self, issue: Issue) -> bool:
        """Send a work signal to the Hermes agent and wait for completion.

        Builds workspace context from the filesystem, then sends a nudge
        file.  Polls every 5 seconds up to ``self._config.timeout`` seconds
        for completion.
        """
        workspace = self._resolve_workspace_context(issue)
        metadata = {
            "workspace": workspace,
            "agent_type": "hermes",
            "mode": self._config.mode,
        }

        payload = SignalPayload(
            target=self._target,
            action=SignalAction.WORK,
            issue_id=issue.identifier,
            title=issue.title,
            priority=3,
            metadata=metadata,
        )

        # Send the signal
        if not self._signal_provider.send(self._target, payload):
            print(f"[HermesAgent] Failed to send signal to {self._target}")
            return False

        # Wait for completion by polling for acknowledgement
        deadline = time.time() + self._config.timeout
        while time.time() < deadline:
            result = self._signal_provider.poll(self._target, timeout=5)
            if result is not None:
                # Signal was picked up — acknowledge and return
                self._signal_provider.acknowledge(result.signal_id)
                # If the result has a completion status in metadata, use it
                status = result.metadata.get("status", "completed")
                return status == "completed" or status == "success"

            time.sleep(5)

        print(f"[HermesAgent] Timeout waiting for {self._target} "
              f"to complete {issue.identifier}")
        return False

    def get_id(self) -> str:
        """Return a stable identifier for this agent instance."""
        return f"hermes/{self._target}"

    # ── workspace context resolution ───────────────────────────

    @staticmethod
    def _resolve_workspace_context(issue: Issue) -> dict[str, Any]:
        """Discover workspace context from the filesystem.

        Checks common Hermes workspace locations:
        - ``$PRISMATIC_HOME/work/`` — primary workspace
        - ``/workspace/`` — container workspace
        - A ``PRISMATIC_WORKSPACE`` env var

        Returns a dict with ``path``, ``name``, and ``files`` keys.
        """
        context: dict[str, Any] = {
            "path": "",
            "name": issue.project or issue.identifier,
            "files": [],
        }

        # Check env var first
        env_workspace = os.environ.get("PRISMATIC_WORKSPACE")
        if env_workspace and Path(env_workspace).exists():
            context["path"] = env_workspace
            context["files"] = HermesAgent._list_context_files(Path(env_workspace))
            return context

        # Check common locations
        prismatic_home = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")
        for candidate in [
            Path(prismatic_home) / "work",
            Path("/workspace"),
            Path(".").resolve(),
        ]:
            if candidate.exists():
                context["path"] = str(candidate)
                context["files"] = HermesAgent._list_context_files(candidate)
                return context

        return context

    @staticmethod
    def _list_context_files(workspace: Path, max_depth: int = 2) -> list[str]:
        """List relevant files in the workspace (non-recursive up to max_depth)."""
        files = []
        try:
            for f in workspace.rglob("*"):
                if f.is_file() and f.suffix in {".py", ".md", ".yaml", ".yml", ".json", ".toml"}:
                    try:
                        rel = f.relative_to(workspace)
                        files.append(str(rel))
                    except ValueError:
                        files.append(f.name)
                    if len(files) >= 50:
                        break
        except (PermissionError, OSError):
            pass
        return files


# ── Factory helpers ────────────────────────────────────────────

def create_hermes_agent(config: dict[str, Any]) -> HermesAgent:
    """Shorthand factory for HermesAgent with a plain config dict."""
    agent_config = AgentConfig(
        executable="hermes",
        mode=config.get("mode", "signal"),
        timeout=config.get("timeout", 300),
        next_label=config.get("next_label"),
    )
    return HermesAgent(config=agent_config, agent_config=config)
