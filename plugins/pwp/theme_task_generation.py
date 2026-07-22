"""PWP Theme Task Generation — Canonical task plans for PWP theme operations.

Provides task-manager-neutral canonical task-plan types, immutable routing decision reuse,
and fail-closed validation semantics for Prismatic Web Plugin theme generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from prismatic.capability_router import CapabilityRegistry, RouteDecision, route_issue


class TaskPlanValidationError(ValueError):
    """Raised when task plan inputs violate validation rules or fail closed."""


@dataclass(frozen=True)
class DispatchState:
    """Task-manager-neutral state of task readiness for automated execution."""

    build_initiated: bool = False
    operator_approved: bool = False
    dispatch_ready: bool = False
    held: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "build_initiated",
            "operator_approved",
            "dispatch_ready",
            "held",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TaskPlanValidationError(f"{field_name} must be a boolean")


@dataclass(frozen=True)
class ThemeTaskPlan:
    """Canonical task-manager-neutral theme task plan.

    Contains specification details, contracts, verifiers, dispatch state,
    and exactly one immutable RouteDecision resolved at plan construction time.
    """

    plan_id: str
    title: str
    description: str
    priority: int
    capability_requirements: frozenset[str]
    requires_gpu: bool
    parent_id: str | None
    dependency_ids: tuple[str, ...]
    contracts: tuple[str, ...]
    files: tuple[str, ...]
    verifiers: tuple[str, ...]
    dispatch_state: DispatchState
    route_decision: RouteDecision
    base_labels: tuple[str, ...]

    @property
    def owner(self) -> str | None:
        """Owner agent derived from the immutable routing decision."""
        if self.route_decision and self.route_decision.selected:
            return self.route_decision.selected.name
        return None

    @property
    def is_dispatchable(self) -> bool:
        """True only if all dispatch readiness criteria are met and routing is cleanly resolved."""
        if self.dispatch_state.held:
            return False
        if not (
            self.dispatch_state.build_initiated
            and self.dispatch_state.operator_approved
            and self.dispatch_state.dispatch_ready
        ):
            return False
        if self.route_decision is None or self.route_decision.selected is None:
            return False
        return True

    @property
    def emitted_labels(self) -> tuple[str, ...]:
        """Compute labels for task dispatch.

        Emits no agent:* label if plan is held, unapproved, uninitiated, not ready, or routing is unresolved.
        Emits exactly one agent:<name> label if all dispatch criteria are met and routing resolved a single agent.
        """
        labels: list[str] = list(self.base_labels)
        if (
            self.is_dispatchable
            and self.route_decision
            and self.route_decision.selected
        ):
            selected_agent = self.route_decision.selected
            label_name = selected_agent.label
            if not label_name.startswith("agent:"):
                label_name = f"agent:{selected_agent.name}"
            if label_name not in labels:
                labels.append(label_name)
        return tuple(labels)

    @property
    def rendered_description(self) -> str:
        """Render markdown description incorporating requirements, contracts, verifiers, and routing state."""
        sections: list[str] = []
        if self.description.strip():
            sections.append(self.description.strip())

        if self.contracts:
            contracts_formatted = "\n".join(f"- {c}" for c in self.contracts)
            sections.append(f"### Contracts\n{contracts_formatted}")

        if self.files:
            files_formatted = "\n".join(f"- `{f}`" for f in self.files)
            sections.append(f"### Target Files\n{files_formatted}")

        if self.verifiers:
            verifiers_formatted = "\n".join(f"- `{v}`" for v in self.verifiers)
            sections.append(f"### Verifiers\n{verifiers_formatted}")

        if self.dependency_ids:
            deps_formatted = ", ".join(self.dependency_ids)
            sections.append(f"### Dependencies\n{deps_formatted}")

        owner_str = self.owner or "unassigned (no capacity / unresolved)"
        reason_str = self.route_decision.reason if self.route_decision else "not routed"
        sections.append(
            f"### Routing Metadata\n- Assigned Lane: `{owner_str}`\n- Decision Reason: {reason_str}"
        )

        return "\n\n".join(sections)

    def linear_issue_input(self) -> dict[str, Any]:
        """Linear adapter derived from canonical task plan."""
        input_dict: dict[str, Any] = {
            "title": self.title,
            "description": self.rendered_description,
            "priority": self.priority,
            "labels": list(self.emitted_labels),
        }
        if self.plan_id:
            input_dict["identifier"] = self.plan_id
        if self.parent_id:
            input_dict["parentId"] = self.parent_id
        return input_dict


def _validate_string_sequence(
    items: Iterable[Any], field_name: str, allow_empty: bool = True
) -> tuple[str, ...]:
    if items is None:
        if allow_empty:
            return ()
        raise TaskPlanValidationError(f"{field_name} must not be None")
    if not isinstance(items, (list, tuple, set, frozenset)):
        raise TaskPlanValidationError(
            f"{field_name} must be a sequence or set of strings"
        )
    result: list[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, str):
            raise TaskPlanValidationError(
                f"{field_name}[{idx}] must be a string, got {type(item).__name__}"
            )
        stripped = item.strip()
        if stripped and stripped not in result:
            result.append(stripped)
    if isinstance(items, (set, frozenset)):
        result.sort()
    if not allow_empty and not result:
        raise TaskPlanValidationError(f"{field_name} must not be empty")
    return tuple(result)


def create_theme_task_plan(
    plan_id: str,
    title: str,
    description: str,
    verifiers: Iterable[str],
    *,
    priority: int = 3,
    capability_requirements: Iterable[str] = (),
    requires_gpu: bool = False,
    parent_id: str | None = None,
    dependency_ids: Iterable[str] = (),
    contracts: Iterable[str] = (),
    files: Iterable[str] = (),
    dispatch_state: DispatchState | None = None,
    raw_labels: Iterable[str] = (),
    registry: CapabilityRegistry | None = None,
) -> ThemeTaskPlan:
    """Construct a canonical theme task plan with single-pass immutable routing and fail-closed validation."""
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise TaskPlanValidationError("plan_id must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise TaskPlanValidationError("title must be a non-empty string")
    if not isinstance(description, str):
        raise TaskPlanValidationError("description must be a string")
    if type(priority) is not int or not (1 <= priority <= 5):
        raise TaskPlanValidationError("priority must be an integer between 1 and 5")
    if type(requires_gpu) is not bool:
        raise TaskPlanValidationError("requires_gpu must be a boolean")

    clean_verifiers = _validate_string_sequence(
        verifiers, "verifiers", allow_empty=False
    )
    clean_contracts = _validate_string_sequence(contracts, "contracts")
    clean_files = _validate_string_sequence(files, "files")
    clean_deps = _validate_string_sequence(dependency_ids, "dependency_ids")
    clean_caps = tuple(
        dict.fromkeys(
            capability.lower()
            for capability in _validate_string_sequence(
                capability_requirements, "capability_requirements"
            )
        )
    )

    if parent_id is not None and not isinstance(parent_id, str):
        raise TaskPlanValidationError("parent_id must be a string or None")

    if dispatch_state is None:
        dispatch_state = DispatchState()
    elif not isinstance(dispatch_state, DispatchState):
        raise TaskPlanValidationError("dispatch_state must be a DispatchState instance")

    clean_labels = list(_validate_string_sequence(raw_labels, "raw_labels"))
    for label in clean_labels:
        if label.lower().startswith("agent:"):
            raise TaskPlanValidationError(
                f"manually supplied agent label {label!r} is not allowed"
            )

    # Prepare labels for capability router
    routing_labels = list(clean_labels)
    for cap in clean_caps:
        routing_labels.append(f"capability:{cap}")
    if requires_gpu:
        routing_labels.append("gpu")

    issue_dict: dict[str, Any] = {
        "identifier": plan_id.strip(),
        "title": title.strip(),
        "description": description,
        "labels": routing_labels,
    }

    # Resolve capability_router.route_issue EXACTLY ONCE per plan
    route_decision = route_issue(issue_dict, registry=registry)

    return ThemeTaskPlan(
        plan_id=plan_id.strip(),
        title=title.strip(),
        description=description,
        priority=priority,
        capability_requirements=frozenset(clean_caps),
        requires_gpu=requires_gpu,
        parent_id=parent_id.strip() if parent_id else None,
        dependency_ids=clean_deps,
        contracts=clean_contracts,
        files=clean_files,
        verifiers=clean_verifiers,
        dispatch_state=dispatch_state,
        route_decision=route_decision,
        base_labels=tuple(clean_labels),
    )


def generate_pwp_theme_task_plans(
    theme_name: str,
    *,
    phases: Sequence[str] = ("tokens", "modules", "emdash", "qa"),
    dispatch_state: DispatchState | None = None,
    registry: CapabilityRegistry | None = None,
) -> list[ThemeTaskPlan]:
    """Generate canonical task plans for standard PWP theme lifecycle phases."""
    if not isinstance(theme_name, str) or not theme_name.strip():
        raise TaskPlanValidationError("theme_name must be a non-empty string")

    plans: list[ThemeTaskPlan] = []
    clean_name = theme_name.strip()
    state = dispatch_state or DispatchState()

    phase_configs: dict[str, dict[str, Any]] = {
        "tokens": {
            "title": f"[PWP] Design Tokens Definition for {clean_name}",
            "description": f"Define and compile W3C-compliant design tokens for theme {clean_name}.",
            "capability_requirements": ["code", "docs"],
            "verifiers": [
                "npm run check:theme",
                "pytest plugins/pwp/tests/test_theme_diff.py",
            ],
            "contracts": [
                "W3C design token spec",
                "PWP CSS custom property namespace --pwp-*",
            ],
            "files": [
                f"plugins/pwp/themes/{clean_name}/tokens.json",
                f"plugins/pwp/themes/{clean_name}/theme.css",
            ],
        },
        "modules": {
            "title": f"[PWP] Astro Component Modules for {clean_name}",
            "description": f"Implement typed Astro components and module schemas for theme {clean_name}.",
            "capability_requirements": ["code"],
            "verifiers": ["npm run build", "astro check"],
            "contracts": [
                "Astro component slots interface",
                "Module JSON schema validation",
            ],
            "files": [f"plugins/pwp/themes/{clean_name}/src/components/"],
        },
        "emdash": {
            "title": f"[PWP] EmDash Content Schema & Field Map for {clean_name}",
            "description": f"Map human-editable fields and lock compliance fields for theme {clean_name}.",
            "capability_requirements": ["docs", "content"],
            "verifiers": ["pytest plugins/pwp/tests/test_oauth_credentials.py"],
            "contracts": ["EmDash editable field map", "Locked compliance field rules"],
            "files": [f"plugins/pwp/themes/{clean_name}/emdash/fields.json"],
        },
        "qa": {
            "title": f"[PWP] Visual & Accessibility QA for {clean_name}",
            "description": f"Execute Playwright visual regression and axe-core accessibility checks for theme {clean_name}.",
            "capability_requirements": ["review", "test"],
            "verifiers": ["npm run test:a11y", "npm run test:visual"],
            "contracts": [
                "WCAG 2.2 AA accessibility standard",
                "Lighthouse performance budget",
            ],
            "files": [f"plugins/pwp/themes/{clean_name}/reports/qa.json"],
        },
    }

    for phase in phases:
        if phase not in phase_configs:
            raise TaskPlanValidationError(f"unknown theme phase: {phase!r}")
        config = phase_configs[phase]
        plan = create_theme_task_plan(
            plan_id=f"pwp-theme-{clean_name}-{phase}",
            title=config["title"],
            description=config["description"],
            verifiers=config["verifiers"],
            capability_requirements=config["capability_requirements"],
            contracts=config["contracts"],
            files=config["files"],
            dispatch_state=state,
            raw_labels=["plugin:pwp", "pwp:theme"],
            registry=registry,
        )
        plans.append(plan)

    return plans
