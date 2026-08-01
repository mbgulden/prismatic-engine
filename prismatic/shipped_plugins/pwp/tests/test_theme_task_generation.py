"""Tests for PWP Theme Task Generation module."""

from __future__ import annotations

from dataclasses import replace

import plugins.pwp.theme_task_generation as theme_tasks
import pytest
from plugins.pwp.theme_task_generation import (
    DispatchState,
    TaskPlanValidationError,
    ThemeTaskPlan,
    create_theme_task_plan,
    generate_pwp_theme_task_plans,
)

from prismatic.capability_router import (
    AgentCapability,
    CapabilityRegistry,
    RouteDecision,
)


def make_test_registry() -> CapabilityRegistry:
    """Helper to build an explicit test capability registry."""
    return CapabilityRegistry(
        [
            AgentCapability(
                name="test-coder",
                label="agent:test-coder",
                capabilities=frozenset({"code"}),
                max_concurrent=2,
                current_load=0,
                priority=10,
            ),
            AgentCapability(
                name="test-docser",
                label="agent:test-docser",
                capabilities=frozenset({"docs"}),
                max_concurrent=2,
                current_load=0,
                priority=20,
            ),
            AgentCapability(
                name="test-reviewer",
                label="agent:test-reviewer",
                capabilities=frozenset({"review", "test"}),
                max_concurrent=1,
                current_load=0,
                priority=30,
            ),
        ]
    )


def make_batch_registry() -> CapabilityRegistry:
    """Registry that intentionally makes code-only routing ambiguous."""
    return CapabilityRegistry(
        [
            AgentCapability(
                name="test-coder",
                label="agent:test-coder",
                capabilities=frozenset({"code"}),
                max_concurrent=2,
                current_load=0,
                priority=10,
            ),
            AgentCapability(
                name="test-docser",
                label="agent:test-docser",
                capabilities=frozenset({"docs"}),
                max_concurrent=2,
                current_load=0,
                priority=20,
            ),
            AgentCapability(
                name="test-theme-builder",
                label="agent:test-theme-builder",
                capabilities=frozenset({"code", "docs"}),
                max_concurrent=2,
                current_load=0,
                priority=40,
            ),
        ]
    )


def test_canonical_plan_creation_with_explicit_registry():
    registry = make_test_registry()
    plan = create_theme_task_plan(
        plan_id="pwp-task-001",
        title="Implement Theme Layout",
        description="  Build base layout component for trust-light theme.  ",
        verifiers=["npm run build"],
        capability_requirements=["code"],
        registry=registry,
    )

    assert plan.plan_id == "pwp-task-001"
    assert plan.title == "Implement Theme Layout"
    assert plan.description == "Build base layout component for trust-light theme."
    assert plan.owner == "test-coder"
    assert plan.route_decision is not None
    assert plan.route_decision.selected is not None
    assert plan.route_decision.selected.name == "test-coder"


