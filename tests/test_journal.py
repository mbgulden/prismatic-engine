from __future__ import annotations

import json
from pathlib import Path

from prismatic.journal import (
    JournalConfig,
    build_inventory,
    cron_inventory,
    extract_golden_thread_summary,
    run_snapshot,
    validate_agent_output,
)


def make_config(tmp_path: Path) -> JournalConfig:
    workspace = tmp_path / "work"
    profile = tmp_path / ".harness" / "profiles" / "orchestrator"
    research = workspace / "Hermes-Research"
    return JournalConfig(
        workspace=workspace,
        harness_profile=profile,
        research_repo=research,
        journal_root=research / "journals",
        report_root=research / "reports" / "journal-continuity-audit",
        doc_root=research / "docs" / "journal-continuity-audit",
        sessions_dir=profile / "sessions",
        cron_jobs=profile / "cron" / "jobs.json",
        project_registry=workspace / "project-registry.json",
        team_id="team",
        project_id="project",
        state_todo="todo",
        state_in_progress="started",
        labels={},
    )


def test_build_inventory_supports_list_shaped_cron_jobs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (config.journal_root / "inbox").mkdir(parents=True)
    (config.journal_root / "inbox" / "2026-06-18.md").write_text("# Inbox\n")
    config.sessions_dir.mkdir(parents=True)
    (config.sessions_dir / "session.json").write_text("{}")
    config.cron_jobs.parent.mkdir(parents=True)
    config.cron_jobs.write_text(json.dumps([
        {"id": "j1", "name": "Monthly Journal Continuity Audit", "enabled": True, "schedule": "0 8 1 * *", "script": "monthly_journal_continuity_audit.py"},
        {"id": "x", "name": "Unrelated", "enabled": True},
    ]))

    json_path, md_path = build_inventory("test", config)
    data = json.loads(json_path.read_text())

    assert json_path.exists()
    assert md_path.exists()
    assert data["inbox_journals"]["count"] == 1
    assert data["sessions"]["count"] == 1
    assert data["cron"]["count"] == 1
    assert "Monthly Journal Continuity Audit" in md_path.read_text()


def test_cron_inventory_supports_dict_shaped_jobs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.cron_jobs.parent.mkdir(parents=True)
    config.cron_jobs.write_text(json.dumps({"jobs": [{"id": "j1", "name": "AGY watchdog", "paused": False}]}))

    data = cron_inventory(config)

    assert data["count"] == 1
    assert data["jobs"][0]["enabled"] is True


def test_snapshot_writes_inbox_and_event_index(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.sessions_dir.mkdir(parents=True)
    (config.sessions_dir / "session.json").write_text(json.dumps({
        "messages": [
            {"role": "assistant", "content": "I fixed the journal setup and wrote `/tmp/example.md` after a timeout error."}
        ]
    }))
    config.project_registry.parent.mkdir(parents=True)
    config.project_registry.write_text(json.dumps({"ventures": {}}))

    result = run_snapshot(config, force=True)

    assert result["changed"] is True
    assert result["signals"] >= 1
    assert Path(result["today_file"]).exists()
    assert (config.journal_root / ".index" / "events.json").exists()


def test_second_witness_requires_log_and_artifacts(tmp_path: Path) -> None:
    log = tmp_path / "agy.log"
    artifact = tmp_path / "report.md"
    log.write_text("completed cleanly\n" * 20)
    artifact.write_text("report")

    passed = validate_agent_output(log_path=str(log), artifact=[str(artifact)])
    missing = validate_agent_output(log_path=str(log), artifact=[str(tmp_path / "missing.md")])

    assert passed["passed"] is True
    assert missing["passed"] is False
    assert str(tmp_path / "missing.md") in missing["artifacts"]["missing"]


# ── Golden Thread summary (GRO-XXXX: schema-drift regression) ──────────
def test_golden_thread_summary_handles_current_schema(tmp_path: Path) -> None:
    """_last_sync is now a timestamp string; the dict moved to _last_sync_previous."""
    config = make_config(tmp_path)
    config.project_registry.parent.mkdir(parents=True)
    config.project_registry.write_text(json.dumps({
        "_last_sync": "2026-06-24T17:11:30.862085+00:00",  # timestamp string
        "_last_sync_previous": {
            "timestamp": "2026-06-24T15:19:37.273584+00:00",
            "linear_total_active": 600,
            "linear_in_progress": 218,
            "linear_unstarted": 30,
            "stale_gt7d": 292,
            "agent_done_stuck": 0,
            "cron_errors": 17,
            "cron_silent_fails": 14,
        },
        "ventures": {},
        "standalone_projects": {},
    }))

    out = extract_golden_thread_summary(config)

    # Must NOT crash, must show current numbers, must not show linear_todo:0
    assert "218 In Progress" in out
    assert "30 Todo" in out
    assert "600 active" in out
    assert "292 stale" in out
    assert "17 errors" in out
    assert "0 open PRs" in out  # github fields absent → default 0


def test_golden_thread_summary_handles_legacy_schema(tmp_path: Path) -> None:
    """_last_sync as a dict (pre-drift registry) still works."""
    config = make_config(tmp_path)
    config.project_registry.parent.mkdir(parents=True)
    config.project_registry.write_text(json.dumps({
        "_last_sync": {
            "linear_in_progress": 5,
            "linear_in_review": 2,
            "linear_todo": 3,
            "github_prs_open": 10,
            "github_issues_open": 4,
        },
        "ventures": {},
        "standalone_projects": {},
    }))

    out = extract_golden_thread_summary(config)

    assert "5 In Progress" in out
    assert "2 In Review" in out
    assert "3 Todo" in out
    assert "10 open PRs" in out
    assert "4 issues" in out


def test_golden_thread_summary_handles_missing_registry(tmp_path: Path) -> None:
    """When project-registry.json doesn't exist, return a graceful warning."""
    config = make_config(tmp_path)
    # Don't create the registry file
    out = extract_golden_thread_summary(config)
    assert "not found" in out


def test_golden_thread_summary_handles_empty_registry(tmp_path: Path) -> None:
    """When registry has no sync metadata, return just the section header."""
    config = make_config(tmp_path)
    config.project_registry.parent.mkdir(parents=True)
    config.project_registry.write_text(json.dumps({
        "_last_sync": "2026-06-24T17:11:30+00:00",
        # no _last_sync_previous
        "ventures": {},
        "standalone_projects": {},
    }))
    out = extract_golden_thread_summary(config)
    # Should not crash; section header should appear
    assert "Golden Thread" in out
    # No Linear line when no sync data
    assert "In Progress" not in out
