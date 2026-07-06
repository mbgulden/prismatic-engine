"""Smoke tests for the Prismatic ingest → dispatch → state-sync spine.

These tests intentionally stay small and local: they exercise the same durable
SQLite/JSON state surfaces used by the live path without calling Linear or
launching real agent processes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def test_ingest_smoke_persists_local_task(tmp_path):
    """Ingest smoke: a task can enter the durable local queue."""
    from prismatic.local_tasks import LocalTaskQueue

    queue = LocalTaskQueue(tmp_path / "event_router.db")

    task = queue.create(
        title="Smoke ingest task",
        agent="agy",
        workspace=tmp_path,
        metadata={"source": "GRO-3499-smoke"},
    )

    stored = queue.get(task.id)
    assert stored.id.startswith("local-")
    assert stored.title == "Smoke ingest task"
    assert stored.agent == "agy"
    assert stored.status == "queued"
    assert stored.metadata["source"] == "GRO-3499-smoke"


def test_dispatch_smoke_moves_queued_task_to_dispatched(tmp_path, monkeypatch):
    """Dispatch smoke: the dispatcher consumes a queued task and records status."""
    from prismatic import dispatcher
    from prismatic.local_tasks import LocalTaskQueue

    queue = LocalTaskQueue(tmp_path / "event_router.db")
    task = queue.create(title="Smoke dispatch task", agent="agy", workspace=tmp_path)
    launched: list[tuple[str, str, dict]] = []

    def fake_launcher(issue_id: str, title: str = "", **kwargs):
        launched.append((issue_id, title, kwargs))
        return True

    class Dedup:
        def __init__(self):
            self.marked: list[tuple[str, str, str]] = []

        def is_processed(self, issue_id, label, cycle_id):
            return False

        def mark_processed(self, issue_id, label, cycle_id):
            self.marked.append((issue_id, label, cycle_id))

    monkeypatch.setattr(dispatcher, "AGENT_CONFIG", {"agy": {"mode": "launch"}})
    monkeypatch.setattr(dispatcher, "AGENT_LAUNCHERS", {"agy": fake_launcher})

    dedup: Any = Dedup()
    dispatched = dispatcher.dispatch_local_tasks(dedup, "cycle-smoke", local_task_queue=queue)

    assert dispatched == 1
    assert launched == [(task.id, "Smoke dispatch task", {"task": "Smoke dispatch task", "workspace": str(tmp_path.resolve())})]
    assert queue.get(task.id).status == "dispatched"


def test_state_sync_smoke_persists_run_completion(tmp_path):
    """State-sync smoke: run completion survives a fresh store instance."""
    from prismatic.run_records import AgentRunRecordStore

    store_path = tmp_path / "run_records.json"
    store = AgentRunRecordStore(str(store_path))
    run_id = store.create_run("GRO-3499", "ned")

    assert store.update_run(run_id, "completed", output_path=str(tmp_path / "RESULT.md"))

    reloaded = AgentRunRecordStore(str(store_path))
    record = reloaded.get_run(run_id)

    assert record is not None
    assert record.issue_id == "GRO-3499"
    assert record.agent_name == "ned"
    assert record.status == "completed"
    assert record.completed_at is not None
    assert record.output_path is not None
    assert Path(record.output_path).name == "RESULT.md"