def test_route_issue_resolved_exactly_once_and_reused(monkeypatch):
    registry = make_test_registry()
    real_route_issue = theme_tasks.route_issue
    calls = []

    def counting_route_issue(issue, *, registry=None):
        calls.append(issue)
        return real_route_issue(issue, registry=registry)

    monkeypatch.setattr(theme_tasks, "route_issue", counting_route_issue)
    plan = create_theme_task_plan(
        plan_id="pwp-task-002",
        title="Write Documentation",
        description="Document theme tokens.",
        verifiers=["pytest tests/"],
        capability_requirements=["docs"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        registry=registry,
    )

    assert len(calls) == 1
    decision = plan.route_decision
    assert decision.selected is not None
    assert decision.selected.name == "test-docser"

    assert plan.owner == "test-docser"
    assert "test-docser" in plan.rendered_description
    assert "agent:test-docser" in plan.emitted_labels

    linear_input = plan.linear_issue_input(
        label_ids_by_name={"agent:test-docser": "linear-label-docser"}
    )
    assert linear_input["labelIds"] == ["linear-label-docser"]
    assert linear_input["title"] == "Write Documentation"
    assert len(calls) == 1


def test_held_plan_emits_no_agent_label():
    registry = make_test_registry()
    plan = create_theme_task_plan(
        plan_id="pwp-task-003",
        title="Theme Component Refactor",
        description="Refactor button styles.",
        verifiers=["npm run check:theme"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True, held=True
        ),
        registry=registry,
    )

    assert plan.dispatch_state.held is True
    assert plan.is_dispatchable is False
    assert plan.owner == "test-coder"  # Owner metadata is preserved for information
    assert not any(label.startswith("agent:") for label in plan.emitted_labels)
    assert not any(
        label.startswith("agent:")
        for label in plan.linear_issue_input(label_ids_by_name={})["labelIds"]
    )


def test_agent_label_emitted_only_when_all_readiness_conditions_met():
    registry = make_test_registry()

    # Case 1: missing build_initiated
    plan1 = create_theme_task_plan(
        plan_id="task-1",
        title="Title",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=False, operator_approved=True, dispatch_ready=True
        ),
        registry=registry,
    )
    assert plan1.is_dispatchable is False
    assert not any(label.startswith("agent:") for label in plan1.emitted_labels)

    # Case 2: missing operator_approved
    plan2 = create_theme_task_plan(
        plan_id="task-2",
        title="Title",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=False, dispatch_ready=True
        ),
        registry=registry,
    )
    assert plan2.is_dispatchable is False
    assert not any(label.startswith("agent:") for label in plan2.emitted_labels)

    # Case 3: missing dispatch_ready
    plan3 = create_theme_task_plan(
        plan_id="task-3",
        title="Title",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=False
        ),
        registry=registry,
    )
    assert plan3.is_dispatchable is False
    assert not any(label.startswith("agent:") for label in plan3.emitted_labels)

    # Case 4: all ready
    plan4 = create_theme_task_plan(
        plan_id="task-4",
        title="Title",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        registry=registry,
    )
    assert plan4.is_dispatchable is True
    assert "agent:test-coder" in plan4.emitted_labels


def test_fail_closed_on_empty_verifiers():
    registry = make_test_registry()

    with pytest.raises(TaskPlanValidationError, match="verifiers must not be empty"):
        create_theme_task_plan(
            plan_id="task-err-1",
            title="Title",
            description="Desc",
            verifiers=[],
            registry=registry,
        )

    with pytest.raises(TaskPlanValidationError, match="verifiers must not be empty"):
        create_theme_task_plan(
            plan_id="task-err-2",
            title="Title",
            description="Desc",
            verifiers=["   ", "\t"],
            registry=registry,
        )


def test_fail_closed_on_manually_supplied_agent_labels():
    registry = make_test_registry()

    with pytest.raises(TaskPlanValidationError, match="manually supplied agent label"):
        create_theme_task_plan(
            plan_id="task-err-3",
            title="Title",
            description="Desc",
            verifiers=["npm run test"],
            raw_labels=["agent:agy"],
            registry=registry,
        )

    with pytest.raises(TaskPlanValidationError, match="manually supplied agent label"):
        create_theme_task_plan(
            plan_id="task-err-4",
            title="Title",
            description="Desc",
            verifiers=["npm run test"],
            raw_labels=["AGENT:CUSTOM"],
            registry=registry,
        )


def test_fail_closed_on_malformed_nested_values():
    registry = make_test_registry()

    with pytest.raises(
        TaskPlanValidationError, match="verifiers\\[0\\] must be a string"
    ):
        create_theme_task_plan(
            plan_id="task-err-5",
            title="Title",
            description="Desc",
            verifiers=[123],  # type: ignore
            registry=registry,
        )

    with pytest.raises(TaskPlanValidationError, match="priority must be an integer"):
        create_theme_task_plan(
            plan_id="task-err-6",
            title="Title",
            description="Desc",
            verifiers=["v1"],
            priority=10,  # Invalid priority out of bounds
            registry=registry,
        )


def test_fail_closed_on_non_boolean_dispatch_and_gpu_values():
    registry = make_test_registry()

    with pytest.raises(
        TaskPlanValidationError, match="build_initiated must be a boolean"
    ):
        DispatchState(build_initiated="yes")  # type: ignore[arg-type]

    with pytest.raises(TaskPlanValidationError, match="priority must be an integer"):
        create_theme_task_plan(
            plan_id="task-bool-priority",
            title="Title",
            description="Desc",
            verifiers=["v1"],
            priority=True,  # type: ignore[arg-type]
            registry=registry,
        )

    with pytest.raises(TaskPlanValidationError, match="requires_gpu must be a boolean"):
        create_theme_task_plan(
            plan_id="task-bad-gpu",
            title="Title",
            description="Desc",
            verifiers=["v1"],
            requires_gpu=1,  # type: ignore[arg-type]
            registry=registry,
        )


