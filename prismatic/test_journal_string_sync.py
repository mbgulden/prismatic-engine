from __future__ import annotations

import json
from pathlib import Path

from prismatic.journal import JournalConfig, extract_golden_thread_summary


def test_extract_golden_thread_summary_tolerates_string_last_sync(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    registry = workspace / "project-registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"_last_sync": "2026-07-03T19:32:55Z", "ventures": {}})
    )

    config = JournalConfig(
        workspace=workspace,
        harness_profile=tmp_path / ".harness" / "profiles" / "orchestrator",
        research_repo=workspace / "Hermes-Research",
        journal_root=workspace / "Hermes-Research" / "journals",
        report_root=workspace
        / "Hermes-Research"
        / "reports"
        / "journal-continuity-audit",
        doc_root=workspace / "Hermes-Research" / "docs" / "journal-continuity-audit",
        sessions_dir=tmp_path / ".harness" / "profiles" / "orchestrator" / "sessions",
        cron_jobs=tmp_path
        / ".harness"
        / "profiles"
        / "orchestrator"
        / "cron"
        / "jobs.json",
        project_registry=registry,
        team_id="team",
        project_id="project",
        state_todo="todo",
        state_in_progress="started",
        labels={},
    )

    summary = extract_golden_thread_summary(config)

    assert "Last sync: 2026-07-03T19:32:55Z" in summary
    assert "AttributeError" not in summary
