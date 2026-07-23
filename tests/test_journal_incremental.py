"""Incremental cursor, dedupe, rotation, and quarantine coverage for journal snapshots."""

from __future__ import annotations

from pathlib import Path

from prismatic.journal import (
    JournalConfig,
    advance_source_cursor,
    dedupe_signals,
    quarantine_operational_lines,
    run_snapshot,
)


OBSERVED_AT = "2026-07-23T06:20:00Z"


def make_config(tmp_path: Path) -> JournalConfig:
    workspace = tmp_path / "work"
    profile = tmp_path / ".harness" / "profiles" / "orchestrator"
    research = workspace / "Hermes-Research"
    return JournalConfig(
        workspace=workspace,
        harness_profile=profile,
        research_repo=research,
        journal_root=research / "journals",
        report_root=research / "reports",
        doc_root=research / "docs",
        sessions_dir=profile / "sessions",
        cron_jobs=profile / "cron" / "jobs.json",
        project_registry=workspace / "project-registry.json",
        team_id="team",
        project_id="project",
        state_todo="todo",
        state_in_progress="in_progress",
        labels={},
    )


def test_cursor_reads_only_appended_bytes_and_detects_rotation(tmp_path: Path) -> None:
    source = tmp_path / "collector.log"
    source.write_text("first\n")

    first, payload = advance_source_cursor(source, None, OBSERVED_AT)
    assert payload == b"first\n"
    assert first["byte_offset"] == len(payload)
    assert first["record_position"] == 1
    assert first["rotated"] is False

    second, payload = advance_source_cursor(source, first, OBSERVED_AT)
    assert payload == b""
    assert second["byte_offset"] == first["byte_offset"]

    source.write_text("first\nsecond\n")
    appended, payload = advance_source_cursor(source, second, OBSERVED_AT)
    assert payload == b"second\n"
    assert appended["collection_window"]["start_offset"] == len(b"first\n")
    assert appended["record_position"] == 2
    assert appended["rotated"] is False

    source.write_text("rotated\n")
    rotated, payload = advance_source_cursor(source, appended, OBSERVED_AT)
    assert payload == b"rotated\n"
    assert rotated["record_position"] == 1
    assert rotated["rotated"] is True
    assert rotated["collection_window"]["start_offset"] == 0


def test_dedupe_uses_stable_idempotency_key() -> None:
    signal = {
        "type": "log_error",
        "source": "gateway.log",
        "count": 1,
        "latest": ["error"],
    }
    accepted, deduped = dedupe_signals([signal], [])
    assert len(accepted) == 1
    assert len(accepted[0]["idempotency_key"]) == 64

    rerun, rerun_deduped = dedupe_signals([signal], accepted)
    assert rerun == []
    assert rerun_deduped == 1
    assert deduped == 0


def test_untimestamped_operational_line_is_quarantined_not_event(
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.log"
    records = quarantine_operational_lines(
        source,
        b"ERROR missing timestamp\n2026-07-23 06:20:00 ERROR current\n",
        OBSERVED_AT,
    )

    assert len(records) == 1
    assert records[0]["reason"] == "missing_or_malformed_timestamp"
    assert records[0]["excerpt_redacted"] == "ERROR missing timestamp"
    assert len(records[0]["idempotency_key"]) == 64


def test_no_new_input_rerun_accepts_zero_events(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.sessions_dir.mkdir(parents=True)
    config.sessions_dir.joinpath("session.json").write_text(
        '{"messages":[{"role":"assistant","content":"I fixed the collector after a timeout error."}]}'
    )
    config.project_registry.parent.mkdir(parents=True)
    config.project_registry.write_text('{"ventures":{}}')

    first = run_snapshot(config, force=True)
    second = run_snapshot(config, force=True)

    assert first["signals"] >= 1
    assert second["signals"] == 0
    assert second["changed"] is False
    events = list((config.journal_root / ".index").glob("events-*.json"))
    assert len(events) == 1
