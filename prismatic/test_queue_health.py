from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prismatic.queue_health import (
    QueueIssue,
    build_scorecard,
    format_age,
    parse_linear_time,
    render_scorecard,
)


def issue(
    identifier: str, state: str, hours_old: float, labels: tuple[str, ...]
) -> QueueIssue:
    return QueueIssue(
        identifier=identifier,
        title=f"Issue {identifier}",
        state=state,
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_old),
        labels=labels,
    )


def test_build_scorecard_counts_lane_status_and_queue_age() -> None:
    rows = build_scorecard(
        [
            issue("GRO-1", "In Progress", 1, ("agent:ned",)),
            issue("GRO-2", "Backlog", 100, ("agent:ned",)),
            issue("GRO-3", "Backlog", 2, ("agent:ned", "needs:human")),
            issue("GRO-4", "Done", 200, ("agent:ned",)),
            issue("GRO-5", "Todo", 12, ("agent:fred",)),
        ],
        lane_labels=("agent:ned", "agent:fred"),
        stale_after_hours=72,
    )

    ned, fred = rows
    assert ned.lane == "ned"
    assert ned.active == 1
    assert ned.queued == 2
    assert ned.blocked == 1
    assert ned.stale == 1
    assert ned.completed == 1
    assert ned.status == "🔴"
    assert 99 <= ned.max_queue_age_hours <= 101

    assert fred.lane == "fred"
    assert fred.queued == 1
    assert fred.status == "🟢"


def test_render_scorecard_is_concise_table() -> None:
    rows = build_scorecard(
        [issue("GRO-1", "Backlog", 96, ("agent:ned",))],
        lane_labels=("agent:ned",),
        stale_after_hours=72,
    )

    output = render_scorecard(rows)

    assert "Prismatic queue/runtime health" in output
    assert "Lane" in output
    assert "ned" in output
    assert "4.0d" in output
    assert "🟡" in output
    assert len(output.splitlines()) == 4


def test_parse_linear_time_accepts_z_suffix() -> None:
    parsed = parse_linear_time("2026-07-06T14:15:38.279Z")

    assert parsed.tzinfo is not None
    assert parsed.year == 2026
    assert parsed.hour == 14


def test_format_age_compacts_hours_and_days() -> None:
    assert format_age(0) == "-"
    assert format_age(5.4) == "5h"
    assert format_age(36) == "1.5d"
