"""Tests for prismatic.run_records — JSON-file backed agent run records.

GRO-2402 follow-up: run_records.py is the most critical untested file in
the engine. It's used by:
- The gateway (creates a record when dispatch fires)
- The fleet watchdog (the `check_agent_run_freshness` we added reads
  this store to detect silent dispatch failures)

Until now, a bug in run_records would silently break observability. These
tests cover:
- AgentRunRecord dataclass shape and serialization
- AgentRunRecordStore: create/update/get/query operations
- Persistence: round-trip via disk write/read
- Concurrency: advisory file lock prevents corruption
- Reporting: Markdown report generation
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.run_records import (  # noqa: E402
    AgentRunRecord,
    AgentRunRecordStore,
)


# ── AgentRunRecord dataclass ─────────────────────────────────────────
def test_agent_run_record_defaults():
    r = AgentRunRecord(run_id="abc", issue_id="GRO-1", agent_name="fred")
    assert r.status == "pending"
    assert r.started_at == ""
    assert r.completed_at is None
    assert r.output_path is None
    assert r.error_message is None


def test_agent_run_record_from_dict_round_trip():
    data = {
        "run_id": "abc",
        "issue_id": "GRO-1",
        "agent_name": "fred",
        "status": "completed",
        "started_at": "2026-06-24T19:00:00+00:00",
        "completed_at": "2026-06-24T19:05:00+00:00",
        "output_path": "/tmp/result.md",
        "error_message": None,
    }
    r = AgentRunRecord.from_dict(data)
    assert r.run_id == "abc"
    assert r.status == "completed"
    assert r.output_path == "/tmp/result.md"


def test_agent_run_record_from_dict_ignores_unknown_fields():
    """Forward-compatible: extra fields don't break parsing."""
    data = {
        "run_id": "abc", "issue_id": "GRO-1", "agent_name": "fred",
        "future_field": "ignore me",
    }
    r = AgentRunRecord.from_dict(data)
    assert r.run_id == "abc"


