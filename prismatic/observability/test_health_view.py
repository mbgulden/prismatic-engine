from __future__ import annotations

from datetime import datetime, timezone

from prismatic.observability.health_view import (
    FailureClass,
    HealthStatus,
    LaneHealth,
    SubsystemHealth,
    build_health_view,
    evaluate_subsystem,
    render_markdown,
)

NOW = datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc)


def test_build_health_view_lists_actionable_failures_before_operator_noise():
    view = build_health_view(
        [
            SubsystemHealth(
                name="gateway",
                status=HealthStatus.HEALTHY,
                summary="serving",
            ),
            SubsystemHealth(
                name="curator",
                status=HealthStatus.WARNING,
                summary="label debt accumulating",
                failure_class=FailureClass.LABEL_DEBT,
                evidence=["agent:ned queue=15"],
                action="Patch dispatcher label filter.",
            ),
        ],
        [LaneHealth("ned", active=1, queued=15, blocked=0)],
        generated_at=NOW,
    )

    assert view["overall_status"] == "warning"
    assert view["what_is_broken"] == [
        {
            "subsystem": "curator",
            "status": "warning",
            "failure_class": "label_debt",
            "summary": "label debt accumulating",
            "action": "Patch dispatcher label filter.",
            "evidence": ["agent:ned queue=15"],
        }
    ]
    assert view["lanes"][0]["queued"] == 15
    assert "silent_stall" in view["failure_taxonomy"]


def test_evaluate_subsystem_distinguishes_normal_idle_from_silent_stall():
    stale_idle = evaluate_subsystem(
        "merge",
        last_seen_at="2026-07-06T19:40:00Z",
        now=NOW,
        expected_interval_seconds=300,
        required=False,
        active=0,
        queued=0,
    )
    stale_work = evaluate_subsystem(
        "supervisor",
        last_seen_at="2026-07-06T19:40:00Z",
        now=NOW,
        expected_interval_seconds=300,
        required=False,
        active=0,
        queued=3,
    )

    assert stale_idle.status == HealthStatus.IDLE
    assert stale_idle.failure_class is None
    assert stale_work.status == HealthStatus.WARNING
    assert stale_work.failure_class == FailureClass.SILENT_STALL
    assert "not normal idle" in stale_work.action


def test_required_subsystem_without_heartbeat_is_down_and_actionable():
    row = evaluate_subsystem(
        "gateway",
        last_seen_at=None,
        now=NOW,
        failure_class=FailureClass.INGEST,
        action="Restart gateway and check ingress logs.",
    )

    assert row.status == HealthStatus.DOWN
    assert row.failure_class == FailureClass.INGEST
    assert row.actionable is True
    assert row.evidence == ["last_seen_at missing"]


def test_lane_health_marks_blocked_lanes_without_treating_empty_lanes_as_broken():
    idle = LaneHealth("kai-content")
    blocked = LaneHealth("ned", active=1, queued=2, blocked=1)

    assert idle.status == HealthStatus.IDLE
    assert blocked.status == HealthStatus.WARNING


def test_render_markdown_keeps_one_minute_operator_shape():
    view = build_health_view(
        [
            SubsystemHealth(
                name="event-consumer",
                status=HealthStatus.WARNING,
                summary="silent stall: queue present, no heartbeat",
                failure_class=FailureClass.SILENT_STALL,
                action="Check worker loop.",
            )
        ],
        [LaneHealth("agy", active=0, queued=4, blocked=0)],
        generated_at=NOW,
    )

    markdown = render_markdown(view)

    assert markdown.startswith("# Prismatic Health View")
    assert "## What is broken?" in markdown
    assert "event-consumer" in markdown
    assert "silent_stall" in markdown
    assert "| agy | healthy | 0 | 4 | 0 |" in markdown
