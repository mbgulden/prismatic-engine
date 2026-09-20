"""Unit tests for SQLite Multi-Agent Concurrency & WAL Hardening (Directive 03).

Verifies:
1. Mandatory PRAGMAs (journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL, foreign_keys=ON).
2. execute_with_retry exponential backoff and transient lock contention resolution.
3. 10+ concurrent worker threads writing 100 turns simultaneously with 0 lock errors.
4. Fleet migration hook (PRAGMA wal_checkpoint(TRUNCATE)).
"""

import concurrent.futures
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from prismatic.fleet.db import (
    checkpoint_sqlite_database,
    execute_with_retry,
    init_sqlite_connection,
)
from prismatic.fleet.manager import PrismaticFleetManager


def test_init_sqlite_connection_pragmas():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conn = init_sqlite_connection(db_path, timeout_seconds=5.0)
        cursor = conn.cursor()

        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert str(journal_mode).lower() == "wal"

        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        assert busy_timeout == 5000

        cursor.execute("PRAGMA synchronous;")
        synchronous = cursor.fetchone()[0]
        # NORMAL is integer 1 in SQLite
        assert synchronous == 1

        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys = cursor.fetchone()[0]
        assert foreign_keys == 1

        cursor.close()
        conn.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_execute_with_retry_success():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conn = init_sqlite_connection(db_path)
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT);")

        # Test string query
        execute_with_retry(conn, "INSERT INTO test (val) VALUES (?);", ("turn_1",))

        # Test callable
        def _insert(c):
            c.execute("INSERT INTO test (val) VALUES (?);", ("turn_2",))

        execute_with_retry(conn, _insert)

        cur = conn.execute("SELECT COUNT(*) FROM test;")
        count = cur.fetchone()[0]
        assert count == 2
        conn.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_execute_with_retry_recovers_from_transient_lock():
    conn = MagicMock()
    mock_cursor = MagicMock()

    # Simulate 2 'database is locked' errors followed by success
    side_effects = [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("database is locked"),
        mock_cursor,
    ]
    conn.execute.side_effect = side_effects

    res = execute_with_retry(conn, "SELECT 1;", max_retries=4, initial_delay=0.01)
    assert res == mock_cursor
    assert conn.execute.call_count == 3


def test_concurrent_multi_agent_writes_no_lock_errors():
    """Spawn 10 concurrent threads writing 100 turns simultaneously to verify 0 lock errors."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Schema setup
        setup_conn = init_sqlite_connection(db_path)
        setup_conn.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                turn_idx INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        setup_conn.close()

        num_agents = 10
        turns_per_agent = 10
        total_turns = num_agents * turns_per_agent

        errors = []

        def worker_task(agent_name: str, agent_idx: int):
            for t_idx in range(turns_per_agent):
                try:
                    conn = init_sqlite_connection(db_path, timeout_seconds=5.0)
                    now_ts = time.time()
                    execute_with_retry(
                        conn,
                        """
                        INSERT INTO turns (agent_id, turn_idx, content, created_at)
                        VALUES (?, ?, ?, ?);
                        """,
                        (agent_name, t_idx, f"Turn {t_idx} by {agent_name}", now_ts),
                    )
                    conn.close()
                except Exception as exc:
                    errors.append(f"Worker {agent_name} turn {t_idx} failed: {exc}")

        agents = [f"agent_{i:02d}" for i in range(num_agents)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_agents) as executor:
            futures = [executor.submit(worker_task, agent, i) for i, agent in enumerate(agents)]
            concurrent.futures.wait(futures)

        # Assert 0 database is locked errors occurred
        assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors: {errors[:3]}"

        # Verify all 100 turns are present
        verify_conn = init_sqlite_connection(db_path, timeout_seconds=5.0)
        cursor = verify_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM turns;")
        recorded_turns = cursor.fetchone()[0]
        assert recorded_turns == total_turns, f"Expected {total_turns} turns, found {recorded_turns}"

        # Verify journal mode remains WAL
        cursor.execute("PRAGMA journal_mode;")
        final_journal_mode = cursor.fetchone()[0]
        assert str(final_journal_mode).lower() == "wal"

        cursor.close()
        verify_conn.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        wal_file = f"{db_path}-wal"
        if os.path.exists(wal_file):
            os.unlink(wal_file)
        shm_file = f"{db_path}-shm"
        if os.path.exists(shm_file):
            os.unlink(shm_file)


def test_checkpoint_sqlite_database():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conn = init_sqlite_connection(db_path)
        conn.execute("CREATE TABLE test_cp (id INTEGER PRIMARY KEY, name TEXT);")
        for i in range(50):
            conn.execute("INSERT INTO test_cp (name) VALUES (?);", (f"entry_{i}",))
        conn.commit()
        conn.close()

        res = checkpoint_sqlite_database(db_path, mode="TRUNCATE")
        assert res["status"] == "CHECKPOINTED"
        assert res["mode"] == "TRUNCATE"
        assert res["result"] is not None
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        for ext in ("-wal", "-shm"):
            p = f"{db_path}{ext}"
            if os.path.exists(p):
                os.unlink(p)


def test_fleet_manager_migrate_databases_to_wal():
    with tempfile.TemporaryDirectory() as temp_dir:
        hermes_dir = Path(temp_dir)
        # Create root state.db
        root_db = hermes_dir / "state.db"
        c1 = sqlite3.connect(root_db)
        c1.execute("CREATE TABLE test_root (id INTEGER PRIMARY KEY);")
        c1.close()

        # Create profiles
        p1_dir = hermes_dir / "profiles" / "kai"
        p1_dir.mkdir(parents=True)
        c2 = sqlite3.connect(p1_dir / "state.db")
        c2.execute("CREATE TABLE test_kai (id INTEGER PRIMARY KEY);")
        c2.close()

        p2_dir = hermes_dir / "profiles" / "ned"
        p2_dir.mkdir(parents=True)
        c3 = sqlite3.connect(p2_dir / "state.db")
        c3.execute("CREATE TABLE test_ned (id INTEGER PRIMARY KEY);")
        c3.close()

        mgr = PrismaticFleetManager(hermes_root=hermes_dir)
        res = mgr.migrate_databases_to_wal()

        assert res["total_scanned"] >= 3
        assert res["migrated_count"] >= 3
        assert res["errors_count"] == 0

        for item in res["migrated"]:
            assert item["journal_mode"] == "wal"
            assert item["status"] == "OK"
