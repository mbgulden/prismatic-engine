"""Capability registry and capacity-aware work routing.

This module is intentionally harness-agnostic: it knows about agent
capabilities, load, and routing constraints, but it does not know how a
Hermes, AGY, Codex, or GPU worker is launched.  Dispatchers can use the
selected ``AgentCapability.label`` to apply Linear labels or signal the
chosen harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TaskRequest:
    """Normalized routing request for a unit of work."""

    issue_id: str
    title: str = ""
    description: str = ""
    labels: tuple[str, ...] = ()
    required_capabilities: frozenset[str] = frozenset()
    requires_gpu: bool = False
    priority: int = 3

    @classmethod
    def from_issue(cls, issue: Mapping[str, Any]) -> "TaskRequest":
        """Build a request from a Linear-like issue dict.

        The issue may contain labels as strings or ``{"name": ...}`` dicts.
        Explicit labels of the form ``capability:<name>`` are authoritative;
        otherwise a small keyword classifier extracts coarse capabilities.
        """

        labels = normalize_labels(issue.get("labels", ()))
        title = str(issue.get("title") or "")
        description = str(issue.get("description") or "")
        explicit_caps = {
            label.split(":", 1)[1].strip().lower()
            for label in labels
            if label.startswith("capability:") and label.split(":", 1)[1].strip()
        }
        inferred_caps = infer_capabilities(title, description, labels)
        capabilities = explicit_caps or inferred_caps
        requires_gpu = (
            "gpu" in capabilities
            or "gpu" in {label.lower() for label in labels}
            or any(
                token in f"{title} {description}".lower()
                for token in ("cuda", "ollama", "vram", "local model", "llm serving")
            )
        )
        priority = infer_priority(labels)
        return cls(
            issue_id=str(issue.get("identifier") or issue.get("id") or ""),
            title=title,
            description=description,
            labels=tuple(labels),
            required_capabilities=frozenset(capabilities),
            requires_gpu=requires_gpu,
            priority=priority,
        )


@dataclass(frozen=True)
class AgentCapability:
    """Routing metadata for one worker lane."""

    name: str
    label: str
    capabilities: frozenset[str]
    max_concurrent: int = 1
    current_load: int = 0
    gpu_capable: bool = False
    available: bool = True
    priority: int = 100
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def remaining_capacity(self) -> int:
        return max(0, self.max_concurrent - self.current_load)

    @property
    def load_ratio(self) -> float:
        if self.max_concurrent <= 0:
            return 1.0
        return min(1.0, self.current_load / self.max_concurrent)

    def can_accept(self, task: TaskRequest) -> bool:
        if not self.available or self.remaining_capacity <= 0:
            return False
        if task.requires_gpu and not self.gpu_capable:
            return False
        return task.required_capabilities.issubset(self.capabilities)


@dataclass(frozen=True)
class RouteDecision:
    """Router result with a machine-readable reason."""

    selected: AgentCapability | None
    candidates: tuple[AgentCapability, ...]
    reason: str

    @property
    def agent(self) -> str | None:
        return self.selected.name if self.selected else None

    @property
    def label(self) -> str | None:
        return self.selected.label if self.selected else None


class CapabilityRegistry:
    """Mutable registry of worker capabilities and live capacity."""

    def __init__(self, agents: Iterable[AgentCapability] = ()) -> None:
        self._agents: dict[str, AgentCapability] = {}
        for agent in agents:
            self.register(agent)

    def register(self, agent: AgentCapability) -> None:
        if not agent.name:
            raise ValueError("agent.name is required")
        if not agent.label:
            raise ValueError("agent.label is required")
        if agent.max_concurrent < 0:
            raise ValueError("agent.max_concurrent must be >= 0")
        if agent.current_load < 0:
            raise ValueError("agent.current_load must be >= 0")
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentCapability | None:
        return self._agents.get(name)

    def list(self) -> list[AgentCapability]:
        return sorted(self._agents.values(), key=lambda a: (a.priority, a.name))

    def with_loads(self, loads: Mapping[str, int]) -> "CapabilityRegistry":
        cloned = CapabilityRegistry(self.list())
        for name, load in loads.items():
            if name in cloned._agents:
                cloned._agents[name] = replace(
                    cloned._agents[name], current_load=max(0, int(load))
                )
        return cloned

    def reserve(self, name: str, slots: int = 1) -> AgentCapability:
        agent = self._agents[name]
        if agent.current_load + slots > agent.max_concurrent:
            raise RuntimeError(f"agent {name!r} has no remaining capacity")
        updated = replace(agent, current_load=agent.current_load + slots)
        self._agents[name] = updated
        return updated


class CapacityAwareRouter:
    """Select the least-loaded eligible lane for a task."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def route(self, task: TaskRequest) -> RouteDecision:
        all_agents = self.registry.list()
        candidates = tuple(agent for agent in all_agents if agent.can_accept(task))
        if not candidates:
            missing = ",".join(sorted(task.required_capabilities)) or "none"
            gpu = "; gpu required" if task.requires_gpu else ""
            return RouteDecision(None, (), f"no eligible agent for capabilities={missing}{gpu}")

        selected = min(
            candidates,
            key=lambda agent: (
                agent.load_ratio,
                -agent.remaining_capacity,
                agent.priority,
                agent.name,
            ),
        )
        return RouteDecision(
            selected,
            candidates,
            (
                f"selected {selected.name}: load={selected.current_load}/"
                f"{selected.max_concurrent}; capabilities="
                f"{','.join(sorted(task.required_capabilities)) or 'none'}"
            ),
        )

    def reserve(self, task: TaskRequest) -> RouteDecision:
        decision = self.route(task)
        if decision.selected is None:
            return decision
        selected = self.registry.reserve(decision.selected.name)
        candidates = tuple(
            selected if agent.name == selected.name else agent
            for agent in decision.candidates
        )
        return RouteDecision(selected, candidates, decision.reason)


