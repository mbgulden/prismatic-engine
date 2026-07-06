"""Queue/runtime health scorecard for Prismatic lanes.

This module intentionally stays provider-light: the Linear fetcher is a thin
GraphQL adapter, while the scorecard builder and renderer are pure functions
covered by tests.  The output is designed for daily operator use, not forensic
reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Sequence

from .dispatcher import gql

DEFAULT_AGENT_LABELS: tuple[str, ...] = (
    "agent:ned",
    "agent:fred",
    "agent:kai",
    "agent:kai-content",
    "agent:agy",
    "agent:jules",
    "agent:codex",
)

ACTIVE_STATES = {"in progress", "in review", "started"}
QUEUED_STATES = {"backlog", "todo", "triage", "ready"}
COMPLETED_STATES = {"done", "completed", "cancelled", "canceled"}
BLOCKED_LABEL_MARKERS = ("blocked", "waiting", "needs:human", "human-decision")


@dataclass(frozen=True)
class QueueIssue:
    """Normalized issue data used by the scorecard."""

    identifier: str
    title: str
    state: str
    created_at: datetime
    labels: tuple[str, ...] = field(default_factory=tuple)

    @property
    def age_hours(self) -> float:
        return max(
            0.0,
            (datetime.now(timezone.utc) - self.created_at).total_seconds() / 3600,
        )


@dataclass(frozen=True)
class LaneScorecard:
    """Aggregated health for one lane."""

    lane: str
    active: int = 0
    queued: int = 0
    blocked: int = 0
    stale: int = 0
    completed: int = 0
    total_open: int = 0
    max_queue_age_hours: float = 0.0
    avg_queue_age_hours: float = 0.0

    @property
    def status(self) -> str:
        if self.blocked:
            return "🔴"
        if self.stale or self.queued >= 5:
            return "🟡"
        return "🟢"


def parse_linear_time(value: str | None) -> datetime:
    """Parse Linear's ISO-8601 timestamp into an aware UTC datetime."""

    if not value:
        return datetime.now(timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lane_from_labels(labels: Sequence[str], lane_labels: Sequence[str]) -> str | None:
    for lane in lane_labels:
        if lane in labels:
            return lane.removeprefix("agent:")
    return None


def _is_blocked(issue: QueueIssue) -> bool:
    state = issue.state.lower()
    if "blocked" in state or "waiting" in state:
        return True
    return any(
        marker in label.lower()
        for label in issue.labels
        for marker in BLOCKED_LABEL_MARKERS
    )


def build_scorecard(
    issues: Iterable[QueueIssue],
    *,
    lane_labels: Sequence[str] = DEFAULT_AGENT_LABELS,
    stale_after_hours: float = 72.0,
) -> list[LaneScorecard]:
    """Aggregate issues into per-lane runtime health rows.

    Args:
        issues: Normalized issue stream, usually from Linear.
        lane_labels: Agent labels to include and order in the report.
        stale_after_hours: Queue age threshold. Queued issues older than this
            count as stale.
    """

    buckets: dict[str, list[QueueIssue]] = {
        label.removeprefix("agent:"): [] for label in lane_labels
    }
    for issue in issues:
        lane = _lane_from_labels(issue.labels, lane_labels)
        if lane is not None:
            buckets.setdefault(lane, []).append(issue)

    rows: list[LaneScorecard] = []
    for lane in [label.removeprefix("agent:") for label in lane_labels]:
        lane_issues = buckets.get(lane, [])
        active = queued = blocked = stale = completed = 0
        queue_ages: list[float] = []

        for issue in lane_issues:
            state = issue.state.lower()
            if _is_blocked(issue):
                blocked += 1
            if state in COMPLETED_STATES:
                completed += 1
                continue
            if state in ACTIVE_STATES:
                active += 1
            else:
                # Unknown open states are still queue pressure.
                queued += 1
                age = issue.age_hours
                queue_ages.append(age)
                if age >= stale_after_hours:
                    stale += 1

        rows.append(
            LaneScorecard(
                lane=lane,
                active=active,
                queued=queued,
                blocked=blocked,
                stale=stale,
                completed=completed,
                total_open=active + queued + blocked,
                max_queue_age_hours=max(queue_ages, default=0.0),
                avg_queue_age_hours=mean(queue_ages) if queue_ages else 0.0,
            )
        )
    return rows


def format_age(hours: float) -> str:
    """Format queue age compactly for terminal tables."""

    if hours <= 0:
        return "-"
    if hours < 24:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def render_scorecard(rows: Sequence[LaneScorecard]) -> str:
    """Render a concise fixed-width scorecard table."""

    header = "Lane              S  active queued blocked stale done  max-age avg-age"
    rule = "-" * len(header)
    lines = ["Prismatic queue/runtime health", header, rule]
    for row in rows:
        lines.append(
            f"{row.lane:<17} {row.status:<2} "
            f"{row.active:>6} {row.queued:>6} {row.blocked:>7} "
            f"{row.stale:>5} {row.completed:>4} "
            f"{format_age(row.max_queue_age_hours):>7} "
            f"{format_age(row.avg_queue_age_hours):>7}"
        )
    return "\n".join(lines)


def _node_to_issue(node: dict[str, Any]) -> QueueIssue:
    labels = tuple(
        label.get("name", "")
        for label in node.get("labels", {}).get("nodes", [])
        if isinstance(label, dict) and label.get("name")
    )
    state = node.get("state") or {}
    return QueueIssue(
        identifier=node.get("identifier", ""),
        title=node.get("title", ""),
        state=state.get("name", ""),
        created_at=parse_linear_time(node.get("createdAt")),
        labels=labels,
    )


def fetch_linear_agent_issues(lane_labels: Sequence[str]) -> list[QueueIssue]:
    """Fetch current Linear issues for the requested agent labels."""

    query = """
    query QueueHealth($labels: [String!]) {
      issues(
        first: 250
        filter: {
          labels: { name: { in: $labels } }
        }
        orderBy: createdAt
      ) {
        nodes {
          identifier
          title
          createdAt
          state { name type }
          labels { nodes { name } }
        }
      }
    }
    """
    data = gql(query, {"labels": list(lane_labels)})
    nodes = data.get("issues", {}).get("nodes", [])
    return [_node_to_issue(node) for node in nodes]


def run_scorecard(
    *,
    lane_labels: Sequence[str] = DEFAULT_AGENT_LABELS,
    stale_after_hours: float = 72.0,
) -> str:
    """Fetch, aggregate, and render the queue/runtime health scorecard."""

    issues = fetch_linear_agent_issues(lane_labels)
    rows = build_scorecard(
        issues,
        lane_labels=lane_labels,
        stale_after_hours=stale_after_hours,
    )
    return render_scorecard(rows)


def cmd_queue_health(args: Any) -> None:
    """argparse handler for ``prismatic-engine queue-health``."""

    lane_labels = tuple(args.lane) if args.lane else DEFAULT_AGENT_LABELS
    print(run_scorecard(lane_labels=lane_labels, stale_after_hours=args.stale_hours))
