"""Tests for durable worker launch records (GRO-3490)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

from prismatic import dispatcher


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM launch_records ORDER BY created_at")]
    finally:
        conn.close()


def test_record_launch_record_creates_traceable_unique_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "event_router.db"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    sandbox = tmp_path / "sandbox-GRO-3490"
    sandbox.mkdir()
    monkeypatch.setenv("PRISMATIC_LAUNCH_RECORDS_DB_PATH", str(db_path))
    monkeypatch.setenv("PRISMATIC_WORKTREE_PATH", str(worktree))
    monkeypatch.setenv("PRISMATIC_SANDBOX_PATH", str(sandbox))

    first = dispatcher.record_launch_record(
        agent_name="agy",
        issue_id="issue-uuid",
        identifier="GRO-3490",
        cmd=["agy", "--issue", "issue-uuid"],
        pid=1234,
        labels=["agent:agy", "dispatch:ready"],
        cycle_id="cycle-1",
    )
    second = dispatcher.record_launch_record(
        agent_name="agy",
        issue_id="issue-uuid",
        identifier="GRO-3490",
        cmd=["agy", "--issue", "issue-uuid"],
        pid=5678,
        labels=["agent:agy", "dispatch:ready"],
        cycle_id="cycle-1",
    )

    rows = _rows(db_path)
    assert len(rows) == 2
    assert first != second
    assert {row["run_id"] for row in rows} == {first, second}
    for row in rows:
        assert row["issue_id"] == "issue-uuid"
        assert row["identifier"] == "GRO-3490"
        assert row["agent_name"] == "agy"
        assert row["handle_type"] == "sandbox"
        assert row["handle"] == str(sandbox)
        assert row["sandbox_path"] == str(sandbox)
        assert row["worktree_path"] == str(worktree)
        assert row["status"] == "launched"
        assert json.loads(row["labels_json"]) == ["agent:agy", "dispatch:ready"]
        assert json.loads(row["command_json"])[0] == "agy"


def test_launch_agy_persists_pid_command_and_worktree_handle(tmp_path, monkeypatch):
    db_path = tmp_path / "launches.db"
    worktree = tmp_path / "repo"
    worktree.mkdir()
    monkeypatch.setenv("PRISMATIC_LAUNCH_RECORDS_DB_PATH", str(db_path))
    monkeypatch.setenv("PRISMATIC_WORKTREE_PATH", str(worktree))
    monkeypatch.delenv("PRISMATIC_SANDBOX_PATH", raising=False)
    monkeypatch.setattr(dispatcher, "AGY_PATH", "/bin/echo")
    monkeypatch.setattr(dispatcher, "get_agy_model_from_labels", lambda labels: None)

    proc = MagicMock()
    proc.pid = 4242
    popen = MagicMock(return_value=proc)
    monkeypatch.setattr(dispatcher.subprocess, "Popen", popen)

    result = dispatcher.launch_agy(
        issue_id="issue-uuid",
        title="Persist launch records",
        identifier="GRO-3490",
        labels=["agent:agy"],
        cycle_id="cycle-3490",
        request_id="req-1",
    )

    assert result is proc
    rows = _rows(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["pid"] == 4242
    assert row["issue_id"] == "issue-uuid"
    assert row["identifier"] == "GRO-3490"
    assert row["agent_name"] == "agy"
    assert row["handle_type"] == "worktree"
    assert row["handle"] == str(worktree)
    assert row["worktree_path"] == str(worktree)
    assert row["cycle_id"] == "cycle-3490"
    assert row["request_id"] == "req-1"
    cmd = json.loads(row["command_json"])
    assert cmd[:3] == ["/bin/echo", "--headless", "--issue"]
    assert "Persist launch records" in cmd
