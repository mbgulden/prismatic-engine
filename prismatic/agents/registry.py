"""Agent Registry & Dynamic Discovery Engine for Prismatic Engine Signals.

Provides dynamic agent discovery, active process inspection, model tracking,
and active work telemetry across AGY, Hermes, Kai, Fred, Autobot, and custom subagents.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from prismatic.agent_signal_stream import list_agent_signals
from prismatic.agy_activity import list_agy_activity_runs


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


class AgentRegistryManager:
    """Manages agent discovery, active process lifecycle, and model telemetry."""

    @classmethod
    def discover_known_agents(cls) -> list[dict[str, Any]]:
        # Read environment or settings overrides
        hermes_endpoint = os.environ.get("HERMES_ENDPOINT", "http://100.83.32.92:9000")
        agy_model = os.environ.get("AGY_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-pro"
        
        # Check Jules Capacity Ledger
        jules_payload = {}
        try:
            from prismatic.jules_capacity import capacity_payload
            jules_payload = capacity_payload()
        except Exception:
            pass

        jules_status = "idle"
        jules_task = ""
        if jules_payload.get("ok"):
            active = jules_payload.get("active", 0)
            awaiting = jules_payload.get("awaiting", 0)
            if active > 0:
                jules_status = "executing"
                jules_task = f"Active Sessions ({active})"
            elif awaiting > 0:
                jules_status = "waiting_input"
                jules_task = f"Awaiting Plan ({awaiting})"

        return [
            {
                "agent_id": "agy",
                "name": "Antigravity (AGY)",
                "type": "cli_harness",
                "executable": "agy",
                "active_model": agy_model,
                "model_provider": "Google DeepMind",
                "capabilities": ["architecture", "tdd", "verification", "refactoring"],
                "icon": "⚡",
                "source": "AGY CLI Connection",
            },
            {
                "agent_id": "hermes",
                "name": "Hermes Orchestrator",
                "type": "hermes_node",
                "executable": "hermes",
                "active_model": "claude-3-7-sonnet",
                "model_provider": "Anthropic / Proxmox Node",
                "capabilities": ["infrastructure", "proxmox", "deployments", "dns"],
                "icon": "🌐",
                "source": f"Hermes Hub ({hermes_endpoint})",
            },
            {
                "agent_id": "jules",
                "name": "Jules CLI",
                "type": "jules_harness",
                "executable": "jules",
                "active_model": "jules-agent-v1",
                "model_provider": "Google Cloud",
                "capabilities": ["multi_repo", "capacity_ledger", "auto_pr"],
                "icon": "🚀",
                "source": "Jules Capacity Store (300/day limit)",
                "status_override": jules_status,
                "current_task_override": jules_task,
            },
            {
                "agent_id": "kai",
                "name": "Kai (UI Specialist)",
                "type": "specialist_subagent",
                "executable": "kai",
                "active_model": "claude-3-7-sonnet",
                "model_provider": "Anthropic",
                "capabilities": ["css", "ui_design", "accessibility", "playwright"],
                "icon": "🎨",
                "source": "Subagent Registry",
            },
            {
                "agent_id": "fred",
                "name": "Fred (TDD Specialist)",
                "type": "specialist_subagent",
                "executable": "fred",
                "active_model": "claude-3-7-sonnet",
                "model_provider": "Anthropic",
                "capabilities": ["python_kernel", "fastapi", "pytest", "review_factory"],
                "icon": "⚙️",
                "source": "Subagent Registry",
            },
            {
                "agent_id": "george",
                "name": "George (Peer Reviewer)",
                "type": "review_agent",
                "executable": "george",
                "active_model": "gpt-4o",
                "model_provider": "OpenAI",
                "capabilities": ["peer_review", "rebase_audit", "pr_evidence"],
                "icon": "🛡️",
                "source": "Subagent Registry",
            },
            {
                "agent_id": "autobot",
                "name": "Autobot (CI Worker)",
                "type": "verification_worker",
                "executable": "autobot",
                "active_model": "deepseek-r1",
                "model_provider": "DeepSeek",
                "capabilities": ["ci_runner", "wheel_build", "clean_room"],
                "icon": "🤖",
                "source": "Subagent Registry",
            },
        ]

    @classmethod
    def get_active_agents(cls) -> list[dict[str, Any]]:
        """Dynamically discover running/registered agents and their active telemetry."""
        signals_data = list_agent_signals(limit=200, include_log_tails=False)
        agy_runs_data = list_agy_activity_runs(limit=20)

        counts = signals_data.get("counts", {})
        recent_items = signals_data.get("items", [])

        # Map last activity per agent from recent signals
        last_signal_by_agent: dict[str, dict[str, Any]] = {}
        for item in recent_items:
            a = str(item.get("agent") or "").lower()
            if a and a not in last_signal_by_agent:
                last_signal_by_agent[a] = item

        agents: list[dict[str, Any]] = []

        for meta in cls.discover_known_agents():
            aid = meta["agent_id"]
            last_sig = last_signal_by_agent.get(aid, {})
            signal_count = counts.get(aid, 0)

            # Determine status & model override
            status = meta.get("status_override", "idle")
            current_task = meta.get("current_task_override", "")

            if aid == "agy" and agy_runs_data.get("status") == "ok":
                runs = agy_runs_data.get("runs", [])
                active_runs = [r for r in runs if r.get("state") == "running"]
                if active_runs:
                    status = "executing"
                    current_task = active_runs[0].get("task_ref", "")
                elif runs:
                    current_task = runs[0].get("task_ref", "")

            if status == "idle" and last_sig:
                sig_status = str(last_sig.get("status") or "").lower()
                if "execut" in sig_status or "run" in sig_status or "active" in sig_status:
                    status = "executing"
                elif "error" in sig_status or "fail" in sig_status:
                    status = "errored"
                elif "wait" in sig_status or "nudge" in sig_status:
                    status = "waiting_input"
                current_task = last_sig.get("issue_id") or last_sig.get("run_id") or ""

            agents.append(
                {
                    "agent_id": aid,
                    "name": meta["name"],
                    "type": meta["type"],
                    "executable": meta["executable"],
                    "status": status,
                    "active_model": meta["active_model"],
                    "model_provider": meta["model_provider"],
                    "capabilities": meta["capabilities"],
                    "icon": meta["icon"],
                    "source": meta["source"],
                    "signal_count": signal_count,
                    "current_task": current_task,
                    "last_signal_at": last_sig.get("timestamp"),
                    "last_message": last_sig.get("message") or "Ready for task assignments.",
                    "severity": last_sig.get("severity", "info"),
                }
            )

        return agents


def get_agent_telemetry_summary() -> dict[str, Any]:
    agents = AgentRegistryManager.get_active_agents()
    active_count = sum(1 for a in agents if a["status"] == "executing")
    errored_count = sum(1 for a in agents if a["status"] == "errored")
    total_signals = sum(a["signal_count"] for a in agents)

    return {
        "status": "ok",
        "source": "prismatic.agents.registry",
        "total_agents": len(agents),
        "active_agents": active_count,
        "errored_agents": errored_count,
        "total_signals": total_signals,
        "agents": agents,
    }
