"""Prompt4 packet-gate evaluation using the latest valid packet per agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Mapping

SUCCESS_MARKERS: dict[str, tuple[str, ...]] = {
    "fred": (
        "FRED_PROMPT4_PACKET_SUPERSESSION_OK",
        "RAW_AGENT_OUTPUT_REPAIR_QUEUE_OK",
        "RAW_AGENT_OUTPUT_REPAIR_QUEUE_PR_READY_OK",
        "RAW_AGENT_OUTPUT_QUEUE_OK",
    ),
    "agy": ("AGY_PACKET_FIXTURES_REPAIR_HINTS_OK",),
    "george": (
        "GEORGE_RAW_OUTPUT_REPAIR_DASHBOARD_PROOF_OK",
        "GEORGE_ASSIGNED_DISPATCH_AD_HOC_VERIFIED_OK",
    ),
}
BLOCKED_MARKERS: dict[str, tuple[str, ...]] = {
    "fred": ("RAW_AGENT_OUTPUT_REPAIR_QUEUE_BLOCKED",),
    "agy": ("AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED",),
    "george": (
        "GEORGE_RAW_OUTPUT_REPAIR_DASHBOARD_PROOF_BLOCKED",
        "GEORGE_ASSIGNED_DISPATCH_BLOCKED",
    ),
}
DEFAULT_REQUIRED_AGENTS = ("fred", "agy")
_RESULT_RE = re.compile(r"^RESULT=(PASS|FAIL|BLOCKED)\s*$")
_MARKER_RE = re.compile(r"^MARKER=([A-Z0-9_]+)\s*$")


@dataclass(frozen=True)
class PacketObservation:
    agent: str
    issue: str
    created_at: str
    result: str
    marker: str
    state: str
    user: str = ""
    line_number: int = 0


def _marker_owner(marker: str) -> tuple[str, bool] | None:
    for agent, markers in SUCCESS_MARKERS.items():
        if marker in markers:
            return agent, True
    for agent, markers in BLOCKED_MARKERS.items():
        if marker in markers:
            return agent, False
    return None


def parse_packet_observations(
    issue: str, comments: Iterable[Mapping[str, object]]
) -> list[PacketObservation]:
    """Parse marker packets, pairing each marker with its nearest preceding RESULT."""
    observations: list[PacketObservation] = []
    for comment in comments:
        body = str(comment.get("body") or "")
        lines = body.splitlines()
        results: list[tuple[int, str]] = []
        for index, raw in enumerate(lines):
            line = raw.strip()
            result_match = _RESULT_RE.match(line)
            if result_match:
                results.append((index, result_match.group(1)))
                continue
            marker_match = _MARKER_RE.match(line)
            if not marker_match:
                continue
            marker = marker_match.group(1)
            owner = _marker_owner(marker)
            if not owner:
                continue
            preceding = [item for item in results if item[0] < index]
            if not preceding:
                continue
            _, result = preceding[-1]
            agent, success_marker = owner
            state = "pass" if success_marker and result == "PASS" else "blocked"
            user = comment.get("user") or {}
            user_name = str(user.get("name") or "") if isinstance(user, Mapping) else ""
            observations.append(
                PacketObservation(
                    agent=agent,
                    issue=issue,
                    created_at=str(comment.get("createdAt") or ""),
                    result=result,
                    marker=marker,
                    state=state,
                    user=user_name,
                    line_number=index + 1,
                )
            )
    return observations


def evaluate_prompt4_packet_gate(
    comments_by_issue: Mapping[str, Iterable[Mapping[str, object]]],
    *,
    required_agents: tuple[str, ...] = DEFAULT_REQUIRED_AGENTS,
) -> dict[str, Any]:
    """Evaluate only each agent's latest known packet; old blockers are superseded."""
    observations: list[PacketObservation] = []
    for issue, comments in comments_by_issue.items():
        observations.extend(parse_packet_observations(issue, comments))
    latest: dict[str, PacketObservation] = {}
    for observation in observations:
        previous = latest.get(observation.agent)
        key = (observation.created_at, observation.line_number)
        previous_key = (
            (previous.created_at, previous.line_number) if previous else ("", -1)
        )
        if not previous or key >= previous_key:
            latest[observation.agent] = observation
    found = {
        agent: latest.get(agent, None) is not None and latest[agent].state == "pass"
        for agent in SUCCESS_MARKERS
    }
    blocked = {
        agent: latest.get(agent, None) is not None and latest[agent].state == "blocked"
        for agent in SUCCESS_MARKERS
    }
    superseded_blocked = {
        agent: sum(
            1
            for observation in observations
            if observation.agent == agent
            and observation.state == "blocked"
            and latest.get(agent) != observation
        )
        for agent in SUCCESS_MARKERS
    }
    missing = [agent for agent in required_agents if not found.get(agent)]
    active_blockers = [agent for agent in required_agents if blocked.get(agent)]
    complete = not missing and not active_blockers
    return {
        "complete": complete,
        "required_agents": list(required_agents),
        "missing": missing,
        "active_blockers": active_blockers,
        "found": found,
        "blocked": blocked,
        "superseded_blocked": superseded_blocked,
        "latest": {agent: asdict(packet) for agent, packet in latest.items()},
    }
