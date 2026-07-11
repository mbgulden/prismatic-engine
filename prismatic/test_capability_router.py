from prismatic.capability_router import (
    AgentCapability,
    CapabilityRegistry,
    CapacityAwareRouter,
    TaskRequest,
    default_capability_registry,
    route_issue,
)


def test_router_selects_least_loaded_matching_capability():
    registry = CapabilityRegistry(
        [
            AgentCapability(
                name="busy",
                label="agent:busy",
                capabilities=frozenset({"code"}),
                max_concurrent=4,
                current_load=3,
                priority=1,
            ),
            AgentCapability(
                name="idle",
                label="agent:idle",
                capabilities=frozenset({"code"}),
                max_concurrent=2,
                current_load=0,
                priority=100,
            ),
        ]
    )

    decision = CapacityAwareRouter(registry).route(
        TaskRequest(issue_id="GRO-1", required_capabilities=frozenset({"code"}))
    )

    assert decision.agent == "idle"
    assert decision.label == "agent:idle"
    assert len(decision.candidates) == 2


def test_router_requires_gpu_capable_lane_for_gpu_work():
    registry = CapabilityRegistry(
        [
            AgentCapability(
                name="cpu-code",
                label="agent:cpu-code",
                capabilities=frozenset({"code", "gpu"}),
                max_concurrent=4,
                current_load=0,
                gpu_capable=False,
            ),
            AgentCapability(
                name="gpu-code",
                label="agent:gpu-code",
                capabilities=frozenset({"code", "gpu"}),
                max_concurrent=1,
                current_load=0,
                gpu_capable=True,
            ),
        ]
    )

    decision = CapacityAwareRouter(registry).route(
        TaskRequest(
            issue_id="GRO-2",
            required_capabilities=frozenset({"code", "gpu"}),
            requires_gpu=True,
        )
    )

    assert decision.agent == "gpu-code"
    assert [candidate.name for candidate in decision.candidates] == ["gpu-code"]


def test_router_returns_explainable_no_route_when_capacity_full():
    registry = CapabilityRegistry(
        [
            AgentCapability(
                name="full",
                label="agent:full",
                capabilities=frozenset({"review"}),
                max_concurrent=1,
                current_load=1,
            )
        ]
    )

    decision = CapacityAwareRouter(registry).route(
        TaskRequest(issue_id="GRO-3", required_capabilities=frozenset({"review"}))
    )

    assert decision.selected is None
    assert decision.candidates == ()
    assert "no eligible agent" in decision.reason


def test_route_issue_uses_explicit_capability_labels_over_keywords():
    registry = CapabilityRegistry(
        [
            AgentCapability(
                name="docs",
                label="agent:docs",
                capabilities=frozenset({"docs"}),
                max_concurrent=1,
            ),
            AgentCapability(
                name="code",
                label="agent:code",
                capabilities=frozenset({"code"}),
                max_concurrent=1,
            ),
        ]
    )

    decision = route_issue(
        {
            "identifier": "GRO-4",
            "title": "Implement docs update",
            "labels": ["dispatch:ready", "capability:docs"],
        },
        registry,
    )

    assert decision.agent == "docs"


def test_default_registry_accepts_legacy_agent_config_overrides():
    registry = default_capability_registry(
        {
            "agy": {
                "capabilities": {"code", "gpu"},
                "max_concurrent": 7,
                "current_load": 6,
                "gpu_capable": True,
            },
            "jules": {
                "capabilities": {"code"},
                "max_concurrent": 2,
                "current_load": 0,
            },
        }
    )

    agy = registry.get("agy")
    jules = registry.get("jules")

    assert agy is not None
    assert agy.max_concurrent == 7
    assert agy.gpu_capable is True
    assert jules is not None
    assert jules.remaining_capacity == 2