def test_unordered_inputs_are_normalized_deterministically():
    plan = create_theme_task_plan(
        plan_id="task-deterministic",
        title="Title",
        description="Desc",
        verifiers={"v2", "v1"},
        capability_requirements={"DOCS", "code"},
        dependency_ids={"dep-b", "dep-a"},
        raw_labels={"z-label", "a-label"},
        registry=make_test_registry(),
    )

    assert plan.verifiers == ("v1", "v2")
    assert plan.dependency_ids == ("dep-a", "dep-b")
    assert plan.base_labels == ("a-label", "z-label")
    assert plan.capability_requirements == frozenset({"code", "docs"})


def test_fail_closed_on_no_capacity_or_unresolved_routing():
    # Registry with full capacity (0 remaining capacity)
    full_registry = CapabilityRegistry(
        [
            AgentCapability(
                name="busy-coder",
                label="agent:busy-coder",
                capabilities=frozenset({"code"}),
                max_concurrent=1,
                current_load=1,
            )
        ]
    )

    plan = create_theme_task_plan(
        plan_id="task-no-cap",
        title="Code Task",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        registry=full_registry,
    )

    assert plan.route_decision.selected is None
    assert plan.owner is None
    assert plan.is_dispatchable is False
    assert not any(label.startswith("agent:") for label in plan.emitted_labels)


def test_fail_closed_on_multiple_eligible_agents():
    registry = CapabilityRegistry(
        [
            AgentCapability(
                name="coder-a",
                label="agent:coder-a",
                capabilities=frozenset({"code"}),
                max_concurrent=1,
                current_load=0,
            ),
            AgentCapability(
                name="coder-b",
                label="agent:coder-b",
                capabilities=frozenset({"code"}),
                max_concurrent=1,
                current_load=0,
            ),
        ]
    )
    plan = create_theme_task_plan(
        plan_id="task-ambiguous",
        title="Code Task",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        registry=registry,
    )

    assert len(plan.route_decision.candidates) == 2
    assert plan.route_decision.selected is not None
    assert plan.owner is None
    assert plan.is_dispatchable is False
    assert not any(label.startswith("agent:") for label in plan.emitted_labels)


