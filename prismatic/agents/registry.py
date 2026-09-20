"""
Prismatic Dynamic Agent Registry & Multi-Host Discovery Engine.
Tracks core swarm agents and dynamically discovers active nodes across harnesses and machines.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from prismatic.agent_signal_stream import list_agent_signals
from prismatic.lock import _get_lock_manager

logger = logging.getLogger("prismatic.agents.registry")

REGISTRY_DB_PATH = Path(os.path.expanduser("~/.antigravity/agents/dynamic_registry.json"))

# Patterns that should NEVER be registered as persistent fleet agents (test workers / chaos iterations)
EPHEMERAL_TEST_AGENT_REGEX = re.compile(
    r"^(agent_(contender|fault|zombie|disjoint)_\d+|test_|tmp_worker|mock_agent)",
    re.IGNORECASE
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("Failed reading %s: %s", path, e)
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _ensure_dir(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


from prismatic.agents.discovery import AgentDiscoveryService, AgentProfile


class DynamicAgentRegistry:
    """Manages multi-host agent discovery, live lease tracking, and dynamic registry storage."""

    @classmethod
    def get_registered_agents(cls) -> list[dict[str, Any]]:
        """Return dynamically discovered agents across filesystem profiles, registry, and mesh."""
        agents = AgentDiscoveryService.get_agents()
        out: list[dict[str, Any]] = []
        for a in agents:
            d = a.to_dict()
            d["type"] = d.get("role", "dynamic_node")
            d["executable"] = d.get("agent_id", "agent")
            out.append(d)
        return out

    @classmethod
    def register_or_update_agent(
        cls,
        agent_id: str,
        name: Optional[str] = None,
        host: Optional[str] = None,
        model: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
        icon: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Dynamically register or update an agent node from any host/machine."""
        clean_id = agent_id.strip().lower().replace(" ", "-")
        if EPHEMERAL_TEST_AGENT_REGEX.match(clean_id):
            return None

        stored_doc = _read_json(REGISTRY_DB_PATH)
        agents_dict = stored_doc.get("agents", {})

        record = agents_dict.get(clean_id, {
            "agent_id": clean_id,
            "name": name or f"{agent_id.title()} Agent",
            "host": host or socket.gethostname(),
            "type": "dynamic_node",
            "executable": clean_id,
            "active_model": model or "auto",
            "model_provider": "External Node",
            "capabilities": capabilities or ["tool_calling", "telemetry"],
            "icon": icon or "⚡",
            "source": f"Dynamic Registration ({host or socket.gethostname()})",
            "created_at": _utc_now_iso(),
        })

        if name:
            record["name"] = name
        if host:
            record["host"] = host
        if model:
            record["active_model"] = model
        if capabilities:
            record["capabilities"] = capabilities
        if icon:
            record["icon"] = icon
        record["last_seen_at"] = _utc_now_iso()

        agents_dict[clean_id] = record
        stored_doc["agents"] = agents_dict
        _write_json(REGISTRY_DB_PATH, stored_doc)
        AgentDiscoveryService.invalidate_cache()
        return record

    @classmethod
    def delete_agent(cls, agent_id: str) -> bool:
        """Delete a dynamic agent from the registry."""
        clean_id = agent_id.strip().lower()
        stored_doc = _read_json(REGISTRY_DB_PATH)
        agents_dict = stored_doc.get("agents", {})
        if clean_id in agents_dict:
            del agents_dict[clean_id]
            stored_doc["agents"] = agents_dict
            _write_json(REGISTRY_DB_PATH, stored_doc)
            AgentDiscoveryService.invalidate_cache()
            return True
        return False

    @classmethod
    def get_active_agents(cls) -> list[dict[str, Any]]:
        """Return accurate live agent status, active lease holdings, and last telemetry."""
        registered = cls.get_registered_agents()
        registered_map = {a["agent_id"]: a for a in registered}
        alias_map = {}
        for a in registered:
            alias_map[a["agent_id"]] = a["agent_id"]
            for alias in a.get("alias", []):
                alias_map[alias.lower()] = a["agent_id"]

        # 1. Fetch active locks from SwarmLockManager
        active_locks_by_agent: dict[str, list[dict[str, Any]]] = {}
        try:
            lock_mgr = _get_lock_manager()
            lock_status = lock_mgr.get_enriched_status()
            for l in lock_status.get("locks", []):
                holder_raw = str(l.get("holder") or "unknown").strip().lower()
                if EPHEMERAL_TEST_AGENT_REGEX.match(holder_raw):
                    continue
                canonical_id = alias_map.get(holder_raw, holder_raw)
                
                # Dynamically discover unknown genuine lock holders
                if canonical_id not in registered_map:
                    new_agent = cls.register_or_update_agent(
                        agent_id=canonical_id,
                        name=f"{l.get('holder')} Agent",
                        host=l.get("workspace") or "Remote Host",
                        model=l.get("model") or "gemini-2.5-pro",
                        icon="🔒"
                    )
                    if new_agent:
                        registered_map[canonical_id] = new_agent
                        alias_map[holder_raw] = canonical_id

                if canonical_id in registered_map:
                    active_locks_by_agent.setdefault(canonical_id, []).append(l)
        except Exception as e:
            logger.debug("Error reading active locks in registry: %s", e)

        # 2. Fetch live signals
        signals_data = list_agent_signals(limit=150, include_log_tails=False)
        counts = signals_data.get("counts", {})
        recent_items = signals_data.get("items", [])

        last_signal_by_agent: dict[str, dict[str, Any]] = {}
        for item in recent_items:
            raw_agent = str(item.get("agent") or "").strip().lower()
            if EPHEMERAL_TEST_AGENT_REGEX.match(raw_agent):
                continue
            canon = alias_map.get(raw_agent, raw_agent)
            
            # Dynamically discover unknown genuine signal emitters
            if canon and canon not in registered_map:
                new_agent = cls.register_or_update_agent(
                    agent_id=canon,
                    name=f"{raw_agent.title()} Agent",
                    host="Discovered Node",
                    icon="📡"
                )
                if new_agent:
                    registered_map[canon] = new_agent
                    alias_map[raw_agent] = canon

            if canon and canon not in last_signal_by_agent:
                last_signal_by_agent[canon] = item

        agents_out: list[dict[str, Any]] = []

        for aid, meta in registered_map.items():
            held_locks = active_locks_by_agent.get(aid, [])
            last_sig = last_signal_by_agent.get(aid, {})
            signal_count = counts.get(aid, 0)
            for alias in meta.get("alias", []):
                signal_count += counts.get(alias.lower(), 0)

            # Determine live execution status
            if held_locks:
                status = "executing"
                current_task = held_locks[0].get("task_id") or held_locks[0].get("intention") or "Holding Active Lease"
            elif last_sig:
                sig_type = str(last_sig.get("event_type") or "").lower()
                if "error" in sig_type or "fail" in sig_type:
                    status = "errored"
                elif "wait" in sig_type or "nudge" in sig_type:
                    status = "waiting_input"
                else:
                    status = "idle"
                current_task = last_sig.get("issue_id") or last_sig.get("run_id") or ""
            else:
                status = "idle"
                current_task = ""

            agents_out.append({
                "agent_id": aid,
                "name": meta["name"],
                "host": meta.get("host", "Unknown Host"),
                "type": meta["type"],
                "executable": meta.get("executable", aid),
                "status": status,
                "active_model": meta.get("active_model", "default"),
                "model_provider": meta.get("model_provider", "Local / Cloud"),
                "capabilities": meta.get("capabilities", ["tool_calling"]),
                "icon": meta.get("icon", "🤖"),
                "source": meta.get("source", "Swarm Fleet Registry"),
                "active_locks": len(held_locks),
                "signal_count": signal_count,
                "current_task": current_task,
                "last_signal_at": last_sig.get("timestamp"),
                "last_message": last_sig.get("message") or "Ready for task assignments.",
                "severity": last_sig.get("severity", "info"),
            })

        return agents_out


def get_agent_telemetry_summary() -> dict[str, Any]:
    agents = DynamicAgentRegistry.get_active_agents()
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


AgentRegistryManager = DynamicAgentRegistry
