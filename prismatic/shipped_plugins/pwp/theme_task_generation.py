"""PWP Theme Task Generation — Canonical task plans for PWP theme operations.

Provides task-manager-neutral canonical task-plan types, immutable routing decision reuse,
and fail-closed validation semantics for Prismatic Web Plugin theme generation.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from prismatic.capability_router import (
    AgentCapability,
    CapabilityRegistry,
    RouteDecision,
    route_issue,
)


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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("ThemeTaskPlan is final and cannot be subclassed")

    def __post_init__(self) -> None:
        if type(self) is not ThemeTaskPlan:
            raise TaskPlanValidationError("ThemeTaskPlan subclasses are not supported")
        for field_name in ("plan_id", "title", "description"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip() or value != value.strip():
                raise TaskPlanValidationError(
                    f"{field_name} must be a non-empty string"
                )
        if type(self.priority) is not int or not (1 <= self.priority <= 5):
            raise TaskPlanValidationError("priority must be an integer between 1 and 5")
        if type(self.requires_gpu) is not bool:
            raise TaskPlanValidationError("requires_gpu must be a boolean")
        if type(self.dispatch_state) is not DispatchState:
            raise TaskPlanValidationError("dispatch_state must be a DispatchState")
        if type(self.route_decision) is not RouteDecision:
            raise TaskPlanValidationError("route_decision must be a RouteDecision")
        if type(self.capability_requirements) is not frozenset or any(
            type(capability) is not str
            or not capability.strip()
            or capability != capability.strip().lower()
            for capability in self.capability_requirements
        ):
            raise TaskPlanValidationError(
                "capability_requirements must be a normalized frozenset of strings"
            )
        for field_name, allow_empty in (
            ("dependency_ids", True),
            ("contracts", True),
            ("files", True),
            ("verifiers", False),
            ("base_labels", True),
        ):
            value = getattr(self, field_name)
            if (
                type(value) is not tuple
                or _validate_string_sequence(value, field_name, allow_empty=allow_empty)
                != value
            ):
                raise TaskPlanValidationError(
                    f"{field_name} must be a normalized tuple of strings"
                )
        if any(label.lower().startswith("agent:") for label in self.base_labels):
            raise TaskPlanValidationError(
                "manual agent labels are prohibited; routing must select the agent"
            )
        if self.parent_id is not None and (
            type(self.parent_id) is not str
            or not self.parent_id.strip()
            or self.parent_id != self.parent_id.strip()
        ):
            raise TaskPlanValidationError(
                "parent_id must be None or a non-empty string"
            )
        decision = self.route_decision
        if type(decision.candidates) is not tuple:
            raise TaskPlanValidationError("route_decision.candidates must be a tuple")
        if (
            type(decision.reason) is not str
            or not decision.reason.strip()
            or decision.reason != decision.reason.strip()
        ):
            raise TaskPlanValidationError(
                "route_decision.reason must be a normalized non-empty string"
            )
        seen_names: set[str] = set()
        seen_labels: set[str] = set()
        for candidate in decision.candidates:
            self._validate_agent_candidate(candidate)
            if candidate.name in seen_names or candidate.label in seen_labels:
                raise TaskPlanValidationError(
                    "route_decision candidates must have unique names and labels"
                )
            seen_names.add(candidate.name)
            seen_labels.add(candidate.label)
        if decision.selected is not None:
            self._validate_agent_candidate(decision.selected)
            if decision.selected not in decision.candidates:
                raise TaskPlanValidationError(
                    "route_decision.selected must be present in route_decision.candidates"
                )

    @staticmethod
    def _validate_agent_candidate(candidate: AgentCapability) -> None:
        if type(candidate) is not AgentCapability:
            raise TaskPlanValidationError(
                "route_decision candidates must be exact AgentCapability values"
            )
        if (
            type(candidate.name) is not str
            or not candidate.name.strip()
            or candidate.name != candidate.name.strip()
        ):
            raise TaskPlanValidationError(
                "route candidate name must be a normalized non-empty string"
            )
        expected_label = f"agent:{candidate.name}"
        if type(candidate.label) is not str or candidate.label != expected_label:
            raise TaskPlanValidationError(
                f"route candidate label must equal {expected_label!r}"
            )
        if type(candidate.capabilities) is not frozenset or any(
            type(capability) is not str
            or not capability
            or capability != capability.strip().lower()
            for capability in candidate.capabilities
        ):
            raise TaskPlanValidationError(
                "route candidate capabilities must be normalized strings in a frozenset"
            )
        for field_name in ("max_concurrent", "current_load", "priority"):
            if type(getattr(candidate, field_name)) is not int:
                raise TaskPlanValidationError(
                    f"route candidate {field_name} must be an integer"
                )
        if candidate.max_concurrent < 0 or candidate.current_load < 0:
            raise TaskPlanValidationError(
                "route candidate capacity values must be non-negative"
            )
        for field_name in ("gpu_capable", "available"):
            if type(getattr(candidate, field_name)) is not bool:
                raise TaskPlanValidationError(
                    f"route candidate {field_name} must be a boolean"
                )
        if type(candidate.metadata) is not dict:
            raise TaskPlanValidationError("route candidate metadata must be a dict")

    def _has_exactly_one_eligible_agent(self) -> bool:
        decision = self.route_decision
        if decision.selected is None or len(decision.candidates) != 1:
            return False
        selected = decision.candidates[0]
        if decision.selected != selected:
            return False
        if not selected.available or selected.remaining_capacity <= 0:
            return False
        if not self.capability_requirements.issubset(selected.capabilities):
            return False
        if self.requires_gpu and not selected.gpu_capable:
            return False
        return True

    @property
    def owner(self) -> str | None:
        """Owner agent derived from one unambiguous, eligible routing decision."""
        selected = self.route_decision.selected
        if self._has_exactly_one_eligible_agent() and selected is not None:
            return selected.name
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
        return self._has_exactly_one_eligible_agent()

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

    def linear_issue_input(
        self,
        *,
        label_ids_by_name: dict[str, str],
        parent_issue_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a Linear ``IssueCreateInput`` payload derived from this plan.

        Linear label and parent UUIDs are deliberately resolved outside the canonical plan.
        Missing adapter mappings fail closed instead of leaking task-manager identifiers into
        the canonical representation.
        """
        if type(label_ids_by_name) is not dict:
            raise TaskPlanValidationError("label_ids_by_name must be a dict")
        emitted_labels = self.emitted_labels
        missing_labels = [
            label
            for label in emitted_labels
            if type(label_ids_by_name.get(label)) is not str
            or not label_ids_by_name[label].strip()
        ]
        if missing_labels:
            raise TaskPlanValidationError(
                "missing Linear label IDs for: " + ", ".join(missing_labels)
            )
        if self.parent_id and (
            type(parent_issue_id) is not str or not parent_issue_id.strip()
        ):
            raise TaskPlanValidationError(
                "parent_issue_id is required when the canonical plan has a parent_id"
            )
        if not self.parent_id and parent_issue_id is not None:
            raise TaskPlanValidationError(
                "parent_issue_id must be omitted when the canonical plan has no parent_id"
            )

        linear_priority = {1: 1, 2: 2, 3: 3, 4: 4, 5: 0}[self.priority]
        input_dict: dict[str, Any] = {
            "title": self.title,
            "description": self.rendered_description,
            "priority": linear_priority,
            "labelIds": [label_ids_by_name[label].strip() for label in emitted_labels],
        }
        if parent_issue_id is not None:
            input_dict["parentId"] = parent_issue_id.strip()
        return input_dict


