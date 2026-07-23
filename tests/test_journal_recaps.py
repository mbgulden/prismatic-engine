"""Evidence-cited deterministic recap coverage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prismatic.journal import (
    JournalConfig,
    build_evidence_recap,
    generate_recap,
    recap_window,
)


def config_for(tmp_path: Path) -> JournalConfig:
    research = tmp_path / "Hermes-Research"
    profile = tmp_path / ".hermes" / "profiles" / "orchestrator"
    return JournalConfig(
        workspace=tmp_path,
        harness_profile=profile,
        research_repo=research,
        journal_root=research / "journals",
        report_root=research / "reports",
        doc_root=research / "docs",
        sessions_dir=profile / "sessions",
        cron_jobs=profile / "cron" / "jobs.json",
        project_registry=tmp_path / "project-registry.json",
        team_id="team",
        project_id="project",
        state_todo="todo",
        state_in_progress="progress",
        labels={},
    )


def test_daily_recap_cites_events_and_uses_current_cron_state(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)
    index.joinpath("events-2026-07-23.json").write_text(
        json.dumps(
            [
                {
                    "type": "cron_run",
                    "job_name": "snapshot",
                    "status": "error",
                    "snippet": "token: secret-value",
                    "idempotency_key": "a" * 64,
                    "_timestamp": "2026-07-23T05:00:00Z",
                }
            ]
        )
    )
    config.cron_jobs.parent.mkdir(parents=True)
    config.cron_jobs.write_text(
        json.dumps(
            {"jobs": [{"name": "snapshot", "enabled": True, "last_status": "ok"}]}
        )
    )

    result = generate_recap(
        "daily", config, datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    )
    rendered = Path(result["path"]).read_text()

    assert result["source_event_count"] == 1
    assert result["rendered_claim_count"] == 1
    assert result["artifact_bytes"] < 32_768
    assert "cited_event_ids" not in result
    manifest = json.loads(Path(result["citation_manifest_path"]).read_text())
    assert manifest["cited_event_ids"] == ["a" * 64]
    assert "[E:aaaaaaaaaaaa]" in rendered
    assert "current `ok`" in rendered
    assert "secret-value" not in rendered
    assert "[REDACTED]" in rendered


def test_quiet_daily_and_weekly_boundary_are_deterministic(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    now = datetime(2026, 7, 26, 23, 59, tzinfo=timezone.utc)  # Sunday
    weekly_start, weekly_end = recap_window("weekly", now)
    assert weekly_start == datetime(2026, 7, 20, tzinfo=timezone.utc)
    assert weekly_end == now

    result = generate_recap("daily", config, now)
    rendered = Path(result["path"]).read_text()
    assert result["quiet"] is True
    assert "Quiet window" in rendered
    assert "No current scheduler state available" in rendered


def test_recap_claims_are_bounded_but_each_displayed_event_is_cited() -> None:
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    events = [
        {
            "type": "decision",
            "snippet": f"event {index}",
            "idempotency_key": f"{index:064x}",
        }
        for index in range(55)
    ]
    rendered, citations = build_evidence_recap(
        events, "daily", now.replace(hour=0), now, [], max_events=3
    )

    assert len(citations) == 3
    assert "Showing the latest 3 cited events of 55 accepted events" in rendered
    assert "event 54" in rendered
    assert "event 0" not in rendered
    assert rendered.count("[E:") == 3


def test_generated_recap_uses_compact_result_and_bounded_artifact(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    index = config.journal_root / ".index"
    index.mkdir(parents=True)
    events = [
        {
            "type": "decision",
            "snippet": f"event {index}",
            "idempotency_key": f"{index:064x}",
            "_timestamp": f"2026-07-23T{index % 12:02d}:00:00Z",
        }
        for index in range(55)
    ]
    index.joinpath("events-2026-07-23.json").write_text(json.dumps(events))

    result = generate_recap(
        "daily", config, datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    )
    rendered = Path(result["path"]).read_text()
    assert result["source_event_count"] == 55
    assert result["rendered_claim_count"] == 50
    assert result["artifact_bytes"] == len(rendered.encode()) < 32_768
    assert "cited_event_ids" not in result
    assert rendered.count("[E:") == 50
