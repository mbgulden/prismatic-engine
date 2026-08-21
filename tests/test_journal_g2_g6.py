from __future__ import annotations

import datetime as dt
import json

from prismatic.journal import (
    QUARANTINE_RETENTION_DAYS,
    JournalConfig,
    _ensure_era_flag,
    build_evidence_recap,
    quarantine_summary,
    rotate_quarantine,
    update_event_index,
)

NOW = dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)


def make_config(tmp_path):
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


# ---- G2: era flag ----


def test_ensure_era_flag_marks_unkeyed_rows_legacy():
    row = {"type": "cron_run", "source": "old.log", "summary": "x"}
    _ensure_era_flag(row)
    assert row["legacy"] is True


def test_ensure_era_flag_keeps_keyed_rows_modern():
    row = {"type": "cron_run", "idempotency_key": "abc123"}
    _ensure_era_flag(row)
    assert row["legacy"] is False


def test_ensure_era_flag_is_idempotent():
    row = {"type": "cron_run", "legacy": True}
    _ensure_era_flag(row)
    _ensure_era_flag(row)
    assert row["legacy"] is True


def test_update_event_index_stamps_legacy_false_on_keyed_rows(tmp_path):
    config = make_config(tmp_path)
    index_dir = config.journal_root / ".index"
    index_dir.mkdir(parents=True)
    now = "2026-08-21T01:00:00Z"
    signals = [{"type": "cron_run", "job_name": "x", "idempotency_key": "k1"}]
    update_event_index(signals, now, config)
    day = index_dir / "events-2026-08-21.json"
    rows = json.loads(day.read_text())
    assert len(rows) == 1
    assert rows[0]["legacy"] is False


def test_update_event_index_stamps_legacy_true_on_unkeyed_rows(tmp_path):
    config = make_config(tmp_path)
    index_dir = config.journal_root / ".index"
    index_dir.mkdir(parents=True)
    now = "2026-08-21T01:00:00Z"
    update_event_index([{"type": "log_error", "source": "e.log"}], now, config)
    rows = json.loads((index_dir / "events-2026-08-21.json").read_text())
    assert rows[0]["legacy"] is True


# ---- G6: quarantine rotation + summary ----


def _write_quarantine(config, day, records):
    folder = config.journal_root / ".quarantine"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps(records))


def test_rotate_quarantine_removes_only_older_than_retention(tmp_path):
    config = make_config(tmp_path)
    old_day = (
        (NOW - dt.timedelta(days=QUARANTINE_RETENTION_DAYS + 2)).date().isoformat()
    )
    fresh_day = (NOW - dt.timedelta(days=3)).date().isoformat()
    _write_quarantine(config, old_day, [{"idempotency_key": "a"}])
    _write_quarantine(config, fresh_day, [{"idempotency_key": "b"}])
    (config.journal_root / ".quarantine" / "notes.json").write_text("[]")
    removed = rotate_quarantine(config, now=NOW)
    folder = config.journal_root / ".quarantine"
    assert removed == 1
    assert not (folder / f"{old_day}.json").exists()
    assert (folder / f"{fresh_day}.json").exists()
    assert (folder / "notes.json").exists()  # undated files never deleted


def test_rotate_quarantine_missing_dir_is_noop(tmp_path):
    config = make_config(tmp_path)
    assert rotate_quarantine(config, now=NOW) == 0


def test_quarantine_summary_reports_top_sources_in_window(tmp_path):
    config = make_config(tmp_path)
    d = NOW.date().isoformat()
    _write_quarantine(
        config,
        d,
        [
            {"idempotency_key": "1", "source": "/var/log/big.log"},
            {"idempotency_key": "2", "source": "/var/log/big.log"},
            {"idempotency_key": "3", "source": "/tmp/other.log"},
        ],
    )
    start = NOW - dt.timedelta(days=1)
    end = NOW
    summary = quarantine_summary(config, start, end)
    assert summary["total"] == 3
    assert summary["top"][0] == ("/var/log/big.log", 2)


def test_quarantine_summary_ignores_out_of_window_files(tmp_path):
    config = make_config(tmp_path)
    old_day = (
        (NOW - dt.timedelta(days=QUARANTINE_RETENTION_DAYS - 5)).date().isoformat()
    )
    _write_quarantine(config, old_day, [{"idempotency_key": "1", "source": "s"}])
    assert quarantine_summary(config, NOW - dt.timedelta(days=1), NOW) == {}


def test_quarantine_summary_empty_dir_returns_empty(tmp_path):
    config = make_config(tmp_path)
    (config.journal_root / ".quarantine").mkdir(parents=True)
    assert quarantine_summary(config, NOW - dt.timedelta(days=1), NOW) == {}


def test_build_evidence_recap_renders_quarantine_section(tmp_path):
    start = NOW - dt.timedelta(days=1)
    end = NOW
    quarantine = {"total": 42, "top": [("/var/log/x.log", 40), ("y", 2)]}
    md, _ = build_evidence_recap([], "daily", start, end, [], quarantine=quarantine)
    assert "### Quarantine (malformed noise, not signal)" in md
    assert "42 quarantined line(s)" in md
    assert "`/var/log/x.log` — 40" in md


def test_build_evidence_recap_no_quarantine_param_is_backward_compatible(tmp_path):
    start = NOW - dt.timedelta(days=1)
    end = NOW
    md, _ = build_evidence_recap([], "daily", start, end, [])
    assert "Quarantine" not in md
