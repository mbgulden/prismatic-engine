from __future__ import annotations

import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from prismatic import dispatcher
from prismatic.jules_capacity import (
    DAILY_LIMIT,
    capacity_payload,
    parse_jules_remote_list,
    record_jules_launch,
    reconcile_jules_list_output,
)


def test_stable_identity_replay_is_idempotent_but_distinct_identities_split(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "private" / "jules.sqlite3"
    monkeypatch.setenv("PRISMATIC_JULES_CAPACITY_DB_PATH", str(db))
    key1 = record_jules_launch(
        issue_id="GRO-1",
        repository="prismatic-engine",
        source_path="/tmp/jules-GRO-1-a.log",
        launch_identity="request-GRO-1-one",
        launch_ts_utc="2026-07-21T10:00:00Z",
    )
    key2 = record_jules_launch(
        issue_id="GRO-1",
        repository="prismatic-engine",
        source_path="/tmp/jules-GRO-1-b.log",
        launch_identity="request-GRO-1-one",
        launch_ts_utc="2026-07-21T11:00:00Z",
        lifecycle_status="running",
    )
    key3 = record_jules_launch(
        issue_id="GRO-1",
        repository="prismatic-engine",
        source_path="/tmp/jules-GRO-1-c.log",
        launch_identity="request-GRO-1-two",
        launch_ts_utc="2026-07-21T11:30:00Z",
    )

    assert key1 == key2
    assert key3 != key1
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jules_launches").fetchone()[0] == 2
        row = conn.execute(
            "SELECT lifecycle_status FROM jules_launches WHERE launch_key=?", (key1,)
        ).fetchone()
        assert row[0] == "active"

    payload = capacity_payload(now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC))
    assert payload["limit"] == DAILY_LIMIT == 300
    assert payload["observed_launches"] == 2
    assert payload["remaining_observed_capacity"] == 298
    assert payload["active"] == 2
    assert payload["status"] == "partial_coverage"
    assert "current" not in payload


def test_parser_detects_auth_errors_even_with_zero_return_code() -> None:
    parsed = parse_jules_remote_list(
        "Login required: authenticate before listing sessions", returncode=0
    )
    assert parsed["available"] is False
    assert parsed["status"] == "unavailable"
    assert parsed["error_class"] == "auth_unavailable"
    assert parsed["sessions"] == []


def test_parser_handles_real_numeric_first_cli_table_and_labeled_rows() -> None:
    text = """
SESSION ID             TITLE                       UPDATED        STATUS
12345678901234567890   Synthetic completed task    now            Completed
22345678901234567890   Synthetic failed task       now            Failed
32345678901234567890   Needs human                 now            Awaiting User
42345678901234567890   Needs plan                  now            Awaiting Plan
52345678901234567890   Active blank status         now
session: safe-labeled-123 Completed
"""
    parsed = parse_jules_remote_list(text, returncode=0)
    statuses = [row["status"] for row in parsed["sessions"]]
    assert parsed["available"] is True
    assert statuses == [
        "completed",
        "failed",
        "awaiting_user",
        "awaiting_plan",
        "active",
        "completed",
    ]
    assert parsed["sessions"][0]["session_id"] == "12345678901234567890"
    assert parsed["sessions"][-1]["session_id"] == "safe-labeled-123"


def test_reconciliation_persists_success_and_unavailable_attempts_and_snapshot_freshness(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "private" / "jules.sqlite3"
    monkeypatch.setenv("PRISMATIC_JULES_CAPACITY_DB_PATH", str(db))
    record_jules_launch(
        issue_id="GRO-2",
        session_id="12345678901234567890",
        launch_identity="req-GRO-2",
        launch_ts_utc="2026-07-21T11:00:00Z",
    )

    success_at = "2026-07-21T11:30:00Z"
    result = reconcile_jules_list_output(
        "12345678901234567890 Synthetic Completed",
        returncode=0,
        attempted_at=success_at,
    )
    assert result["updated"] == 1
    payload = capacity_payload(now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC))
    assert payload["completed"] == 1
    assert payload["snapshot_at"] == success_at
    assert payload["snapshot_age_sec"] == 1800
    assert payload["status"] == "partial_coverage"

    unavailable_at = "2026-07-21T12:05:00Z"
    reconcile_jules_list_output(
        "Permission denied: login required token=SECRET",
        returncode=0,
        attempted_at=unavailable_at,
    )
    payload = capacity_payload(now=datetime(2026, 7, 21, 12, 6, tzinfo=UTC))
    assert payload["status"] == "unavailable"
    assert payload["snapshot_at"] == unavailable_at
    assert payload["errors"] == [
        {"source": "jules-remote-list", "error_class": "auth_unavailable"}
    ]
    assert "SECRET" not in str(payload)
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM jules_reconciliations").fetchone()[0]
            == 2
        )