def normalize_labels(raw_labels: Iterable[Any]) -> list[str]:
    labels: list[str] = []
    for label in raw_labels or ():
        if isinstance(label, str):
            labels.append(label)
        elif isinstance(label, Mapping):
            name = label.get("name")
            if name:
                labels.append(str(name))
    return labels


def infer_priority(labels: Sequence[str]) -> int:
    lowered = {label.lower() for label in labels}
    if "dispatch:priority" in lowered or "priority:urgent" in lowered:
        return 5
    if "priority:low" in lowered:
        return 1
    return 3


def infer_capabilities(title: str, description: str, labels: Sequence[str]) -> frozenset[str]:
    text = f"{title} {description} {' '.join(labels)}".lower()
    caps: set[str] = set()
    keyword_map = {
        "code": ("build", "bug", "fix", "implement", "refactor", "test", "pytest", "router", "registry"),
        "docs": ("docs", "documentation", "readme", "spec"),
        "review": ("review", "audit", "validate", "second witness"),
        "content": ("content", "copy", "blog", "article", "landing page"),
        "deploy": ("deploy", "cloudflare", "worker", "dns", "ssl"),
        "gpu": ("gpu", "cuda", "ollama", "vram", "local model"),
    }
    for capability, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords):
            caps.add(capability)
    if not caps:
        caps.add("general")
    return frozenset(caps)


def default_capability_registry(
    agent_config: Mapping[str, Mapping[str, Any]] | None = None,
) -> CapabilityRegistry:
    """Build the default Prismatic worker registry.

    ``agent_config`` may be the legacy dispatcher ``AGENT_CONFIG`` mapping.
    Entries can override ``capabilities``, ``max_concurrent``, ``gpu_capable``,
    and ``priority`` without changing this module.
    """

    defaults: dict[str, dict[str, Any]] = {
        "fred": {
            "capabilities": {"general", "orchestration", "review", "docs", "content"},
            "max_concurrent": 3,
            "priority": 30,
        },
        "kai": {
            "capabilities": {"general", "code", "deploy", "content", "review"},
            "max_concurrent": 2,
            "priority": 40,
        },
        "agy": {
            "capabilities": {"general", "code", "docs", "review", "research", "gpu"},
            "max_concurrent": 4,
            "gpu_capable": True,
            "priority": 20,
        },
        "jules": {
            "capabilities": {"general", "code", "test", "review"},
            "max_concurrent": 2,
            "priority": 50,
        },
        "codex": {
            "capabilities": {"general", "code", "test", "review"},
            "max_concurrent": 2,
            "priority": 60,
        },
        "ned": {
            "capabilities": {"general", "code", "infra", "deploy", "gpu", "docs"},
            "max_concurrent": 2,
            "gpu_capable": True,
            "priority": 25,
        },
    }

    config = agent_config or {name: {} for name in defaults}
    agents: list[AgentCapability] = []
    for name, legacy in config.items():
        merged = {**defaults.get(name, {"capabilities": {"general"}}), **dict(legacy)}
        capabilities = frozenset(str(c).lower() for c in merged.get("capabilities", {"general"}))
        label = str(merged.get("label") or f"agent:{name}")
        agents.append(
            AgentCapability(
                name=name,
                label=label,
                capabilities=capabilities,
                max_concurrent=int(merged.get("max_concurrent", 1)),
                current_load=int(merged.get("current_load", 0)),
                gpu_capable=bool(merged.get("gpu_capable", "gpu" in capabilities)),
                available=bool(merged.get("available", True)),
                priority=int(merged.get("priority", 100)),
                metadata=merged,
            )
        )
    return CapabilityRegistry(agents)


def route_issue(
    issue: Mapping[str, Any],
    registry: CapabilityRegistry | None = None,
) -> RouteDecision:
    """Convenience API for routing a Linear-like issue dict."""

    request = TaskRequest.from_issue(issue)
    router = CapacityAwareRouter(registry or default_capability_registry())
    return router.route(request)
