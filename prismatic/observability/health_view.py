"""Operational health view and failure taxonomy for Prismatic Engine.

The health view is intentionally small and dependency-free: callers can feed it
snapshots from the gateway, event consumer, curator, merge pipeline,
supervisor, and lane router without coupling observability to any one harness.
It answers the operator question "what is broken?" as a single structured
object and an optional Markdown report.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable


class HealthStatus(StrEnum):
    """Operator-facing subsystem health states."""

    HEALTHY = "healthy"
    IDLE = "idle"
    WARNING = "warning"
    DOWN = "down"


class FailureClass(StrEnum):
    """Actionable failure taxonomy shared by health producers."""

    INGEST = "ingest"
    ROUTING = "routing"
    EXECUTION = "execution"
    ARTIFACT = "artifact"
    STATE_SYNC = "state_sync"
    LABEL_DEBT = "label_debt"
    SILENT_STALL = "silent_stall"


_STATUS_RANK = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.IDLE: 0,
    HealthStatus.WARNING: 1,
    HealthStatus.DOWN: 2,
}


@dataclass(frozen=True)
class SubsystemHealth:
    """Status row for one critical service or pipeline subsystem."""

    name: str
    status: HealthStatus
    summary: str
    failure_class: FailureClass | None = None
    last_seen_at: str | None = None
    evidence: list[str] = field(default_factory=list)
    action: str = ""

    @property
    def actionable(self) -> bool:
        """Return True when this row represents operator work."""

        return self.status in {HealthStatus.WARNING, HealthStatus.DOWN}


@dataclass(frozen=True)
class LaneHealth:
    """Per-lane queue/runtime snapshot."""

    lane: str
    active: int = 0
    queued: int = 0
    blocked: int = 0
    oldest_queued_minutes: int | None = None

    @property
    def status(self) -> HealthStatus:
        """Classify lane pressure without hiding normal idle lanes."""

        if self.blocked > 0:
            return HealthStatus.WARNING
        if self.active == 0 and self.queued == 0:
            return HealthStatus.IDLE
        return HealthStatus.HEALTHY


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse ISO-8601 timestamps, accepting a trailing ``Z``."""

    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_subsystem(
    name: str,
    *,
    last_seen_at: str | None,
    now: datetime | None = None,
    expected_interval_seconds: int = 300,
    active: int = 0,
    queued: int = 0,
    error_count: int = 0,
    required: bool = True,
    failure_class: FailureClass = FailureClass.EXECUTION,
    action: str = "Investigate subsystem logs and restart the owning service if needed.",
) -> SubsystemHealth:
    """Convert a heartbeat/work snapshot into one health row.

    Silent stalls are classified only when work exists (``active`` or ``queued``)
    and the heartbeat is stale. Empty queues with no recent heartbeat are normal
    idle for optional producers, but down for required services.
    """

    now = now or datetime.now(timezone.utc)
    last_seen = parse_timestamp(last_seen_at)
    workload = active + queued

    if last_seen is None:
        if required:
            return SubsystemHealth(
                name=name,
                status=HealthStatus.DOWN,
                summary="no heartbeat recorded",
                failure_class=failure_class,
                last_seen_at=None,
                evidence=["last_seen_at missing"],
                action=action,
            )
        return SubsystemHealth(
            name=name,
            status=HealthStatus.IDLE,
            summary="idle; no heartbeat expected yet",
            last_seen_at=None,
            evidence=["optional subsystem", "no queued or active work"],
        )

    age_seconds = max(0.0, (now - last_seen).total_seconds())
    stale_after = expected_interval_seconds * 2

    if error_count > 0:
        return SubsystemHealth(
            name=name,
            status=HealthStatus.WARNING,
            summary=f"{error_count} recent error(s)",
            failure_class=failure_class,
            last_seen_at=last_seen.isoformat(),
            evidence=[f"errors={error_count}", f"heartbeat_age={int(age_seconds)}s"],
            action=action,
        )

    if age_seconds > stale_after:
        if workload > 0:
            return SubsystemHealth(
                name=name,
                status=HealthStatus.WARNING,
                summary="silent stall: work is present but heartbeat is stale",
                failure_class=FailureClass.SILENT_STALL,
                last_seen_at=last_seen.isoformat(),
                evidence=[
                    f"heartbeat_age={int(age_seconds)}s",
                    f"stale_after={stale_after}s",
                    f"active={active}",
                    f"queued={queued}",
                ],
                action="Check the worker loop before relaunching; this is not normal idle.",
            )
        if required:
            return SubsystemHealth(
                name=name,
                status=HealthStatus.DOWN,
                summary="required service heartbeat is stale",
                failure_class=failure_class,
                last_seen_at=last_seen.isoformat(),
                evidence=[
                    f"heartbeat_age={int(age_seconds)}s",
                    f"stale_after={stale_after}s",
                ],
                action=action,
            )
        return SubsystemHealth(
            name=name,
            status=HealthStatus.IDLE,
            summary="idle; no queued or active work",
            last_seen_at=last_seen.isoformat(),
            evidence=[f"heartbeat_age={int(age_seconds)}s", "workload=0"],
        )

    return SubsystemHealth(
        name=name,
        status=HealthStatus.HEALTHY,
        summary="heartbeat fresh",
        last_seen_at=last_seen.isoformat(),
        evidence=[f"heartbeat_age={int(age_seconds)}s"],
    )