def test_stale_and_zero_launch_coverage_matures_after_24_hours(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "private" / "jules.sqlite3"
    monkeypatch.setenv("PRISMATIC_JULES_CAPACITY_DB_PATH", str(db))
    reconcile_jules_list_output("", returncode=0, attempted_at="2026-07-20T00:00:00Z")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE jules_metadata SET value=? WHERE key='ledger_created_at'",
            ("2026-07-20T00:00:00Z",),
        )
        conn.commit()

    payload = capacity_payload(now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC))
    assert payload["observed_launches"] == 0
    assert payload["coverage_state"] == "fresh"
    assert payload["status"] == "stale"
    assert payload["snapshot_at"] == "2026-07-20T00:00:00Z"
    assert payload["snapshot_age_sec"] == 129600


def test_private_store_hashes_adversarial_identifiers_and_api_payload_has_no_nested_raw_text(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "private" / "jules.sqlite3"
    monkeypatch.setenv("PRISMATIC_JULES_CAPACITY_DB_PATH", str(db))
    record_jules_launch(
        issue_id="GRO-9\nCONTROL",
        repository="https://user:pass@example.com/repo.git?token=SECRET",
        source_path="/tmp/secret/token/path/ghp_ab...wxyz.log",
        session_id="12345678901234567890",
        launch_identity="github...2345",
        error_class="Traceback password=SECRET raw CLI output",
        launch_ts_utc="2026-07-21T10:00:00Z",
        lifecycle_status="failed",
    )
    payload = capacity_payload(now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC))
    forbidden = [
        "user:pass",
        "token=SECRET",
        "ghp_",
        "github_pat",
        "CONTROL",
        "raw CLI output",
        "GRO-9",
    ]
    for needle in forbidden:
        assert needle.lower() not in str(payload).lower()
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT launch_key, issue_id_hash, repository_hash, source_path_hash, error_class FROM jules_launches"
        ).fetchall()
    stored = str(rows)
    for needle in forbidden:
        assert needle.lower() not in stored.lower()
    assert "sha256:" in stored
    assert rows[0][4] == "cli_error"


def test_dispatcher_records_jules_capacity_before_launch_and_popen_failure_marks_failed(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "private" / "jules.sqlite3"
    worktree = tmp_path / "repo"
    worktree.mkdir()
    jules_bin = tmp_path / "jules"
    jules_bin.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    jules_bin.chmod(0o755)
    monkeypatch.setenv("PRISMATIC_JULES_CAPACITY_DB_PATH", str(db))
    monkeypatch.setenv("PRISMATIC_WORKTREE_PATH", str(worktree))
    monkeypatch.setenv("PRISMATIC_AGENT_RUN_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(dispatcher, "JULES_PATH", str(jules_bin))

    proc = MagicMock()
    proc.pid = 5678
    popen = MagicMock(return_value=proc)
    monkeypatch.setattr(dispatcher.subprocess, "Popen", popen)
    result = dispatcher.launch_jules(
        issue_id="issue-uuid",
        title="Do not store this title as prompt data",
        identifier="GRO-3",
        labels=["agent:jules"],
        request_id="req-GRO-3",
    )
    assert result is proc
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT lifecycle_status FROM jules_launches").fetchall()
    assert rows == [("accepted",)]

    def fail_popen(*args, **kwargs):
        raise OSError("/tmp/raw/path password=SECRET should not persist")

    monkeypatch.setattr(dispatcher.subprocess, "Popen", fail_popen)
    result = dispatcher.launch_jules(
        issue_id="issue-uuid",
        title="Do not store this title as prompt data",
        identifier="GRO-3",
        labels=["agent:jules"],
        request_id="req-GRO-3-failed",
    )
    assert result is None
    with sqlite3.connect(db) as conn:
        statuses = conn.execute(
            "SELECT lifecycle_status, error_class FROM jules_launches ORDER BY updated_at"
        ).fetchall()
    assert ("failed", "cli_error") in statuses
    assert "SECRET" not in str(statuses)
