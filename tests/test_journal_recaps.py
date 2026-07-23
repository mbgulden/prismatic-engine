"""Evidence-cited deterministic recap coverage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prismatic.journal import JournalConfig, generate_recap, recap_window


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

    assert result["events"] == 1
    assert result["cited_event_ids"] == ["a" * 64]
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
