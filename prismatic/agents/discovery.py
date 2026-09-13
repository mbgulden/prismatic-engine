"""
prismatic/agents/discovery.py — Sovereign Dynamic Agent Discovery Service
========================================================================

Discovers, normalizes, and caches agent nodes dynamically across:
1. Local filesystem profiles (~/.hermes/profiles, ~/.prismatic/profiles)
2. Dynamic agent registry (~/.antigravity/agents/dynamic_registry.json)
3. Active SwarmLock leases (real-time concurrency telemetry)
4. Tailscale mesh peers (multi-machine compute nodes)
5. Generic sovereign archetype fallbacks (when zero host profiles exist)

Enforces:
- Zero compile-time coupling to hardcoded personal agent identities.
- Thread-safe TTL in-memory caching with explicit invalidation.
- Automatic extraction of agent name, role, model, and capabilities from profile metadata.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.agents.discovery")

# Regex to detect ephemeral test workers that should never pollute the persistent fleet
EPHEMERAL_TEST_AGENT_REGEX = re.compile(
    r"^(agent_(contender|fault|zombie|disjoint)_\d+|test_|tmp_worker|mock_agent)",
    re.IGNORECASE,
)

# Regex to parse display name and role from SOUL.md first lines
# Examples:
#   "# Ned — Infrastructure Monitor & DevOps Agent"
#   "You are Fred — Michael's Hermes assistant, orchestrator..."
SOUL_HEADER_REGEX = re.compile(
    r"^(?:#\s*|You are\s+)([\w\s\(\)\-]+?)\s*[—–-]\s*(.+?)(?:\.|$)",
    re.MULTILINE,
)


@dataclass
class AgentProfile:
    """Normalized, typed metadata representing an active or configured agent node."""

    agent_id: str
    name: str
    role: str
    host: str = "local"
    active_model: str = "auto"
    model_provider: str = "local"
    capabilities: list[str] = field(default_factory=list)
    status: str = "idle"  # "idle", "active", "busy", "offline", "unknown"
    icon: str = "🤖"
    source: str = "dynamic_discovery"
    last_seen: str | None = None
    current_issue: str | None = None
    current_resource: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentDiscoveryService:
    """Thread-safe dynamic agent discovery service with TTL caching."""

    _lock = threading.Lock()
    _cached_fleet: dict[str, AgentProfile] | None = None
    _last_refresh_time: float = 0.0
    _ttl_seconds: float = 30.0

    @classmethod
    def set_ttl(cls, ttl_seconds: float) -> None:
        """Configure cache TTL in seconds."""
        with cls._lock:
            cls._ttl_seconds = max(1.0, float(ttl_seconds))

    @classmethod
    def invalidate_cache(cls) -> None:
        """Explicitly clear the in-memory agent cache."""
        with cls._lock:
            cls._cached_fleet = None
            cls._last_refresh_time = 0.0

    @classmethod
    def get_agents(cls, force_refresh: bool = False) -> list[AgentProfile]:
        """Return the current fleet of dynamically discovered agents."""
        now = time.time()
        with cls._lock:
            if (
                not force_refresh
                and cls._cached_fleet is not None
                and (now - cls._last_refresh_time) < cls._ttl_seconds
            ):
                return list(cls._cached_fleet.values())

        # Perform discovery outside lock to prevent blocking
        discovered = cls._discover_all_sources()

        with cls._lock:
            cls._cached_fleet = discovered
            cls._last_refresh_time = time.time()
            return list(cls._cached_fleet.values())

    @classmethod
    def get_agents_dict(cls, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Return agents formatted for dashboard APIs and JSON responses."""
        agents = cls.get_agents(force_refresh=force_refresh)
        return {agent.agent_id: agent.to_dict() for agent in agents}

    @classmethod
    def get_agent(cls, agent_id: str) -> AgentProfile | None:
        """Find a specific agent by ID."""
        clean_id = agent_id.strip().lower().replace(" ", "-")
        agents = cls.get_agents()
        for a in agents:
            if a.agent_id == clean_id:
                return a
        return None

    @classmethod
    def find_agents_by_capability(cls, capability: str) -> list[AgentProfile]:
        """Find all agents offering a specific capability."""
        cap_clean = capability.strip().lower()
        agents = cls.get_agents()
        return [
            a for a in agents
            if any(cap_clean in c.lower() for c in a.capabilities)
            or cap_clean in a.role.lower()
        ]

    @classmethod
    def find_agent_for_role(cls, role_keyword: str) -> AgentProfile | None:
        """Match an agent to an ID or functional role keyword (e.g. 'orchestrator', 'review', 'code')."""
        kw = role_keyword.strip().lower()
        agents = cls.get_agents()
        # 1. Exact match on agent_id
        for a in agents:
            if a.agent_id == kw:
                return a
        # 2. Check in role, capabilities, or name
        for a in agents:
            if (
                kw in a.role.lower()
                or kw in a.agent_id.lower()
                or any(kw in c.lower() for c in a.capabilities)
                or kw in a.name.lower()
            ):
                return a
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Multi-Source Discovery Pipeline
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def _discover_all_sources(cls) -> dict[str, AgentProfile]:
        """Aggregate agent metadata across all available environment sources."""
        fleet: dict[str, AgentProfile] = {}

        # 1. Discover local Hermes/Prismatic profiles
        cls._discover_filesystem_profiles(fleet)

        # 2. Discover dynamic agent registry records
        cls._discover_dynamic_registry(fleet)

        # 3. Discover active SwarmLock leases
        cls._discover_swarmlock_holders(fleet)

        # 4. Discover Tailscale mesh peers
        cls._discover_mesh_peers(fleet)

        # 5. Provide fallback generic roles if no profiles were found
        if not fleet:
            cls._populate_fallback_fleet(fleet)

        return fleet

    @classmethod
    def _discover_filesystem_profiles(cls, fleet: dict[str, AgentProfile]) -> None:
        """Scan ~/.hermes/profiles and ~/.prismatic/profiles for installed agent directories."""
        candidate_dirs = [
            Path(os.path.expanduser("~/.hermes/profiles")),
            Path(os.path.expanduser("~/.prismatic/profiles")),
            Path(os.environ.get("PRISMATIC_PROFILES_DIR", "")) if os.environ.get("PRISMATIC_PROFILES_DIR") else None,
        ]

        for base_dir in candidate_dirs:
            if not base_dir or not base_dir.exists() or not base_dir.is_dir():
                continue

            try:
                for entry in sorted(base_dir.iterdir()):
                    if not entry.is_dir():
                        continue
                    agent_id = entry.name.strip().lower().replace(" ", "-")

                    if EPHEMERAL_TEST_AGENT_REGEX.match(agent_id):
                        continue

                    profile = cls._parse_profile_directory(entry, agent_id)
                    if profile:
                        if agent_id not in fleet:
                            fleet[agent_id] = profile
                        else:
                            # Merge fields without overwriting active data
                            existing = fleet[agent_id]
                            if existing.active_model == "auto" and profile.active_model != "auto":
                                existing.active_model = profile.active_model
                            if existing.model_provider == "local" and profile.model_provider != "local":
                                existing.model_provider = profile.model_provider
                            if not existing.capabilities and profile.capabilities:
                                existing.capabilities = profile.capabilities
            except Exception as exc:
                logger.warning("Error scanning profiles directory %s: %s", base_dir, exc)

    @classmethod
    def _parse_profile_directory(cls, dir_path: Path, agent_id: str) -> AgentProfile | None:
        """Extract structured agent metadata from a profile folder."""
        name = agent_id.replace("-", " ").title()
        role = "Autonomous Agent"
        active_model = "auto"
        model_provider = "local"
        capabilities: list[str] = ["execution", "tool_calling"]
        icon = cls._icon_for_agent(agent_id)

        # 1. Parse SOUL.md for display name and role description
        soul_path = dir_path / "SOUL.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text(encoding="utf-8", errors="ignore")
                match = SOUL_HEADER_REGEX.search(content)
                if match:
                    parsed_name = match.group(1).strip()
                    parsed_role = match.group(2).strip()
                    if parsed_name and len(parsed_name) < 40:
                        name = parsed_name
                    if parsed_role and len(parsed_role) < 120:
                        role = parsed_role
            except Exception:
                pass

        # 2. Parse config.yaml for model & provider settings
        cfg_path = dir_path / "config.yaml"
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}

                model_entry = cfg.get("model") or cfg.get("active_model")
                if isinstance(model_entry, dict):
                    active_model = str(model_entry.get("default") or model_entry.get("default_model") or "auto")
                    model_provider = str(model_entry.get("provider") or "local")
                elif isinstance(model_entry, str) and model_entry:
                    active_model = model_entry

                # Infer capabilities from tools / plugins
                if "plugins" in cfg or (dir_path / "plugins").exists():
                    capabilities.append("plugins")
                if (dir_path / "skills").exists():
                    capabilities.append("skills")
            except Exception:
                pass

        return AgentProfile(
            agent_id=agent_id,
            name=name,
            role=role,
            host=os.uname().nodename if hasattr(os, "uname") else "localhost",
            active_model=active_model,
            model_provider=model_provider,
            capabilities=sorted(list(set(capabilities))),
            status="idle",
            icon=icon,
            source=f"Profile ({dir_path.name})",
            last_seen=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def _discover_dynamic_registry(cls, fleet: dict[str, AgentProfile]) -> None:
        """Ingest dynamic registration records from ~/.antigravity/agents/dynamic_registry.json."""
        registry_path = Path(os.path.expanduser("~/.antigravity/agents/dynamic_registry.json"))
        if not registry_path.exists():
            return

        try:
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
            agents_dict = raw.get("agents", {})
            for aid, data in agents_dict.items():
                if not isinstance(data, dict):
                    continue
                clean_id = aid.strip().lower().replace(" ", "-")
                if EPHEMERAL_TEST_AGENT_REGEX.match(clean_id):
                    continue

                profile = AgentProfile(
                    agent_id=clean_id,
                    name=str(data.get("name") or clean_id.title()),
                    role=str(data.get("role") or "Dynamic Swarm Node"),
                    host=str(data.get("host") or "External Node"),
                    active_model=str(data.get("active_model") or data.get("model") or "auto"),
                    model_provider=str(data.get("model_provider") or "External"),
                    capabilities=list(data.get("capabilities", ["remote_execution"])),
                    status=str(data.get("status") or "online"),
                    icon=str(data.get("icon") or cls._icon_for_agent(clean_id)),
                    source=str(data.get("source") or "Dynamic Registration"),
                    last_seen=data.get("last_seen_at") or data.get("created_at"),
                )

                if clean_id not in fleet:
                    fleet[clean_id] = profile
                else:
                    fleet[clean_id].status = profile.status
                    if profile.host != "External Node":
                        fleet[clean_id].host = profile.host
                    if profile.active_model != "auto":
                        fleet[clean_id].active_model = profile.active_model
                    if profile.capabilities:
                        fleet[clean_id].capabilities = sorted(
                            list(set(fleet[clean_id].capabilities + profile.capabilities))
                        )
        except Exception as exc:
            logger.warning("Error reading dynamic registry %s: %s", registry_path, exc)

    @classmethod
    def _discover_swarmlock_holders(cls, fleet: dict[str, AgentProfile]) -> None:
        """Enrich agent statuses with active SwarmLock leases."""
        try:
            from prismatic.core.locking import SwarmLockManager
            status = SwarmLockManager.get_status()
            locks = status.get("locks", [])
            for lock in locks:
                holder = str(lock.get("holder", "")).strip().lower().replace(" ", "-")
                if not holder:
                    continue
                resource = lock.get("resource")
                task_id = lock.get("task_id")

                if holder in fleet:
                    fleet[holder].status = "active"
                    fleet[holder].current_resource = resource
                    fleet[holder].current_issue = task_id
                else:
                    fleet[holder] = AgentProfile(
                        agent_id=holder,
                        name=holder.title(),
                        role="Active Resource Holder",
                        host=os.uname().nodename if hasattr(os, "uname") else "localhost",
                        status="active",
                        icon="🔒",
                        source="Active SwarmLock Lease",
                        current_resource=resource,
                        current_issue=task_id,
                        last_seen=datetime.now(timezone.utc).isoformat(),
                    )
        except Exception:
            pass

    @classmethod
    def _discover_mesh_peers(cls, fleet: dict[str, AgentProfile]) -> None:
        """Discover Tailscale mesh peers as potential compute/worker nodes."""
        try:
            from prismatic.mesh.tailscale import TailscaleMeshClient
            peers = TailscaleMeshClient.get_peers()
            for peer in peers:
                peer_id = f"peer-{peer.host_name.lower()}"
                if peer_id not in fleet and peer.online:
                    fleet[peer_id] = AgentProfile(
                        agent_id=peer_id,
                        name=f"{peer.host_name} (Mesh)",
                        role=f"Tailscale Compute Node ({peer.os_type})",
                        host=peer.tailscale_ip,
                        active_model="mesh-remote",
                        model_provider="Tailscale",
                        capabilities=["mesh_compute", "distributed_task"],
                        status="online",
                        icon="🌐",
                        source=f"Tailscale Mesh ({peer.tailscale_ip})",
                        last_seen=datetime.now(timezone.utc).isoformat(),
                    )
        except Exception:
            pass

    @classmethod
    def _populate_fallback_fleet(cls, fleet: dict[str, AgentProfile]) -> None:
        """Provide generic, sovereign fallback agents when no local profiles exist."""
        fallbacks = [
            AgentProfile(
                agent_id="orchestrator",
                name="Fleet Orchestrator",
                role="Topological Dispatch & Lane Governance",
                host="local",
                active_model="auto",
                model_provider="Local Kernel",
                capabilities=["topological_dispatch", "lane_governance", "multi_agent_coordination"],
                status="online",
                icon="👑",
                source="Sovereign Kernel Fallback",
            ),
            AgentProfile(
                agent_id="architect",
                name="Systems Architect",
                role="Domain Architecture & API Design",
                host="local",
                active_model="auto",
                model_provider="Local Kernel",
                capabilities=["api_design", "database_migrations", "code_authoring"],
                status="idle",
                icon="⚙️",
                source="Sovereign Kernel Fallback",
            ),
            AgentProfile(
                agent_id="builder",
                name="Implementation Specialist",
                role="Code Generation & Component Assembly",
                host="local",
                active_model="auto",
                model_provider="Local Kernel",
                capabilities=["code_authoring", "ast_refactor", "component_compilation"],
                status="idle",
                icon="🛠️",
                source="Sovereign Kernel Fallback",
            ),
            AgentProfile(
                agent_id="reviewer",
                name="Quality & Security Sentinel",
                role="AST Anti-Weakening & Peer Review",
                host="local",
                active_model="auto",
                model_provider="Local Kernel",
                capabilities=["invariant_audit", "pr_handoff", "exact_head_verification"],
                status="idle",
                icon="🛡️",
                source="Sovereign Kernel Fallback",
            ),
            AgentProfile(
                agent_id="deployer",
                name="Release & Edge Engineer",
                role="Packaging & Cloudflare Deployment",
                host="local",
                active_model="auto",
                model_provider="Local Kernel",
                capabilities=["wheel_packaging", "edge_deployment", "clean_room_testing"],
                status="idle",
                icon="🚀",
                source="Sovereign Kernel Fallback",
            ),
        ]
        for fb in fallbacks:
            fleet[fb.agent_id] = fb

    @staticmethod
    def _icon_for_agent(agent_id: str) -> str:
        """Assign visual cue icons based on functional keywords."""
        a = agent_id.lower()
        if "orch" in a or "lead" in a:
            return "👑"
        if "rev" in a or "guard" in a or "sentinel" in a or "audit" in a:
            return "🛡️"
        if "ui" in a or "front" in a or "design" in a or "content" in a:
            return "🎨"
        if "back" in a or "api" in a or "engine" in a:
            return "⚙️"
        if "ci" in a or "bot" in a or "worker" in a:
            return "🤖"
        if "test" in a or "qa" in a:
            return "🧪"
        if "edge" in a or "deploy" in a:
            return "🚀"
        if "mesh" in a or "net" in a:
            return "🌐"
        return "⚡"