def build_health_view(
    subsystems: Iterable[SubsystemHealth],
    lanes: Iterable[LaneHealth] = (),
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical single operational health view."""

    generated_at = generated_at or datetime.now(timezone.utc)
    subsystem_rows = list(subsystems)
    lane_rows = list(lanes)
    actionable = [row for row in subsystem_rows if row.actionable]
    lane_warnings = [lane for lane in lane_rows if lane.status == HealthStatus.WARNING]

    worst_rank = 0
    if subsystem_rows:
        worst_rank = max(_STATUS_RANK[row.status] for row in subsystem_rows)
    if lane_warnings:
        worst_rank = max(worst_rank, _STATUS_RANK[HealthStatus.WARNING])

    overall_status = {
        0: HealthStatus.HEALTHY,
        1: HealthStatus.WARNING,
        2: HealthStatus.DOWN,
    }[worst_rank]

    return {
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "overall_status": overall_status.value,
        "what_is_broken": [
            {
                "subsystem": row.name,
                "status": row.status.value,
                "failure_class": row.failure_class.value if row.failure_class else None,
                "summary": row.summary,
                "action": row.action,
                "evidence": row.evidence,
            }
            for row in actionable
        ],
        "subsystems": [serialize_subsystem(row) for row in subsystem_rows],
        "lanes": [serialize_lane(row) for row in lane_rows],
        "failure_taxonomy": [failure.value for failure in FailureClass],
    }


def serialize_subsystem(row: SubsystemHealth) -> dict[str, Any]:
    """Serialize a subsystem row with enum values converted to strings."""

    data = asdict(row)
    data["status"] = row.status.value
    data["failure_class"] = row.failure_class.value if row.failure_class else None
    data["actionable"] = row.actionable
    return data


def serialize_lane(row: LaneHealth) -> dict[str, Any]:
    """Serialize a lane row including its derived status."""

    data = asdict(row)
    data["status"] = row.status.value
    return data


def render_markdown(view: dict[str, Any]) -> str:
    """Render a compact operator report from ``build_health_view`` output."""

    overall_status = str(view.get("overall_status", "unknown"))
    status_icon = {
        "healthy": "🟢",
        "idle": "🟢",
        "warning": "🟡",
        "down": "🔴",
    }.get(overall_status, "🟡")
    lines = [
        f"# Prismatic Health View — {status_icon} {overall_status}",
        "",
        f"Generated: `{view.get('generated_at', '')}`",
        "",
        "## What is broken?",
    ]

    broken = view.get("what_is_broken", [])
    if not broken:
        lines.append("Nothing actionable. Idle lanes are shown separately from stalls.")
    else:
        for item in broken:
            failure = item.get("failure_class") or "unclassified"
            lines.append(
                f"- **{item.get('subsystem')}** — `{item.get('status')}` / "
                f"`{failure}`: {item.get('summary')}"
            )
            if item.get("action"):
                lines.append(f"  - Action: {item['action']}")

    lines.extend(
        ["", "## Subsystems", "| Subsystem | Status | Failure class | Summary |"]
    )
    lines.append("|---|---:|---|---|")
    for row in view.get("subsystems", []):
        lines.append(
            f"| {row['name']} | {row['status']} | {row.get('failure_class') or ''} | "
            f"{row['summary']} |"
        )

    lines.extend(["", "## Lanes", "| Lane | Status | Active | Queued | Blocked |"])
    lines.append("|---|---:|---:|---:|---:|")
    for row in view.get("lanes", []):
        lines.append(
            f"| {row['lane']} | {row['status']} | {row['active']} | "
            f"{row['queued']} | {row['blocked']} |"
        )

    lines.extend(["", "## Failure taxonomy"])
    for failure in view.get("failure_taxonomy", []):
        lines.append(f"- `{failure}`")

    return "\n".join(lines) + "\n"
