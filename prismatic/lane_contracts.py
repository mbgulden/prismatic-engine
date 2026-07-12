"""Dispatch lane contracts for Prismatic Engine agents.

The dispatcher used to treat ``agent:*`` labels as sufficient routing facts.
That creates label-only dead zones: an issue can sit on an agent label even when
its state, labels, or review/execution semantics are wrong for that lane.

This module makes the dispatch contract explicit and testable.  It is deliberately
small and dependency-free so both the live dispatcher and cron scanners can import
it without pulling in Hermes-specific runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, overload

QueueKind = Literal["execution", "review", "coordination"]
FallbackAction = Literal["hold", "route_to_fred", "starvation_signal"]


@dataclass(frozen=True)
class LaneContract:
    """Rules that decide whether an issue is dispatchable on one lane."""

    agent: str
    required_labels: frozenset[str]
    allowed_states: frozenset[str]
    queue_kind: QueueKind
    description: str
    fallback_action: FallbackAction
    starvation_signal: str
    review_only: bool = False
    blocked_labels: frozenset[str] = field(default_factory=frozenset)

    @property
    def label(self) -> str:
        """Canonical Linear label for this lane."""

        return f"agent:{self.agent}"


ACTIONABLE_STATES = frozenset({"Backlog", "Todo", "In Progress"})
REVIEW_STATES = frozenset({"In Review", "Review", "QA", "Backlog", "Todo"})
REVIEW_ONLY_LABELS = frozenset({"review:only", "type:review", "validation:only"})

LANE_CONTRACTS: dict[str, LaneContract] = {
    "fred": LaneContract(
        agent="fred",
        required_labels=frozenset({"agent:fred"}),
        allowed_states=ACTIONABLE_STATES,
        queue_kind="coordination",
        description=(
            "Orchestrator lane. Intake, decomposition, task routing, and "
            "cross-agent follow-up. Fred is the fallback for ambiguous work."
        ),
        fallback_action="hold",
        starvation_signal="fred_queue_empty",
    ),
    "kai": LaneContract(
        agent="kai",
        required_labels=frozenset({"agent:kai"}),
        allowed_states=ACTIONABLE_STATES,
        queue_kind="execution",
        description=(
            "Product/content execution lane for Kai-owned deliverables after "
            "the issue has explicit Kai ownership."
        ),
        fallback_action="route_to_fred",
        starvation_signal="kai_queue_empty",
        blocked_labels=REVIEW_ONLY_LABELS,
    ),
    "ned": LaneContract(
        agent="ned",
        required_labels=frozenset({"agent:ned"}),
        allowed_states=ACTIONABLE_STATES,
        queue_kind="execution",
        description=(
            "Infrastructure and code-execution lane: scripts/, prismatic/, "
            "plugins/, deployment plumbing, health checks, and ops automation."
        ),
        fallback_action="route_to_fred",
        starvation_signal="ned_queue_empty",
        blocked_labels=REVIEW_ONLY_LABELS,
    ),
    "agy": LaneContract(
        agent="agy",
        required_labels=frozenset({"agent:agy"}),
        allowed_states=ACTIONABLE_STATES,
        queue_kind="execution",
        description=(
            "Antigravity implementation/research lane. Launch only when the "
            "issue is explicitly assigned to AGY, including model-tier labels."
        ),
        fallback_action="route_to_fred",
        starvation_signal="agy_queue_empty",
        blocked_labels=REVIEW_ONLY_LABELS,
    ),
    "jules": LaneContract(
        agent="jules",
        required_labels=frozenset({"agent:jules"}),
        allowed_states=REVIEW_STATES,
        queue_kind="review",
        description=(
            "Validator lane. Review-only and QA work lands here; it must not be "
            "counted as execution queue work for AGY, Kai, or Ned."
        ),
        fallback_action="starvation_signal",
        starvation_signal="jules_review_queue_empty",
        review_only=True,
    ),
}


def get_lane_contract(agent: str) -> LaneContract:
    """Return the dispatch contract for *agent* or raise ``KeyError``."""

    return LANE_CONTRACTS[agent]


def issue_label_names(issue: dict[str, Any]) -> set[str]:
    """Extract a normalized label-name set from a Linear issue dict."""

    labels = issue.get("labels", [])
    if isinstance(labels, dict):
        labels = labels.get("nodes", [])

    names: set[str] = set()
    for label in labels or []:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and label.get("name"):
            names.add(str(label["name"]))
    return names


def issue_state_name(issue: dict[str, Any]) -> str:
    """Extract the Linear workflow-state name from a normalized issue dict."""

    state = issue.get("state") or {}
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        return str(state.get("name") or "")
    return ""


@overload
def qualifies_for_dispatch(
    issue: dict[str, Any],
    agent: str,
    *,
    include_reason: Literal[False] = False,
) -> bool: ...


@overload
def qualifies_for_dispatch(
    issue: dict[str, Any],
    agent: str,
    *,
    include_reason: Literal[True],
) -> tuple[bool, str]: ...


def qualifies_for_dispatch(
    issue: dict[str, Any],
    agent: str,
    *,
    include_reason: bool = False,
) -> bool | tuple[bool, str]:
    """Decide whether *issue* is dispatchable for *agent*.

    A true result requires all of the lane's required labels, an allowed state,
    and no blocked review-only labels for execution lanes.  The optional reason is
    stable enough for logs/tests but intentionally human-readable.
    """

    contract = get_lane_contract(agent)
    labels = issue_label_names(issue)
    state = issue_state_name(issue)

    missing = contract.required_labels - labels
    if missing:
        result = (False, f"missing_required_labels:{','.join(sorted(missing))}")
    elif state not in contract.allowed_states:
        result = (False, f"state_not_allowed:{state or '<unset>'}")
    elif contract.queue_kind == "execution" and labels & contract.blocked_labels:
        blocked = ",".join(sorted(labels & contract.blocked_labels))
        result = (False, f"review_only_not_execution:{blocked}")
    else:
        result = (True, "dispatchable")

    return result if include_reason else result[0]


def filter_dispatchable_issues(
    issues: list[dict[str, Any]],
    agent: str,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """Split candidate issues into dispatchable and held-with-reason lists."""

    dispatchable: list[dict[str, Any]] = []
    held: list[tuple[dict[str, Any], str]] = []
    for issue in issues:
        ok, reason = qualifies_for_dispatch(issue, agent, include_reason=True)
        if ok:
            dispatchable.append(issue)
        else:
            held.append((issue, reason))
    return dispatchable, held


def starvation_signal_for(agent: str) -> str:
    """Return the stable queue-empty signal for an agent lane."""

    return get_lane_contract(agent).starvation_signal
