"""Agent configuration data (lane/agent definitions).

Phase 1 of the dispatcher-removal plan (kanban t_350929e6): the AGENT_CONFIG
mapping moves out of prismatic/dispatcher.py into its surviving home, next to
prismatic/capability_router.py (which consumes the mapping by parameter
injection via default_capability_registry(agent_config=...)). Moved verbatim
from dispatcher.py (origin/main @ 67bc5f69).
"""

from __future__ import annotations

import os
import time
from typing import Any


# Agent binary paths — override via env vars
AGY_PATH: str = os.environ.get("AGY_PATH", "agy")
JULES_PATH: str = os.environ.get("JULES_PATH", "jules")
CODEX_PATH: str = os.environ.get("CODEX_PATH", "codex")


# ═══════════════════════════════════════════════════════════════
# Agent Configuration
# ═══════════════════════════════════════════════════════════════


def _build_dynamic_agent_config() -> dict[str, dict[str, Any]]:
    """Dynamically discover and construct agent configuration dictionary."""
    config: dict[str, dict[str, Any]] = {}
    try:
        from prismatic.agents.discovery import AgentDiscoveryService

        profiles = AgentDiscoveryService.get_agents()
        for p in profiles:
            agent_id = p.agent_id.lower()
            harness = str((p.metadata or {}).get("harness", "") or "").lower()
            if not harness:
                if "agy" in agent_id or "antigravity" in agent_id:
                    harness = "antigravity"
                elif "jules" in agent_id:
                    harness = "jules"
                elif "codex" in agent_id:
                    harness = "codex"
                else:
                    harness = "hermes"

            if "antigravity" in harness or "agy" in harness:
                exe = AGY_PATH
                mode = "launch"
            elif "jules" in harness:
                exe = JULES_PATH
                mode = "launch"
            elif "codex" in harness:
                exe = CODEX_PATH
                mode = "launch"
            else:
                exe = f"hermes --profile {p.agent_id}"
                mode = "signal"
            config[agent_id] = {
                "executable": exe,
                "mode": mode,
                "timeout": 600,
                "next_label": "",
                "description": f"{p.name} — {p.role}",
                "model": p.active_model,
                "harness": harness,
            }
    except Exception:
        pass

    # Dispatch lanes for registered launchers that discovery does not cover
    # (jules/codex use external binaries configured via JULES_PATH/CODEX_PATH
    # and have mode-switch transitions registered in dispatch_once).
    for agent_id, exe in (("jules", JULES_PATH), ("codex", CODEX_PATH)):
        if agent_id not in config:
            config[agent_id] = {
                "executable": exe,
                "mode": "launch",
                "timeout": 600,
                "next_label": "",
                "description": f"{agent_id} — external agent launcher",
                "harness": agent_id,
            }

    if not config:
        config = {
            "orchestrator": {
                "executable": AGY_PATH,
                "mode": "launch",
                "timeout": 600,
                "next_label": "",
                "description": "Sovereign Orchestration Agent",
            },
            "architect": {
                "executable": AGY_PATH,
                "mode": "launch",
                "timeout": 900,
                "next_label": "",
                "description": "System Architecture & Spec Agent",
            },
            "executor": {
                "executable": AGY_PATH,
                "mode": "launch",
                "timeout": 1200,
                "next_label": "",
                "description": "Code Execution & Refactoring Agent",
            },
            "verifier": {
                "executable": AGY_PATH,
                "mode": "launch",
                "timeout": 600,
                "next_label": "",
                "description": "Deterministic Verification & Test Agent",
            },
            "curator": {
                "executable": AGY_PATH,
                "mode": "launch",
                "timeout": 300,
                "next_label": "",
                "description": "Ledger & Audit Curation Agent",
            },
        }
    return config


class DynamicAgentConfigDict(dict):
    """Dict proxy that transparently refreshes discovered agents if empty."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_refresh = 0.0
        self._ttl = 30.0
        self._refreshing = False

    def _ensure_fresh(self) -> None:
        if self._refreshing:
            return
        now = time.time()
        if super().__len__() == 0 or (now - self._last_refresh > self._ttl):
            self._refreshing = True
            try:
                fresh = _build_dynamic_agent_config()
                self.clear()
                self.update(fresh)
                self._last_refresh = now
            finally:
                self._refreshing = False

    def __getitem__(self, key: Any) -> Any:
        self._ensure_fresh()
        return super().__getitem__(key)

    def __iter__(self) -> Any:
        self._ensure_fresh()
        return super().__iter__()

    def __len__(self) -> int:
        self._ensure_fresh()
        return super().__len__()

    def __contains__(self, key: Any) -> bool:
        self._ensure_fresh()
        return super().__contains__(key)

    def keys(self) -> Any:
        self._ensure_fresh()
        return super().keys()

    def values(self) -> Any:
        self._ensure_fresh()
        return super().values()

    def items(self) -> Any:
        self._ensure_fresh()
        return super().items()

    def get(self, key: Any, default: Any = None) -> Any:
        self._ensure_fresh()
        return super().get(key, default)


AGENT_CONFIG: dict[str, dict[str, Any]] = DynamicAgentConfigDict(
    _build_dynamic_agent_config()
)
