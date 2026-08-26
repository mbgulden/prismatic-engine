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


# Baseline canonical swarm fleet definition
CORE_FLEET = [
    {
        "agent_id": "fred",
        "name": "Fred (Orchestrator)",
        "alias": ["fred-orchestrator", "orchestrator"],
        "host": "webtop-hermes (Ubuntu VM 800)",
        "type": "orchestrator",
        "executable": "hermes",
        "active_model": "local-qwen-27b-q8-fred",
        "model_provider": "qwen27b-fred-local (192.168.1.230:8000)",
        "capabilities": ["topological_dispatch", "lane_governance", "multi_agent_coordination", "goal_delegation"],
        "icon": "👑",
        "source": "Hermes Orchestrator Profile",
    },
    {
        "agent_id": "agy",
        "name": "Lightbringer Antigravity",
        "alias": ["lightbringer-agy", "antigravity", "lightbringer-antigravity"],
        "host": "Lightbringer (Windows Host)",
        "type": "lead_assistant",
        "executable": "antigravity",
        "active_model": "gemini-2.5-pro",
        "model_provider": "Google DeepMind",
        "capabilities": ["hypervisor_client", "code_authoring", "ast_refactor", "tool_calling"],
        "icon": "⚡",
        "source": "Primary Developer Host (Lightbringer)",
    },
    {
        "agent_id": "kai",
        "name": "Kai (UI Specialist)",
        "alias": ["kai-ui", "kai-frontend"],
        "host": "webtop-hermes",
        "type": "ui_developer",
        "executable": "kai",
        "active_model": "qwen3.8-27b",
        "model_provider": "qwen27b-kai-local (192.168.1.232:8080)",
        "capabilities": ["dashboard_design", "tailwind_css", "visual_auditing", "accessibility"],
        "icon": "🎨",
        "source": "Hermes Profile (kai)",
    },
    {
        "agent_id": "george",
        "name": "George (Peer Reviewer)",
        "alias": ["george-review"],
        "host": "webtop-hermes",
        "type": "peer_reviewer",
        "executable": "george",
        "active_model": "qwen3.8-27b",
        "model_provider": "qwen27b-kai-local (192.168.1.232:8080)",
        "capabilities": ["invariant_audit", "pr_handoff", "exact_head_verification"],
        "icon": "🛡️",
        "source": "Hermes Profile (george)",
    },
    {
        "agent_id": "ned",
        "name": "Ned (Backend Specialist)",
        "alias": ["ned-backend"],
        "host": "webtop-hermes",
        "type": "backend_developer",
        "executable": "ned",
        "active_model": "Qwen3.8-27B-UD-Q5",
        "model_provider": "qwen27b-ned-local (192.168.1.230:8003)",
        "capabilities": ["database_migrations", "api_design", "backend_infrastructure"],
        "icon": "⚙️",
        "source": "Hermes Profile (ned)",
    },
    {
        "agent_id": "autobot",
        "name": "Autobot (CI Worker)",
        "alias": ["autobot-ci"],
        "host": "webtop-hermes",
        "type": "ci_worker",
        "executable": "autobot",
        "active_model": "MiniMax-M2.7-highspeed",
        "model_provider": "MiniMax",
        "capabilities": ["wheel_packaging", "isolated_venv", "clean_room_testing"],
        "icon": "🤖",
        "source": "Hermes Profile (autobot)",
    },
    {
        "agent_id": "swarmproof",
        "name": "SwarmProof Verifier",
        "alias": ["swarmproof-oracle"],
        "host": "webtop-hermes",
        "type": "truth_oracle",
        "executable": "swarmproof",
        "active_model": "rule-oracle-v0.3.0",
        "model_provider": "Prismatic Core",
        "capabilities": ["ast_analysis", "anti_deception", "manifest_verify"],
        "icon": "⚖️",
        "source": "SwarmProof Core v0.3.0",
    }
]


class DynamicAgentRegistry:
    """Manages multi-host agent discovery, live lease tracking, and dynamic registry storage."""

    @classmethod
    def get_registered_agents(cls) -> list[dict[str, Any]]:
        """Return base fleet plus any dynamically registered external machine agents and Hermes profiles."""
        stored = _read_json(REGISTRY_DB_PATH).get("agents", {})
        
        fleet_map: dict[str, dict[str, Any]] = {}
        for a in CORE_FLEET:
            fleet_map[a["agent_id"]] = dict(a)

        # Dynamically discover live profiles from ~/.hermes/profiles if present
        hermes_profiles_dir = Path(os.path.expanduser("~/.hermes/profiles"))
        if hermes_profiles_dir.exists() and hermes_profiles_dir.is_dir():
            try:
                import yaml
                for p_dir in hermes_profiles_dir.iterdir():
                    if p_dir.is_dir() and not p_dir.is_symlink():
                        cfg_file = p_dir / "config.yaml"
                        if cfg_file.exists():
                            try:
                                with open(cfg_file, "r", encoding="utf-8") as f:
                                    cfg = yaml.safe_load(f) or {}
                                    p_name = p_dir.name
                                    model_obj = cfg.get("model") or cfg.get("active_model") or {}
                                    model_name = model_obj.get("default") if isinstance(model_obj, dict) else str(model_obj)
                                    provider_name = model_obj.get("provider") if isinstance(model_obj, dict) else ""
                                    
                                    # Update existing core agent or add new profile
                                    target_key = "fred" if p_name == "orchestrator" else p_name
                                    if target_key in fleet_map:
                                        if model_name:
                                            fleet_map[target_key]["active_model"] = model_name
                                        if provider_name:
                                            fleet_map[target_key]["model_provider"] = provider_name
                            except Exception:
                                pass
            except Exception:
                pass

        # Merge dynamically registered agents from other nodes (ignoring test patterns)
        for aid, item in stored.items():
            if EPHEMERAL_TEST_AGENT_REGEX.match(aid):
                continue
            if aid not in fleet_map:
                fleet_map[aid] = item
            else:
                fleet_map[aid].update(item)

        return list(fleet_map.values())

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