def _validate_string_sequence(
    items: Iterable[Any], field_name: str, allow_empty: bool = True
) -> tuple[str, ...]:
    if items is None:
        if allow_empty:
            return ()
        raise TaskPlanValidationError(f"{field_name} must not be None")
    if type(items) not in (list, tuple, set, frozenset):
        raise TaskPlanValidationError(
            f"{field_name} must be a sequence or set of strings"
        )
    result: list[str] = []
    for idx, item in enumerate(items):
        if type(item) is not str:
            raise TaskPlanValidationError(
                f"{field_name}[{idx}] must be a string, got {type(item).__name__}"
            )
        stripped = item.strip()
        if stripped and stripped not in result:
            result.append(stripped)
    if type(items) in (set, frozenset):
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
    if type(plan_id) is not str or not plan_id.strip():
        raise TaskPlanValidationError("plan_id must be a non-empty string")
    if type(title) is not str or not title.strip():
        raise TaskPlanValidationError("title must be a non-empty string")
    if type(description) is not str or not description.strip():
        raise TaskPlanValidationError("description must be a non-empty string")
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

    if parent_id is not None and type(parent_id) is not str:
        raise TaskPlanValidationError("parent_id must be a string or None")

    if dispatch_state is None:
        dispatch_state = DispatchState()
    elif type(dispatch_state) is not DispatchState:
        raise TaskPlanValidationError("dispatch_state must be a DispatchState instance")

    if registry is not None and type(registry) is not CapabilityRegistry:
        raise TaskPlanValidationError("registry must be a CapabilityRegistry instance")

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
        description=description.strip(),
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
    if type(theme_name) is not str or not theme_name.strip():
        raise TaskPlanValidationError("theme_name must be a non-empty string")
    clean_phases = _validate_string_sequence(phases, "phases", allow_empty=False)
    if dispatch_state is None:
        state = DispatchState()
    elif type(dispatch_state) is not DispatchState:
        raise TaskPlanValidationError("dispatch_state must be a DispatchState instance")
    else:
        state = dispatch_state
    if registry is not None and type(registry) is not CapabilityRegistry:
        raise TaskPlanValidationError("registry must be a CapabilityRegistry instance")

    plans: list[ThemeTaskPlan] = []
    clean_name = theme_name.strip()

    phase_configs: dict[str, dict[str, Any]] = {
        "tokens": {
            "title": f"[PWP] Design Tokens Definition for {clean_name}",
            "description": f"Define and compile W3C-compliant design tokens for theme {clean_name}.",
            "capability_requirements": ["code", "docs"],
            "verifiers": [
                "pytest -q plugins/pwp/tests/test_compiler_determinism.py",
                "pytest -q plugins/pwp/tests/test_theme_diff.py",
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
            "verifiers": [
                "pytest -q plugins/pwp/tests/test_theme_validator.py",
                "pytest -q plugins/pwp/tests/test_theme_diff.py",
            ],
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
            "verifiers": [
                "pytest -q plugins/pwp/tests/test_theme_task_generation.py",
            ],
            "contracts": ["EmDash editable field map", "Locked compliance field rules"],
            "files": [f"plugins/pwp/themes/{clean_name}/emdash/fields.json"],
        },
        "qa": {
            "title": f"[PWP] Theme Validation QA for {clean_name}",
            "description": f"Execute repository theme validation and deterministic diff checks for theme {clean_name}.",
            "capability_requirements": ["review", "test"],
            "verifiers": [
                "pytest -q plugins/pwp/tests/test_theme_validator.py",
                "pytest -q plugins/pwp/tests/test_theme_diff.py",
            ],
            "contracts": [
                "PWP theme schema validation",
                "Deterministic theme diff output",
            ],
            "files": [f"plugins/pwp/themes/{clean_name}/reports/qa.json"],
        },
    }

    for phase in clean_phases:
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
