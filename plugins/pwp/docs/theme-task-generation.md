# PWP Canonical Theme Task Plan Generation

**Status:** Canonical Implementation  
**Owner Lane:** Prismatic Web Plugin / Theme Task Pipeline  
**Module:** `plugins.pwp.theme_task_generation`  
**Issue:** GRO-3738  

---

## Executive Summary

The PWP Theme Task Generation module introduces task-manager-neutral canonical task plans for Prismatic Web Plugin (PWP) theme development, verification, and deployment.

It provides a decoupled task planning layer that is independent of Linear or any external issue tracking system. Issue tracker representations (such as Linear issue inputs) are strictly derived as secondary adapters from canonical task plans.

---

## Core Contracts & Architecture

### 1. Canonical Task Plan (`ThemeTaskPlan`)

`ThemeTaskPlan` is an immutable, task-manager-neutral data structure representing a discrete unit of theme engineering work:

- **Identity & Specification:** `plan_id`, `title`, `description`, `priority` (1-5), `parent_id`, `dependency_ids`.
- **Contracts & Constraints:** `contracts`, `files`, `capability_requirements`, `requires_gpu`.
- **Verification Requirement:** `verifiers` (tuple of non-empty verification commands). Must never be empty.
- **Dispatch Readiness:** `dispatch_state` (`DispatchState` containing `build_initiated`, `operator_approved`, `dispatch_ready`, `held`).
- **Immutable Routing Decision:** `route_decision` (`RouteDecision` resolved exactly once at construction time).

### 2. Single Immutable Routing Decision

The routing decision is resolved **exactly once** during task plan construction via `prismatic.capability_router.route_issue`. The resulting `RouteDecision` is held immutably on the `ThemeTaskPlan`.

The decision is reused across all consumer outputs:
- **Canonical Owner Data:** `plan.owner` returns `plan.route_decision.selected.name`.
- **Rendered Markdown Description:** `plan.rendered_description` incorporates routing metadata (`Assigned Lane`, `Decision Reason`).
- **Emitted Dispatch Labels:** `plan.emitted_labels` uses `plan.route_decision.selected.label`.
- **Linear Adapter:** `plan.linear_issue_input()` formats the Linear payload reusing the plan's computed attributes.

---

## Dispatch Readiness & Label Emission Rules

Automated agent dispatch labels (`agent:<name>`) are dynamically emitted according to strict safety rules:

1. **Held Plans Emit NO Dispatch Labels:**  
   If `dispatch_state.held == True`, the plan emits no `agent:*` label under any circumstances.
2. **Readiness Gate:**  
   An `agent:<name>` label is emitted **if and only if** all of the following conditions are met:
   - `dispatch_state.build_initiated == True`
   - `dispatch_state.operator_approved == True`
   - `dispatch_state.dispatch_ready == True`
   - `dispatch_state.held == False`
   - `route_decision.selected` resolved to exactly one eligible agent with capacity.

---

## Fail-Closed Validation Rules

The plan constructor `create_theme_task_plan` enforces fail-closed validation to prevent malformed or ambiguous task execution:

| Condition | Behavior |
|---|---|
| **Empty Verifiers** | Raises `TaskPlanValidationError`. Task plans must specify at least one verification command. |
| **Manually Supplied `agent:*` Labels** | Raises `TaskPlanValidationError`. Manual agent assignment labels in `raw_labels` are prohibited; agent assignment must only occur via the capability router. |
| **Malformed Nested Values** | Raises `TaskPlanValidationError` if inputs (priority, verifiers, contracts, files, labels) have invalid types or bounds. |
| **No Capacity / Unresolved Routing** | `route_decision.selected` is `None`. The plan emits no `agent:*` label and `is_dispatchable` evaluates to `False`. |
| **Unknown Lanes** | If requested capabilities cannot be matched to any available agent lane in the `CapabilityRegistry`, routing fails to resolve and no dispatch label is emitted. |

---

## Testing & Registry Isolation

Per GRO-3738 test rules:
- Tests asserting agent identity must construct an explicit `CapabilityRegistry` instance.
- Tests must never depend on global or mutable default-registry agent configurations.

```python
from prismatic.capability_router import AgentCapability, CapabilityRegistry
from plugins.pwp.theme_task_generation import create_theme_task_plan, DispatchState

test_registry = CapabilityRegistry([
    AgentCapability(
        name="theme-coder",
        label="agent:theme-coder",
        capabilities=frozenset({"code"}),
        max_concurrent=2,
    )
])

plan = create_theme_task_plan(
    plan_id="pwp-001",
    title="Build Hero Component",
    description="Implement Hero.astro component",
    verifiers=["npm run build"],
    capability_requirements=["code"],
    dispatch_state=DispatchState(build_initiated=True, operator_approved=True, dispatch_ready=True),
    registry=test_registry,
)

assert plan.owner == "theme-coder"
assert "agent:theme-coder" in plan.emitted_labels
```

---

## Linear Adapter Usage

Linear issue inputs are generated via the `linear_issue_input()` method:

```python
linear_payload = plan.linear_issue_input()
# Returns:
# {
#     "identifier": "pwp-001",
#     "title": "Build Hero Component",
#     "description": "...",
#     "priority": 3,
#     "labels": ["agent:theme-coder"]
# }
```

The canonical `ThemeTaskPlan` does not require or depend on Linear field structures, ensuring portability to other task runners or execution systems.
