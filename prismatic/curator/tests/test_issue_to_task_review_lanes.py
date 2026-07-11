from __future__ import annotations

from datetime import datetime, timezone

from prismatic.curator import issue_to_task as mod


def _recent_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _issue(*, title: str = "Implement executable queue work", state: str = "Todo", labels: list[str] | None = None) -> dict:
    return {
        "identifier": "GRO-3488",
        "title": title,
        "description": "Acceptance criteria are concrete enough for routing and execution.",
        "priority": 2,
        "state": {"name": state},
        "createdAt": _recent_iso(),
        "labels": {"nodes": [{"name": label} for label in (labels or ["agent:ned", "dispatch:ready"])]},
    }


def test_assign_lane_blocks_peer_review_label_even_when_dispatch_ready():
    issue = _issue(title="Peer review completed implementation", labels=["agent:peer-review", "dispatch:ready"])

    lane, skip_reason = mod.assign_lane(issue, lane_mode="autonomous")

    assert lane == "review"
    assert skip_reason == "review-only-lane"
    assert mod.issue_to_task(issue, lane_mode="autonomous") is None


def test_assign_lane_blocks_in_review_state_from_execution_queue():
    issue = _issue(title="Fix review feedback", state="In Review", labels=["agent:ned", "dispatch:ready"])

    lane, skip_reason = mod.assign_lane(issue, lane_mode="autonomous")

    assert lane == "review"
    assert skip_reason == "review-only-lane"
    assert mod.issue_to_task(issue, lane_mode="autonomous") is None


def test_assign_lane_still_allows_runnable_dispatch_ready_work():
    issue = _issue(title="Implement runnable dispatcher fix", labels=["agent:ned", "dispatch:ready"])

    lane, skip_reason = mod.assign_lane(issue, lane_mode="autonomous")
    task = mod.issue_to_task(issue, lane_mode="autonomous")

    assert lane == "backlog"
    assert skip_reason is None
    assert task is not None
    assert task["issue_id"] == "GRO-3488"
