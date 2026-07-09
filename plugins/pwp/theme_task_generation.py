"""Linear task generation policy for PWP theme autopilot work.

The Phase 9 autopilot creates implementation and verifier issues before humans
explicitly start the build.  This module keeps that safe: every generated issue
gets one deterministic owner label for its lane, but ``dispatch:ready`` is held
back until the build has actually been initiated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, TypedDict

BASE_LABELS: tuple[str, ...] = ("plugin:pwp", "prismatic-engine", "Feature")
DISPATCH_READY_LABEL = "dispatch:ready"

LANE_AGENT_LABELS: Mapping[str, str] = {
    "content-strategist": "agent:kai-content",
    "design-theme": "agent:agy",
    "astro-implementation": "agent:ned-code",
    "accessibility-verifier": "agent:ned-code",
    "seo-schema": "agent:kai-content",
    "deploy": "agent:ned-infra",
    "reviewer": "agent:ned-code",
}

LANE_PRIORITIES: Mapping[str, int] = {
    "content-strategist": 3,
    "design-theme": 3,
    "astro-implementation": 2,
    "accessibility-verifier": 2,
    "seo-schema": 3,
    "deploy": 2,
    "reviewer": 3,
}


class ThemeTaskGenerationError(ValueError):
    """Raised when a theme task cannot be generated safely."""


class ThemeTaskPayload(TypedDict):
    title: str
    description: str
    priority: int
    labels: tuple[str, ...]
    parentIdentifier: str | None
    dependencyIdentifiers: tuple[str, ...]
    dispatchHeld: bool


@dataclass(frozen=True)
class ThemeTaskSpec:
    """Input contract for a generated PWP theme Linear issue."""

    title: str
    lane: str
    phase: str
    parent_identifier: str | None = None
    dependency_identifiers: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    priority: int | None = None
    build_initiated: bool = False
    dispatch_ready: bool = True

    def __post_init__(self) -> None:
        if self.lane not in LANE_AGENT_LABELS:
            known = ", ".join(sorted(LANE_AGENT_LABELS))
            raise ThemeTaskGenerationError(
                f"unknown PWP theme lane {self.lane!r}; known lanes: {known}"
            )
        agent_labels = [label for label in self.labels if label.startswith("agent:")]
        if agent_labels:
            raise ThemeTaskGenerationError(
                "extra labels must not include agent:*; lane owns the generated agent label"
            )
        if not self.title.strip():
            raise ThemeTaskGenerationError("generated theme task requires a title")
        if not self.phase.strip():
            raise ThemeTaskGenerationError("generated theme task requires a phase")
        if not self.verification_commands:
            raise ThemeTaskGenerationError(
                "generated theme task requires at least one verification command"
            )


def _dedupe_preserve_order(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return tuple(out)


def labels_for_theme_task(spec: ThemeTaskSpec) -> tuple[str, ...]:
    """Return safe Linear labels for a generated PWP theme task.

    ``dispatch:ready`` is intentionally gated by ``build_initiated``.  The
    generator may pre-create issues with parents, dependencies, priorities, and
    owner labels, but it must not make those issues visible to dispatch scans
    until Michael or the orchestrator explicitly starts the build window.
    """

    labels: list[str] = [
        LANE_AGENT_LABELS[spec.lane],
        *BASE_LABELS,
        f"pwp-theme-phase:{spec.phase}",
    ]
    labels.extend(spec.labels)
    if spec.build_initiated and spec.dispatch_ready:
        labels.append(DISPATCH_READY_LABEL)
    return _dedupe_preserve_order(labels)


def linear_issue_input(spec: ThemeTaskSpec) -> ThemeTaskPayload:
    """Build a Linear-compatible issue input payload, excluding team id.

    Callers that resolve Linear label/parent/dependency UUIDs can use this
    deterministic payload as their source of truth.  Identifier strings are kept
    in the payload so a GraphQL adapter can resolve them in one API-specific
    layer without duplicating lane policy.
    """

    labels = labels_for_theme_task(spec)
    return {
        "title": spec.title.strip(),
        "description": render_theme_task_description(spec),
        "priority": spec.priority
        if spec.priority is not None
        else LANE_PRIORITIES[spec.lane],
        "labels": labels,
        "parentIdentifier": spec.parent_identifier,
        "dependencyIdentifiers": spec.dependency_identifiers,
        "dispatchHeld": DISPATCH_READY_LABEL not in labels,
    }


def render_theme_task_description(spec: ThemeTaskSpec) -> str:
    """Render a description that gives the receiving agent exact contracts."""

    sections: list[str] = [
        f"Generated from PWP theme autopilot phase `{spec.phase}`.",
        "",
        "## Lane",
        f"- Lane: `{spec.lane}`",
        f"- Agent label: `{LANE_AGENT_LABELS[spec.lane]}`",
        "",
        "## Dispatch policy",
        "- Do not add `dispatch:ready` until Michael initiates the build process.",
    ]
    if spec.parent_identifier:
        sections.extend(["", "## Parent", f"- {spec.parent_identifier}"])
    if spec.dependency_identifiers:
        sections.extend(["", "## Dependencies"])
        sections.extend(f"- {identifier}" for identifier in spec.dependency_identifiers)
    if spec.contracts:
        sections.extend(["", "## Contracts"])
        sections.extend(f"- `{contract}`" for contract in spec.contracts)
    if spec.files:
        sections.extend(["", "## Expected files / lanes"])
        sections.extend(f"- `{path}`" for path in spec.files)
    sections.extend(["", "## Verification"])
    sections.extend(f"- `{command}`" for command in spec.verification_commands)
    return "\n".join(sections).strip() + "\n"


def generate_theme_task_inputs(
    specs: Sequence[ThemeTaskSpec],
) -> list[ThemeTaskPayload]:
    """Generate Linear issue payloads and reject ambiguous owner routing."""

    generated = [linear_issue_input(spec) for spec in specs]
    for payload in generated:
        agent_labels = [
            label for label in payload["labels"] if str(label).startswith("agent:")
        ]
        if len(agent_labels) != 1:
            raise ThemeTaskGenerationError(
                f"generated task {payload['title']!r} has ambiguous agent labels: {agent_labels}"
            )
    return generated