def test_direct_canonical_construction_preserves_fail_closed_invariants():
    valid = create_theme_task_plan(
        plan_id="task-direct",
        title="Code Task",
        description="Desc",
        verifiers=["v1"],
        capability_requirements=["code"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        registry=make_test_registry(),
    )

    with pytest.raises(TaskPlanValidationError, match="manual agent labels"):
        replace(valid, base_labels=("agent:manual",))
    with pytest.raises(TaskPlanValidationError, match="verifiers must not be empty"):
        replace(valid, verifiers=())
    with pytest.raises(TaskPlanValidationError, match="must be present"):
        replace(
            valid,
            route_decision=RouteDecision(
                selected=valid.route_decision.selected,
                candidates=(),
                reason="malformed direct decision",
            ),
        )

    selected = valid.route_decision.selected
    assert selected is not None
    malformed_decisions = [
        RouteDecision(
            selected=AgentCapability(
                name="", label="", capabilities=frozenset({"code"})
            ),
            candidates=(
                AgentCapability(name="", label="", capabilities=frozenset({"code"})),
            ),
            reason="empty identity",
        ),
        RouteDecision(
            selected=selected,
            candidates=[selected],  # type: ignore[arg-type]
            reason="list candidates",
        ),
        RouteDecision(
            selected=selected,
            candidates=(selected,),
            reason=None,  # type: ignore[arg-type]
        ),
        RouteDecision(
            selected=AgentCapability(
                name="coder",
                label="agent:other",
                capabilities=frozenset({"code"}),
            ),
            candidates=(
                AgentCapability(
                    name="coder",
                    label="agent:other",
                    capabilities=frozenset({"code"}),
                ),
            ),
            reason="mismatched label",
        ),
        RouteDecision(
            selected=selected,
            candidates=(selected, selected),
            reason="duplicate candidates",
        ),
    ]
    for decision in malformed_decisions:
        with pytest.raises(TaskPlanValidationError):
            replace(valid, route_decision=decision)

    class BadString(str):
        pass  # type: ignore[empty-body]

    class SpoofAgent(AgentCapability):
        @property
        def remaining_capacity(self) -> int:
            return 1  # Always spoof capacity

    malformed_decisions = [
        # Reported in final secondary review:
        RouteDecision(
            selected=AgentCapability(
                name=BadString(" "),  # type: ignore[arg-type]
                label=BadString("agent: "),  # type: ignore[arg-type]
                capabilities=frozenset({"code"}),
            ),
            candidates=(
                AgentCapability(
                    name=BadString(" "),  # type: ignore[arg-type]
                    label=BadString("agent: "),  # type: ignore[arg-type]
                    capabilities=frozenset({"code"}),
                ),
            ),
            reason="bad string subclass name/label",
        ),
        RouteDecision(
            selected=SpoofAgent(
                name="spoof",
                label="agent:spoof",
                capabilities=frozenset({"code"}),
                max_concurrent=1,
                current_load=1,
            ),
            candidates=(
                SpoofAgent(
                    name="spoof",
                    label="agent:spoof",
                    capabilities=frozenset({"code"}),
                    max_concurrent=1,
                    current_load=1,
                ),
            ),
            reason="spoofed capacity",
        ),
        # Existing Repair 5 regressions:
        RouteDecision(
            selected=AgentCapability(
                name="", label="", capabilities=frozenset({"code"})
            ),
            candidates=(
                AgentCapability(name="", label="", capabilities=frozenset({"code"})),
            ),
            reason="empty identity",
        ),
        RouteDecision(
            selected=selected,
            candidates=[selected],  # type: ignore[arg-type]
            reason="list candidates",
        ),
        RouteDecision(
            selected=selected,
            candidates=(selected,),
            reason=None,  # type: ignore[arg-type]
        ),
        RouteDecision(
            selected=AgentCapability(
                name="coder",
                label="agent:other",
                capabilities=frozenset({"code"}),
            ),
            candidates=(
                AgentCapability(
                    name="coder",
                    label="agent:other",
                    capabilities=frozenset({"code"}),
                ),
            ),
            reason="mismatched label",
        ),
        RouteDecision(
            selected=selected,
            candidates=(selected, selected),
            reason="duplicate candidates",
        ),
    ]
    for decision in malformed_decisions:
        with pytest.raises(TaskPlanValidationError):
            replace(valid, route_decision=decision)

    # Reject ThemeTaskPlan subclasses before they can override __post_init__.
    with pytest.raises(TypeError, match="ThemeTaskPlan is final"):

        class BadThemeTaskPlan(ThemeTaskPlan):
            pass

    class BadDispatchState(DispatchState):
        pass

    with pytest.raises(
        TaskPlanValidationError, match="dispatch_state must be a DispatchState instance"
    ):
        create_theme_task_plan(
            plan_id="ds-subclass",
            title="Test DS",
            description="Test DS",
            verifiers=["v1"],
            capability_requirements=frozenset(),
            dispatch_state=BadDispatchState(),  # type: ignore[arg-type]
            registry=make_test_registry(),
        )

    # Validate direct RouteDecision subclassing
    class BadRouteDecision(RouteDecision):
        pass

    with pytest.raises(
        TaskPlanValidationError, match="route_decision must be a RouteDecision"
    ):
        replace(
            valid,
            route_decision=BadRouteDecision(
                selected=None, candidates=(), reason="test"
            ),
        )

    # Validate direct AgentCapability subclassing via _validate_agent_candidate
    class BadAgentCapability(AgentCapability):
        pass

    with pytest.raises(TaskPlanValidationError, match="exact AgentCapability values"):
        ThemeTaskPlan._validate_agent_candidate(
            BadAgentCapability(name="a", label="agent:a", capabilities=frozenset())
        )

    busy = AgentCapability(
        name="busy",
        label="agent:busy",
        capabilities=frozenset({"code"}),
        max_concurrent=1,
        current_load=1,
    )
    direct = replace(
        valid,
        route_decision=RouteDecision(
            selected=busy, candidates=(busy,), reason="direct full-capacity decision"
        ),
    )
    assert isinstance(direct, ThemeTaskPlan)
    assert direct.owner is None
    assert direct.is_dispatchable is False
    assert not any(label.startswith("agent:") for label in direct.emitted_labels)


def test_generator_rejects_subclassed_identity_and_state_inputs():
    class BadString(str):
        pass

    class FalseyDispatchState(DispatchState):
        def __bool__(self) -> bool:
            return False

    class BadRegistry(CapabilityRegistry):
        pass

    with pytest.raises(TaskPlanValidationError, match="theme_name"):
        generate_pwp_theme_task_plans(
            BadString("trust-light"), phases=("tokens",), registry=make_test_registry()
        )
    with pytest.raises(TaskPlanValidationError, match=r"phases\[0\]"):
        generate_pwp_theme_task_plans(
            "trust-light",
            phases=(BadString("tokens"),),
            registry=make_test_registry(),
        )
    with pytest.raises(TaskPlanValidationError, match="dispatch_state"):
        generate_pwp_theme_task_plans(
            "trust-light",
            phases=("tokens",),
            dispatch_state=FalseyDispatchState(
                build_initiated=True,
                operator_approved=True,
                dispatch_ready=True,
            ),
            registry=make_test_registry(),
        )
    with pytest.raises(TaskPlanValidationError, match="registry"):
        generate_pwp_theme_task_plans(
            "trust-light",
            phases=("tokens",),
            registry=BadRegistry(),
        )


def test_linear_issue_input_adapter():
    registry = make_test_registry()
    plan = create_theme_task_plan(
        plan_id="PWP-100",
        title="Build Theme System",
        description="Core theme system task.",
        verifiers=["pytest tests/"],
        contracts=["Contract A"],
        files=["file1.py"],
        parent_id="PWP-PARENT",
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        capability_requirements=["code"],
        raw_labels=["pwp:theme"],
        registry=registry,
    )

    linear_adapter = plan.linear_issue_input(
        label_ids_by_name={
            "pwp:theme": "linear-label-theme",
            "agent:test-coder": "linear-label-coder",
        },
        parent_issue_id="linear-parent-uuid",
    )

    assert "identifier" not in linear_adapter
    assert "labels" not in linear_adapter
    assert linear_adapter["title"] == "Build Theme System"
    assert linear_adapter["parentId"] == "linear-parent-uuid"
    assert linear_adapter["priority"] == 3
    assert linear_adapter["labelIds"] == [
        "linear-label-theme",
        "linear-label-coder",
    ]
    assert "Contract A" in linear_adapter["description"]

    with pytest.raises(TaskPlanValidationError, match="missing Linear label IDs"):
        plan.linear_issue_input(label_ids_by_name={})
    with pytest.raises(TaskPlanValidationError, match="parent_issue_id is required"):
        plan.linear_issue_input(
            label_ids_by_name={
                "pwp:theme": "linear-label-theme",
                "agent:test-coder": "linear-label-coder",
            }
        )


def test_generate_pwp_theme_task_plans_rejects_unknown_phase():
    with pytest.raises(TaskPlanValidationError, match="unknown theme phase"):
        generate_pwp_theme_task_plans(
            "trust-light",
            phases=["unknown-lane"],
            registry=make_test_registry(),
        )


def test_generate_pwp_theme_task_plans_batch():
    registry = make_batch_registry()
    plans = generate_pwp_theme_task_plans(
        "trust-light",
        phases=["tokens", "modules"],
        dispatch_state=DispatchState(
            build_initiated=True, operator_approved=True, dispatch_ready=True
        ),
        registry=registry,
    )

    assert len(plans) == 2
    assert plans[0].plan_id == "pwp-theme-trust-light-tokens"
    assert plans[1].plan_id == "pwp-theme-trust-light-modules"
    assert plans[0].owner == "test-theme-builder"
    assert plans[0].is_dispatchable is True
    assert plans[1].owner is None
    assert plans[1].is_dispatchable is False
    assert len(plans[1].route_decision.candidates) == 2
    assert not any(label.startswith("agent:") for label in plans[1].emitted_labels)
