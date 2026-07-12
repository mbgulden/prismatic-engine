import asyncio
import os
import sqlite3
import pytest
from pathlib import Path
from prismatic.gateway.event_bus import EventBus

def test_checkpoint_methods_exist():
    """Verify that checkpoint methods are defined on EventBus."""
    bus = EventBus()
    assert hasattr(bus, "start_checkpoint_task")
    assert hasattr(bus, "stop_checkpoint_task")
    assert hasattr(bus, "_checkpoint_db")

def test_checkpoint_task_lifecycle(tmp_path, monkeypatch):
    """Test starting and stopping the periodic checkpoint task."""
    db_path = tmp_path / "test_bus.sqlite"
    monkeypatch.setattr("prismatic.gateway.event_bus._BUS_DB_PATH", db_path)

    bus = EventBus()

    async def run_lifecycle():
        # Verify starting the task
        bus.start_checkpoint_task()
        assert hasattr(bus, "_checkpoint_task")
        assert bus._checkpoint_task is not None
        assert not bus._checkpoint_task.done()

        # Verify stopping/cancelling the task
        bus.stop_checkpoint_task()
        await asyncio.sleep(0.01)
        assert bus._checkpoint_task.cancelled() or bus._checkpoint_task.done()

    asyncio.run(run_lifecycle())

def test_checkpoint_db_execution(tmp_path, monkeypatch):
    """Test that _checkpoint_db successfully connects and executes TRUNCATE checkpoint."""
    db_path = tmp_path / "test_bus.sqlite"
    monkeypatch.setattr("prismatic.gateway.event_bus._BUS_DB_PATH", db_path)

    # Initialize the database and set journal_mode=WAL
    # We keep this connection open so that SQLite doesn't auto-delete the WAL file on close
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO test_table (val) VALUES ('test')")
    conn.commit()

    wal_path = Path(str(db_path) + "-wal")
    assert wal_path.exists(), "WAL file should exist while connection is open"
    assert wal_path.stat().st_size > 0, "WAL file should have some content"

    bus = EventBus()
    # Call the checkpoint method
    bus._checkpoint_db()

    # The checkpoint should execute cleanly and truncate the WAL file to 0 bytes
    assert wal_path.exists()
    assert wal_path.stat().st_size == 0

    # Clean up
    conn.close()
