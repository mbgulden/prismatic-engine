from __future__ import annotations

import sqlite3
from pathlib import Path

from prismatic.run_records import AgentRunRecordStore
from prismatic.gateway.server import _run_record_to_dict


def test_sqlite_store_reads_existing_runs_and_sorts_recent_first(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runs.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                issue_id TEXT,
                agent_name TEXT,
                status TEXT,
                started_at TEXT,
                completed_at TEXT,
                output_path TEXT,
                error_message TEXT
            )
            """
        )
        db.executemany(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "old-run",
                    "GRO-1562",
                    "ned",
                    "pending",
                    "2026-06-14T00:00:00+00:00",
                    None,
                    None,
                    None,
                ),
                (
                    "new-run",
                    "GRO-3343",
                    "jules",
                    "completed",
                    "2026-07-06T07:00:00+00:00",
                    "2026-07-06T07:02:30+00:00",
                    "/tmp/out.log",
                    None,
                ),
            ],
        )

    store = AgentRunRecordStore(str(db_path))

    recent = store.get_recent_runs(limit=2)
    assert [r.run_id for r in recent] == ["new-run", "old-run"]
    assert recent[0].status == "completed"

    payload = _run_record_to_dict(recent[0])
    assert payload["duration_seconds"] == 150.0


def test_sqlite_store_reloads_external_supervisor_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.db"
    store = AgentRunRecordStore(str(db_path))
    assert store.get_recent_runs() == []

    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "external-run",
                "GRO-3343",
                "agy",
                "failed",
                "2026-07-06T08:00:00+00:00",
                "2026-07-06T08:00:05+00:00",
                None,
                "boom",
            ),
        )

    reloaded = store.get_recent_runs(limit=1)
    assert len(reloaded) == 1
    assert reloaded[0].run_id == "external-run"
    assert reloaded[0].status == "failed"