# ── AgentRunRecordStore: basic CRUD ───────────────────────────────────
def test_store_creates_empty(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    assert store.record_count == 0
    assert store.all_records == []


def test_store_creates_parent_dir(tmp_path):
    nested = tmp_path / "deeply" / "nested" / "runs.json"
    store = AgentRunRecordStore(store_path=str(nested))
    assert nested.parent.exists()


def test_create_run_returns_uuid(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    assert isinstance(run_id, str)
    assert len(run_id) >= 32  # UUID4 string


def test_create_run_persists_to_disk(tmp_path):
    store_path = str(tmp_path / "runs.json")
    store = AgentRunRecordStore(store_path=store_path)
    run_id = store.create_run("GRO-1", "fred")
    # Read raw file to verify — it's a list of records (not a dict)
    data = json.loads(Path(store_path).read_text())
    assert isinstance(data, list)
    assert len(data) == 1
    record = data[0]
    assert record["run_id"] == run_id
    assert record["issue_id"] == "GRO-1"
    assert record["agent_name"] == "fred"
    assert record["status"] == "pending"


def test_create_run_sets_started_at_to_now(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    before = time.time()
    store.create_run("GRO-1", "fred")
    after = time.time()
    records = store.all_records
    assert len(records) == 1
    # started_at is ISO-8601; verify it parses to a timestamp in range
    from datetime import datetime
    ts = datetime.fromisoformat(records[0].started_at).timestamp()
    assert before <= ts <= after


def test_update_run_sets_status(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    ok = store.update_run(run_id, status="running")
    assert ok is True
    record = store.get_run(run_id)
    assert record.status == "running"


def test_update_run_completed_sets_completed_at(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    store.update_run(run_id, status="completed")
    record = store.get_run(run_id)
    assert record.status == "completed"
    assert record.completed_at is not None


def test_update_run_failed_sets_completed_at(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    store.update_run(run_id, status="failed", error="oops")
    record = store.get_run(run_id)
    assert record.status == "failed"
    assert record.error_message == "oops"
    assert record.completed_at is not None


def test_update_run_unknown_returns_false(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    ok = store.update_run("nonexistent", status="completed")
    assert ok is False


def test_update_run_sets_output_path(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    store.update_run(run_id, status="completed", output_path="/tmp/out.md")
    record = store.get_run(run_id)
    assert record.output_path == "/tmp/out.md"


# ── Query operations ─────────────────────────────────────────────────
def test_get_run_returns_record(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    record = store.get_run(run_id)
    assert record is not None
    assert record.run_id == run_id


def test_get_run_returns_none_for_unknown(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    assert store.get_run("nonexistent") is None


def test_get_runs_for_issue_returns_newest_first(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    store.create_run("GRO-1", "fred")
    time.sleep(0.01)  # Ensure timestamp ordering
    store.create_run("GRO-1", "kai")
    time.sleep(0.01)
    store.create_run("GRO-2", "ned")
    runs = store.get_runs_for_issue("GRO-1")
    assert len(runs) == 2
    assert runs[0].started_at > runs[1].started_at


def test_get_recent_runs_returns_limit(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    for i in range(15):
        store.create_run(f"GRO-{i}", "fred")
        time.sleep(0.001)
    recent = store.get_recent_runs(limit=5)
    assert len(recent) == 5
    # Newest first
    assert recent[0].started_at > recent[-1].started_at


# ── Persistence round-trip ───────────────────────────────────────────
def test_store_persists_across_instances(tmp_path):
    """Two store instances pointing at the same file share records."""
    path = str(tmp_path / "runs.json")
    s1 = AgentRunRecordStore(store_path=path)
    run_id = s1.create_run("GRO-1", "fred")
    # Second instance reads from disk
    s2 = AgentRunRecordStore(store_path=path)
    record = s2.get_run(run_id)
    assert record is not None
    assert record.issue_id == "GRO-1"


def test_reload_picks_up_external_writes(tmp_path):
    """reload() re-reads disk, picking up writes from another process."""
    path = str(tmp_path / "runs.json")
    store = AgentRunRecordStore(store_path=path)
    # External write — file format is a list of records
    external_data = [
        {
            "run_id": "abc", "issue_id": "GRO-9", "agent_name": "ned",
            "status": "completed", "started_at": "2026-06-24T18:00:00+00:00",
            "completed_at": "2026-06-24T18:05:00+00:00",
        }
    ]
    Path(path).write_text(json.dumps(external_data))
    # Without reload, store doesn't see it
    assert store.get_run("abc") is None
    store.reload()
    # Now it sees it
    record = store.get_run("abc")
    assert record is not None
    assert record.issue_id == "GRO-9"


# ── Concurrency: GRO-2402 regression for run_records ─────────────────
def test_concurrent_create_run_no_corruption(tmp_path):
    """10 threads creating 10 runs each → 100 records, no corruption."""
    path = str(tmp_path / "runs.json")
    store = AgentRunRecordStore(store_path=path)
    errors: list[Exception] = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(10):
                store.create_run(f"GRO-{thread_id}-{i}", f"agent-{thread_id}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors: {errors}"
    # All 100 records present
    assert store.record_count == 100
    # Reload from disk to verify the file is well-formed JSON (list shape)
    data = json.loads(Path(path).read_text())
    assert isinstance(data, list)
    assert len(data) == 100


# ── Reporting ─────────────────────────────────────────────────────────
def test_generate_report_empty(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    report = store.generate_report("GRO-NONE")
    assert "No run records found" in report


def test_generate_report_includes_all_runs(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    store.update_run(run_id, status="completed", output_path="/tmp/out.md")
    report = store.generate_report("GRO-1")
    assert "GRO-1" in report
    assert "fred" in report
    assert "completed" in report
    assert "✅" in report  # status emoji


def test_generate_report_includes_error(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    run_id = store.create_run("GRO-1", "fred")
    store.update_run(run_id, status="failed", error="timeout after 30s")
    report = store.generate_report("GRO-1")
    assert "failed" in report
    assert "timeout after 30s" in report
    assert "❌" in report


# ── Convenience properties ───────────────────────────────────────────
def test_all_records_returns_list(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    store.create_run("GRO-1", "fred")
    store.create_run("GRO-2", "kai")
    records = store.all_records
    assert len(records) == 2
    assert {r.issue_id for r in records} == {"GRO-1", "GRO-2"}


def test_record_count_reflects_mutations(tmp_path):
    store = AgentRunRecordStore(store_path=str(tmp_path / "runs.json"))
    assert store.record_count == 0
    store.create_run("GRO-1", "fred")
    assert store.record_count == 1
    store.create_run("GRO-2", "kai")
    assert store.record_count == 2
    # Updates don't add records
    run_id = store.create_run("GRO-3", "ned")
    assert store.record_count == 3
    store.update_run(run_id, status="completed")
    assert store.record_count == 3