"""Comprehensive tests for cron authority schema foundation (CRONAUTH-1).

Proves:
1. Fresh disposable DB migration creates the exact accepted version and tables.
2. Second migration is idempotent.
3. Unrelated pre-existing table/row survives byte-for-value.
4. Injected mid-migration failure fully rolls back.
5. Unknown/newer schema version fails closed without mutation.
6. Duplicate aggregate uniqueness tuple is rejected.
7. Duplicate execution ID is rejected.
8. Duplicate/global receipt ID and second receipt for the same attempt are rejected.
9. Attempt zero, illegal states, illegal outcomes, invalid digest, and oversized evidence are rejected.
10. Missing evidence FK is rejected.
11. Receipt and evidence UPDATE/DELETE attempts are rejected.
12. Attempt N and N+1 can coexist while each remains unique; no schema rule forces reconciliation to N+1.
13. Stale/invalid fence values and malformed lease timestamps are rejected where constrained.
14. Cursor scope collision is rejected and cursor regression is prevented by a database trigger.
15. Existing task-admission tables can coexist unchanged in the same disposable DB.
16. No test resolves or writes the production bus DB.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import sqlite3
from pathlib import Path

import pytest

from prismatic.cron_authority import (
    SCHEMA_VERSION,
    CronAuthorityError,
    CronAuthorityStore,
    connect_cron_authority,
    migrate_cron_authority,
    resolve_db_target,
)
from prismatic.task_admission import TaskAdmissionStore


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    """Return path to a fresh disposable test SQLite database."""
    return tmp_path / "test_cron_authority.sqlite"


@pytest.fixture(autouse=True)
def forbid_production_db_connections(monkeypatch: pytest.MonkeyPatch):
    """Fail every test before sqlite3 can open the production bus path."""
    real_connect = sqlite3.connect
    production = Path("/home/ubuntu/.prismatic/bus/event_log.sqlite")

    def guarded_connect(database, *args, **kwargs):
        value = str(database)
        if value != ":memory:" and not value.startswith("file:"):
            assert Path(value).resolve() != production
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)


@pytest.fixture
def sample_aggregate_kwargs() -> dict:
    return {
        "execution_id": "exec-20260728-0001",
        "cron_id": "cron.test-job",
        "registry_generation": 1,
        "schedule_bucket": "2026-07-28T04:00:00Z",
        "command_digest": "a" * 64,
        "release_digest": "b" * 64,
        "created_at": "2026-07-28T04:00:00Z",
    }


@pytest.fixture
def sample_attempt_kwargs(sample_aggregate_kwargs: dict) -> dict:
    return {
        "execution_id": sample_aggregate_kwargs["execution_id"],
        "attempt": 1,
        "state": "admitted",
        "runner_id": "runner-node-01",
        "fence_token": 1,
        "lease_expires_at": "2026-07-28T04:10:00Z",
        "created_at": "2026-07-28T04:00:00Z",
        "updated_at": "2026-07-28T04:00:00Z",
    }


@pytest.fixture
def sample_evidence_kwargs() -> dict:
    canonical_bytes = b'{"status":"ok"}'
    return {
        "evidence_digest": hashlib.sha256(canonical_bytes).hexdigest(),
        "canonical_bytes": canonical_bytes,
        "created_at": "2026-07-28T04:00:00Z",
    }


@pytest.fixture
def sample_receipt_kwargs(
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_evidence_kwargs: dict,
) -> dict:
    return {
        "receipt_id": "rcpt-20260728-0001",
        "execution_id": sample_aggregate_kwargs["execution_id"],
        "attempt": sample_attempt_kwargs["attempt"],
        "cron_id": sample_aggregate_kwargs["cron_id"],
        "outcome": "succeeded",
        "runner_id": sample_attempt_kwargs["runner_id"],
        "runner_release_digest": "f" * 64,
        "started_at": "2026-07-28T04:00:00Z",
        "finished_at": "2026-07-28T04:05:00Z",
        "error_classification": None,
        "evidence_digest": sample_evidence_kwargs["evidence_digest"],
        "signing_key_id": "key-v1",
        "signature": "sig-test-123",
        "schema_version": 1,
        "created_at": "2026-07-28T04:05:00Z",
    }


class FailingCursorWrapper:
    def __init__(self, real_cursor: sqlite3.Cursor):
        self.real_cursor = real_cursor

    def execute(self, sql: str, *args, **kwargs):
        if "cron_evidence" in sql:
            raise sqlite3.OperationalError("injected_mid_migration_failure")
        return self.real_cursor.execute(sql, *args, **kwargs)

    def fetchone(self):
        return self.real_cursor.fetchone()

    def fetchall(self):
        return self.real_cursor.fetchall()


class FailingConnectionWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn

    def cursor(self):
        return FailingCursorWrapper(self.real_conn.cursor())

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        if "cron_evidence" in sql:
            raise sqlite3.OperationalError("injected_mid_migration_failure")
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


def test_1_fresh_disposable_db_migration(disposable_db: Path):
    """Test 1: Fresh disposable DB migration creates exact accepted version and tables."""
    migrate_cron_authority(disposable_db)

    conn = connect_cron_authority(disposable_db)
    cursor = conn.cursor()

    # Check schema version
    cursor.execute(
        "SELECT authority_id, schema_version, installed_at FROM cron_authority_schema_version;"
    )
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0:2] == (1, SCHEMA_VERSION)
    assert rows[0][2].endswith("Z")
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO cron_authority_schema_version VALUES (2, 1, '2026-07-28T00:00:00Z');"
        )

    # Check required tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {r[0] for r in cursor.fetchall()}
    expected_tables = {
        "cron_authority_schema_version",
        "cron_execution_aggregates",
        "cron_evidence",
        "cron_execution_attempts",
        "cron_receipts",
        "cron_sweep_cursors",
    }
    assert expected_tables.issubset(tables)
    conn.close()


def test_2_migration_idempotency(disposable_db: Path):
    """Test 2: Second migration is idempotent and leaves DB unchanged."""
    migrate_cron_authority(disposable_db)
    migrate_cron_authority(disposable_db)

    conn = connect_cron_authority(disposable_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM cron_authority_schema_version;")
    assert cursor.fetchone()[0] == 1
    conn.close()


def test_3_unrelated_preexisting_table_survives(disposable_db: Path):
    """Test 3: Unrelated pre-existing table/row survives migration byte-for-value."""
    conn = sqlite3.connect(disposable_db)
    conn.execute("CREATE TABLE unrelated_data (id INT PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO unrelated_data (id, val) VALUES (42, 'preserved_value');")
    conn.commit()
    conn.close()

    migrate_cron_authority(disposable_db)

    conn = connect_cron_authority(disposable_db)
    cursor = conn.cursor()
    cursor.execute("SELECT id, val FROM unrelated_data;")
    row = cursor.fetchone()
    assert row == (42, "preserved_value")
    conn.close()


def test_4_injected_mid_migration_failure_rolls_back(disposable_db: Path):
    """Test 4: Injected mid-migration failure fully rolls back without partial tables."""
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = FailingConnectionWrapper(raw_conn)

    with pytest.raises(
        sqlite3.OperationalError, match="injected_mid_migration_failure"
    ):
        migrate_cron_authority(wrapper)

    # Verify no cron tables exist
    cursor = raw_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cursor.fetchall()]
    assert "cron_authority_schema_version" not in tables
    assert "cron_execution_aggregates" not in tables
    raw_conn.close()


def test_5_unknown_newer_schema_version_fails_closed(disposable_db: Path):
    """Test 5: Unknown/newer schema version fails closed without mutation."""
    conn = connect_cron_authority(disposable_db)
    conn.execute(
        "CREATE TABLE cron_authority_schema_version (authority_id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, installed_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO cron_authority_schema_version VALUES (1, 99, '2026-07-28T00:00:00Z');"
    )
    conn.commit()

    with pytest.raises(
        CronAuthorityError, match="Unsupported cron authority schema version rows"
    ):
        migrate_cron_authority(conn)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT authority_id, schema_version FROM cron_authority_schema_version;"
    )
    assert cursor.fetchone() == (1, 99)
    conn.close()


def test_6_duplicate_aggregate_uniqueness_rejected(
    disposable_db: Path, sample_aggregate_kwargs: dict
):
    """Test 6: Duplicate aggregate uniqueness tuple is rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        """INSERT INTO cron_execution_aggregates
        (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, release_digest, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?);""",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.commit()

    # Attempt duplicate aggregate tuple with different execution_id
    duplicate_tuple = dict(sample_aggregate_kwargs)
    duplicate_tuple["execution_id"] = "exec-20260728-0002"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO cron_execution_aggregates
            (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, release_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);""",
            tuple(duplicate_tuple.values()),
        )

    distinct_release = dict(
        duplicate_tuple,
        execution_id="exec-20260728-0003",
        release_digest="c" * 64,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(distinct_release.values()),
        )

    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        conn.execute(
            "INSERT OR REPLACE INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(distinct_release.values()),
        )

    replacement_by_id = dict(sample_aggregate_kwargs, cron_id="cron.rebound")
    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        conn.execute(
            "INSERT OR REPLACE INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(replacement_by_id.values()),
        )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE cron_execution_aggregates SET command_digest=? WHERE execution_id=?",
            ("d" * 64, sample_aggregate_kwargs["execution_id"]),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "DELETE FROM cron_execution_aggregates WHERE execution_id=?",
            (sample_aggregate_kwargs["execution_id"],),
        )

    assert conn.execute(
        "SELECT execution_id, cron_id, release_digest FROM cron_execution_aggregates"
    ).fetchall() == [
        (
            sample_aggregate_kwargs["execution_id"],
            sample_aggregate_kwargs["cron_id"],
            sample_aggregate_kwargs["release_digest"],
        )
    ]
    conn.close()


def test_7_duplicate_execution_id_rejected(
    disposable_db: Path, sample_aggregate_kwargs: dict
):
    """Test 7: Duplicate execution ID is rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        """INSERT INTO cron_execution_aggregates
        (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, release_digest, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?);""",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.commit()

    # Duplicate execution_id with different cron_id
    duplicate_id = dict(sample_aggregate_kwargs)
    duplicate_id["cron_id"] = "cron.different-job"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO cron_execution_aggregates
            (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, release_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);""",
            tuple(duplicate_id.values()),
        )
    conn.close()


def test_8_duplicate_receipt_id_and_second_receipt_for_attempt_rejected(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_evidence_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Test 8: Duplicate global receipt ID and second receipt for same attempt are rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_attempt_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_evidence VALUES (?, ?, ?);",
        tuple(sample_evidence_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_receipt_kwargs.values()),
    )
    conn.commit()

    # 8a. Duplicate global receipt ID under different execution/attempt
    aggregate_2 = dict(
        sample_aggregate_kwargs,
        execution_id="exec-20260728-0002",
        schedule_bucket="2026-07-28T05:00:00Z",
    )
    attempt_2 = dict(sample_attempt_kwargs, execution_id="exec-20260728-0002")
    receipt_dup_id = dict(sample_receipt_kwargs, execution_id="exec-20260728-0002")

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(aggregate_2.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(attempt_2.values()),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(receipt_dup_id.values()),
        )

    # 8b. Second receipt for the same attempt
    receipt_second_for_attempt = dict(
        sample_receipt_kwargs, receipt_id="rcpt-20260728-0002"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(receipt_second_for_attempt.values()),
        )

    conn.close()


def test_9_rejections_for_invalid_types_states_digests_and_evidence(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_evidence_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Test 9: Attempt 0, illegal states/outcomes, invalid digests, and oversized evidence are rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )

    # 9a. Attempt 0 rejected
    attempt_zero = dict(sample_attempt_kwargs, attempt=0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(attempt_zero.values()),
        )

    # Dynamic SQLite typing must not admit a textual attempt number.
    attempt_text = dict(sample_attempt_kwargs, attempt="not-an-integer")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(attempt_text.values()),
        )

    # 9b. Illegal attempt state rejected
    illegal_state = dict(sample_attempt_kwargs, state="illegal_state")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(illegal_state.values()),
        )

    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_attempt_kwargs.values()),
    )

    # 9c. Oversized evidence (> 4000 bytes) rejected
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_evidence VALUES (?, ?, ?);",
            ("f" * 64, b"x" * 4001, "2026-07-28T04:00:00Z"),
        )

    # 9d. Invalid evidence digest (uppercase hex rejected)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_evidence VALUES (?, ?, ?);",
            ("E" * 64, b"valid_bytes", "2026-07-28T04:00:00Z"),
        )

    # Canonical evidence is stored as bytes, never as SQLite TEXT.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_evidence VALUES (?, ?, ?);",
            ("f" * 64, "text-not-bytes", "2026-07-28T04:00:00Z"),
        )

    # 9e. Illegal receipt outcome rejected
    conn.execute(
        "INSERT INTO cron_evidence VALUES (?, ?, ?);",
        tuple(sample_evidence_kwargs.values()),
    )
    illegal_outcome = dict(sample_receipt_kwargs, outcome="invalid_outcome")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(illegal_outcome.values()),
        )

    conn.close()


def test_10_missing_evidence_foreign_key_rejected(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Test 10: Missing evidence FK is rejected when foreign keys are enabled."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_attempt_kwargs.values()),
    )

    # Receipt references missing evidence_digest 'e'*64
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(sample_receipt_kwargs.values()),
        )

    conn.close()


def test_11_receipt_and_evidence_immutability_triggers(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_evidence_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Test 11: Receipt/evidence mutations and INSERT OR REPLACE are rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_attempt_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_evidence VALUES (?, ?, ?);",
        tuple(sample_evidence_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_receipt_kwargs.values()),
    )
    conn.commit()

    # 11a. UPDATE cron_receipts rejected
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="immutable"
    ):
        conn.execute(
            "UPDATE cron_receipts SET outcome='failed' WHERE receipt_id=?;",
            (sample_receipt_kwargs["receipt_id"],),
        )

    # 11b. DELETE cron_receipts rejected
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="immutable"
    ):
        conn.execute(
            "DELETE FROM cron_receipts WHERE receipt_id=?;",
            (sample_receipt_kwargs["receipt_id"],),
        )

    # 11c. UPDATE cron_evidence rejected
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="immutable"
    ):
        conn.execute(
            "UPDATE cron_evidence SET canonical_bytes=? WHERE evidence_digest=?;",
            (b"mutated", sample_evidence_kwargs["evidence_digest"]),
        )

    # 11d. DELETE cron_evidence rejected
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="immutable"
    ):
        conn.execute(
            "DELETE FROM cron_evidence WHERE evidence_digest=?;",
            (sample_evidence_kwargs["evidence_digest"],),
        )

    replaced_evidence = dict(sample_evidence_kwargs, created_at="2026-07-28T05:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        conn.execute(
            "INSERT OR REPLACE INTO cron_evidence VALUES (?, ?, ?);",
            tuple(replaced_evidence.values()),
        )
    assert conn.execute(
        "SELECT created_at FROM cron_evidence WHERE evidence_digest=?",
        (sample_evidence_kwargs["evidence_digest"],),
    ).fetchone() == (sample_evidence_kwargs["created_at"],)

    replaced_receipt = dict(sample_receipt_kwargs, signature="CHANGED")
    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        conn.execute(
            "INSERT OR REPLACE INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(replaced_receipt.values()),
        )
    assert conn.execute(
        "SELECT signature FROM cron_receipts WHERE receipt_id=?",
        (sample_receipt_kwargs["receipt_id"],),
    ).fetchone() == (sample_receipt_kwargs["signature"],)

    conn.close()


def test_12_attempts_coexist_without_forced_reconciliation_to_next(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_evidence_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Test 12: Attempt N and N+1 coexist under the same aggregate while remaining unique."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_attempt_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_evidence VALUES (?, ?, ?);",
        tuple(sample_evidence_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_receipt_kwargs.values()),
    )

    # Add Attempt 2 under same aggregate
    attempt_2 = dict(sample_attempt_kwargs, attempt=2, state="admitted", fence_token=2)
    receipt_2 = dict(
        sample_receipt_kwargs,
        receipt_id="rcpt-20260728-0002",
        attempt=2,
        outcome="succeeded",
    )

    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(attempt_2.values()),
    )
    conn.execute(
        "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        tuple(receipt_2.values()),
    )
    conn.commit()

    cursor = conn.cursor()
    cursor.execute(
        "SELECT attempt, state FROM cron_execution_attempts WHERE execution_id=? ORDER BY attempt;",
        (sample_aggregate_kwargs["execution_id"],),
    )
    assert cursor.fetchall() == [(1, "terminal"), (2, "terminal")]

    cursor.execute(
        "SELECT receipt_id, attempt, outcome FROM cron_receipts WHERE execution_id=? ORDER BY attempt;",
        (sample_aggregate_kwargs["execution_id"],),
    )
    assert cursor.fetchall() == [
        ("rcpt-20260728-0001", 1, "succeeded"),
        ("rcpt-20260728-0002", 2, "succeeded"),
    ]

    conn.close()


def test_13_fence_and_lease_timestamp_constraints(
    disposable_db: Path, sample_aggregate_kwargs: dict, sample_attempt_kwargs: dict
):
    """Test 13: Stale/invalid fence values and malformed lease timestamps are rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )

    # 13a. Non-positive fence token (0) rejected
    bad_fence = dict(sample_attempt_kwargs, fence_token=0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(bad_fence.values()),
        )

    bad_fence_type = dict(sample_attempt_kwargs, fence_token="not-an-integer")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(bad_fence_type.values()),
        )

    # 13b. Malformed lease timestamp (missing Z suffix) rejected
    bad_timestamp = dict(sample_attempt_kwargs, lease_expires_at="2026-07-28T04:10:00")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(bad_timestamp.values()),
        )

    conn.close()


def test_14_cursor_scope_collision_and_regression_trigger(disposable_db: Path):
    """Test 14: Contract scope is unique and integer cursor progress is monotonic."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    cursor_data = (
        "scope-job1-gen1",
        "cron.test-job",
        1,
        "a" * 64,
        1,
        9,
        "2026-07-28T04:00:00Z",
    )
    conn.execute(
        "INSERT INTO cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);",
        cursor_data,
    )
    conn.commit()

    # Primary scope-key collision is rejected.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);",
            (
                "scope-job1-gen1",
                "cron.other",
                1,
                "b" * 64,
                1,
                1,
                "2026-07-28T05:00:00Z",
            ),
        )

    # The normative contract tuple plus policy version is independently unique.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);",
            (
                "different-key",
                "cron.test-job",
                1,
                "a" * 64,
                1,
                10,
                "2026-07-28T05:00:00Z",
            ),
        )

    # Numeric progress across a digit-width boundary succeeds.
    conn.execute(
        "UPDATE cron_sweep_cursors SET cursor_value=10 WHERE scope_key='scope-job1-gen1';"
    )
    conn.commit()
    assert conn.execute(
        "SELECT cursor_value FROM cron_sweep_cursors WHERE scope_key='scope-job1-gen1';"
    ).fetchone() == (10,)

    replacement = (*cursor_data[:5], 1, "2026-07-28T05:00:00Z")
    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        conn.execute(
            "INSERT OR REPLACE INTO cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);",
            replacement,
        )
    assert conn.execute(
        "SELECT cursor_value FROM cron_sweep_cursors WHERE scope_key='scope-job1-gen1';"
    ).fetchone() == (10,)

    # Regression and dynamically typed non-integer values fail closed.
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="regression"
    ):
        conn.execute(
            "UPDATE cron_sweep_cursors SET cursor_value=8 WHERE scope_key='scope-job1-gen1';"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE cron_sweep_cursors SET cursor_value='not-an-integer' WHERE scope_key='scope-job1-gen1';"
        )
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute(
            "DELETE FROM cron_sweep_cursors WHERE scope_key='scope-job1-gen1';"
        )
    assert conn.execute(
        "SELECT cursor_value FROM cron_sweep_cursors WHERE scope_key='scope-job1-gen1';"
    ).fetchone() == (10,)

    conn.close()


def test_15_task_admission_coexistence(disposable_db: Path):
    """Test 15: Task admission tables can coexist unchanged in the same disposable DB."""
    # Initialize task admission store and create table first
    store = TaskAdmissionStore(db_path=disposable_db)
    admission_conn = store._connect()
    admission_conn.close()

    conn = connect_cron_authority(disposable_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='task_admissions';"
    )
    assert cursor.fetchone() is not None

    # Migrate cron authority into the same DB
    migrate_cron_authority(conn)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {r[0] for r in cursor.fetchall()}
    assert "task_admissions" in tables
    assert "cron_authority_schema_version" in tables
    assert "cron_execution_aggregates" in tables
    conn.close()


def test_16_production_bus_db_untouched():
    """Test 16: API requires an explicit target and the autouse guard forbids production."""
    target = inspect.signature(migrate_cron_authority).parameters["target"]
    assert target.default is inspect.Parameter.empty
    assert resolve_db_target(":memory:") == ":memory:"


def test_17_exact_schema_and_trigger_validation_fail_closed(disposable_db: Path):
    """Version watermarks never bless missing, malformed, or squatted objects."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    conn.execute("DROP TRIGGER trg_cron_receipts_no_update")
    conn.commit()
    conn.close()

    with pytest.raises(CronAuthorityError) as missing:
        migrate_cron_authority(disposable_db)
    assert missing.value.code == "schema_object_mismatch"

    squatted = disposable_db.with_name("squatted.sqlite")
    conn = sqlite3.connect(squatted)
    conn.executescript(
        """
        CREATE TABLE unrelated(id INTEGER PRIMARY KEY);
        CREATE TRIGGER trg_cron_receipts_no_update
        BEFORE UPDATE ON unrelated BEGIN SELECT 1; END;
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(CronAuthorityError) as collision:
        migrate_cron_authority(squatted)
    assert collision.value.code == "schema_object_mismatch"
    conn = sqlite3.connect(squatted)
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='cron_authority_schema_version'"
    ).fetchone() == (0,)
    conn.close()

    temp_squatted = disposable_db.with_name("temp-squatted.sqlite")
    conn = sqlite3.connect(temp_squatted)
    conn.execute(
        """
        CREATE TEMP TABLE cron_authority_schema_version (
            authority_id INTEGER,
            schema_version INTEGER,
            installed_at TEXT
        )
        """
    )
    assert not conn.in_transaction
    with pytest.raises(CronAuthorityError) as temp_collision:
        migrate_cron_authority(conn)
    assert temp_collision.value.code == "schema_object_mismatch"
    assert conn.execute(
        "SELECT count(*) FROM main.sqlite_master WHERE name='cron_authority_schema_version'"
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT count(*) FROM temp.sqlite_master WHERE name='cron_authority_schema_version'"
    ).fetchone() == (1,)
    conn.close()


def test_18_active_caller_transaction_is_rejected_without_rollback(disposable_db: Path):
    """Connection setup never mutates or rolls back a caller-owned transaction."""
    conn = sqlite3.connect(disposable_db)
    conn.execute("CREATE TABLE caller_work(value TEXT)")
    conn.execute("INSERT INTO caller_work VALUES ('pending')")
    assert conn.in_transaction

    with pytest.raises(CronAuthorityError) as active:
        connect_cron_authority(conn)
    assert active.value.code == "active_caller_transaction"
    assert conn.in_transaction
    assert conn.execute("SELECT value FROM caller_work").fetchall() == [("pending",)]

    conn.rollback()
    configured = connect_cron_authority(conn)
    assert configured.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert configured.execute("PRAGMA recursive_triggers").fetchone() == (1,)
    conn.close()


def test_19_release_evidence_and_receipt_identity_constraints(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_evidence_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Release identity, evidence address, outcome evidence, and cron identity are durable."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    aggregate_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(cron_execution_aggregates)")
    }
    assert "release_digest" in aggregate_columns
    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(sample_attempt_kwargs.values()),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_evidence VALUES (?, ?, ?)",
            ("e" * 64, b'{"status":"ok"}', "2026-07-28T04:00:00Z"),
        )
    conn.execute(
        "INSERT INTO cron_evidence VALUES (?, ?, ?)",
        tuple(sample_evidence_kwargs.values()),
    )

    failed_without_evidence = dict(
        sample_receipt_kwargs,
        outcome="failed",
        evidence_digest=None,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(failed_without_evidence.values()),
        )

    wrong_cron = dict(sample_receipt_kwargs, cron_id="cron.other")
    with pytest.raises(sqlite3.IntegrityError, match="identity"):
        conn.execute(
            "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(wrong_cron.values()),
        )

    conn.execute(
        "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(sample_receipt_kwargs.values()),
    )
    conn.commit()
    conn.close()


def test_20_ownership_transition_fence_terminal_and_timestamp_guards(
    disposable_db: Path,
    sample_aggregate_kwargs: dict,
    sample_attempt_kwargs: dict,
    sample_receipt_kwargs: dict,
):
    """Ownership, transitions, fences, terminality, and calendar-valid UTC are fail-closed."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
        tuple(sample_aggregate_kwargs.values()),
    )

    invalid_time = dict(
        sample_aggregate_kwargs,
        execution_id="bad-time",
        schedule_bucket="2026-99-99T99:99:99junkZ",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(invalid_time.values()),
        )

    impossible_schedule = dict(
        sample_aggregate_kwargs,
        execution_id="bad-calendar-schedule",
        schedule_bucket="2026-02-30T04:00:00Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(impossible_schedule.values()),
        )

    impossible_created = dict(
        sample_aggregate_kwargs,
        execution_id="bad-calendar-created",
        command_digest="c" * 64,
        created_at="2026-04-31T04:00:00Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(impossible_created.values()),
        )

    claimed_without_owner = dict(
        sample_attempt_kwargs,
        state="claimed",
        runner_id=None,
        fence_token=None,
        lease_expires_at=None,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(claimed_without_owner.values()),
        )

    skipped_first_attempt = dict(
        sample_attempt_kwargs,
        attempt=2,
        state="admitted",
        runner_id=None,
        fence_token=None,
        lease_expires_at=None,
    )
    with pytest.raises(sqlite3.IntegrityError, match="start at 1"):
        conn.execute(
            "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(skipped_first_attempt.values()),
        )

    admitted = dict(
        sample_attempt_kwargs,
        attempt=1,
        state="admitted",
        runner_id=None,
        fence_token=None,
        lease_expires_at=None,
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts (execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(admitted.values()),
    )
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute(
            "DELETE FROM cron_execution_attempts WHERE execution_id=? AND attempt=1",
            (sample_aggregate_kwargs["execution_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="requires matching receipt"):
        conn.execute(
            "UPDATE cron_execution_attempts SET state='terminal' WHERE execution_id=? AND attempt=1",
            (sample_aggregate_kwargs["execution_id"],),
        )

    conn.execute(
        """UPDATE cron_execution_attempts
        SET state='claimed', runner_id='runner-a', fence_token=1,
            lease_expires_at='2026-07-28T04:10:00Z'
        WHERE execution_id=? AND attempt=1""",
        (sample_aggregate_kwargs["execution_id"],),
    )
    with pytest.raises(sqlite3.IntegrityError, match="invalid renewal"):
        conn.execute(
            "UPDATE cron_execution_attempts SET fence_token=2 WHERE execution_id=? AND attempt=1",
            (sample_aggregate_kwargs["execution_id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="invalid renewal"):
        conn.execute(
            "UPDATE cron_execution_attempts SET runner_id='runner-b' WHERE execution_id=? AND attempt=1",
            (sample_aggregate_kwargs["execution_id"],),
        )
    conn.execute(
        "UPDATE cron_execution_attempts SET runner_id='runner-b', fence_token=2 WHERE execution_id=? AND attempt=1",
        (sample_aggregate_kwargs["execution_id"],),
    )
    with pytest.raises(sqlite3.IntegrityError, match="illegal attempt"):
        conn.execute(
            "UPDATE cron_execution_attempts SET state='admitted' WHERE execution_id=? AND attempt=1",
            (sample_aggregate_kwargs["execution_id"],),
        )

    receipt = dict(
        sample_receipt_kwargs,
        runner_id="runner-b",
        evidence_digest=None,
    )
    conn.execute(
        "INSERT INTO cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(receipt.values()),
    )
    assert conn.execute(
        "SELECT state FROM cron_execution_attempts WHERE execution_id=? AND attempt=1",
        (sample_aggregate_kwargs["execution_id"],),
    ).fetchone() == ("terminal",)
    with pytest.raises(sqlite3.IntegrityError, match="terminal attempts"):
        conn.execute(
            "UPDATE cron_execution_attempts SET updated_at='2026-07-28T04:01:00Z' WHERE execution_id=? AND attempt=1",
            (sample_aggregate_kwargs["execution_id"],),
        )
    conn.close()


def test_21_schema_v3_migration_and_snapshot_v2(disposable_db: Path, tmp_path: Path):
    """v1/v2 to v3 migration preserves legacy snapshots and installs v2 snapshots with schedule authority."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    ver = conn.execute(
        "SELECT schema_version FROM cron_authority_schema_version WHERE authority_id = 1;"
    ).fetchone()[0]
    assert ver == 3
    conn.close()

    # Install v2 snapshot
    trusted_parent = tmp_path / "releases"
    trusted_parent.mkdir(parents=True, exist_ok=True)
    os.chmod(trusted_parent, 0o755)
    orig_parent = CronAuthorityStore.get_trusted_release_parent()
    CronAuthorityStore.set_trusted_release_parent(trusted_parent)

    try:
        rel_root = trusted_parent / "a1b2c3d4e5f607182930a1b2c3d4e5f607182930"
        rel_root.mkdir(parents=True, exist_ok=True)
        os.chmod(rel_root, 0o755)
        app_dir = rel_root / "app"
        app_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(app_dir, 0o755)
        exe_file = app_dir / "worker"
        exe_file.write_bytes(b"#!/usr/bin/env python3\nprint('OK')\n")
        os.chmod(exe_file, 0o755)

        st_root = os.stat(rel_root)
        st_cwd = os.stat(app_dir)
        st_exe = os.stat(exe_file)
        exe_digest = hashlib.sha256(
            b"#!/usr/bin/env python3\nprint('OK')\n"
        ).hexdigest()

        rel_ev = {
            "canonical_path": str(rel_root),
            "device": st_root.st_dev,
            "inode": st_root.st_ino,
            "object_type": "directory",
            "owner": str(st_root.st_uid),
            "mode": st_root.st_mode,
        }
        cwd_ev = {
            "canonical_path": str(app_dir),
            "device": st_cwd.st_dev,
            "inode": st_cwd.st_ino,
            "object_type": "directory",
            "owner": str(st_cwd.st_uid),
            "mode": st_cwd.st_mode,
        }
        exe_ev = {
            "canonical_path": str(exe_file),
            "device": st_exe.st_dev,
            "inode": st_exe.st_ino,
            "object_type": "regular_executable",
            "owner": str(st_exe.st_uid),
            "mode": st_exe.st_mode,
            "content_digest": exe_digest,
        }

        argv = ["worker"]
        cwd = str(app_dir)
        from prismatic.cron_runner import compute_command_digest

        cmd_digest = compute_command_digest(tuple(argv), cwd)

        v2_snap_dict = {
            "schema_id": "prismatic.cron.registry-snapshot",
            "schema_version": 2,
            "source_id": "prismatic.cron-authority.sqlite/cron_registry_snapshots_v2",
            "cron_id": "cron.v2.job",
            "registry_generation": 1,
            "trusted_runner_identity": "runner_v2",
            "command_digest": cmd_digest,
            "release_digest": "2" * 64,
            "dependency_digest": "0" * 64,
            "release_root": str(rel_root),
            "release_root_evidence": rel_ev,
            "argv": argv,
            "executable_evidence": exe_ev,
            "cwd": cwd,
            "cwd_evidence": cwd_ev,
            "state": "active",
            "depends_on": [],
            "catch_up_policy": "run_once",
            "max_replay_buckets": 10,
            "schedule": "0 12 * * *",
            "schedule_timezone": "America/New_York",
        }

        res = CronAuthorityStore.install_registry_snapshot_v2(
            db_target=disposable_db,
            registry_generation=1,
            snapshot_data=v2_snap_dict,
        )
        assert res["status"] == "installed"
        assert (
            res["source_id"]
            == "prismatic.cron-authority.sqlite/cron_registry_snapshots_v2"
        )

        read_back = CronAuthorityStore.read_registry_snapshot_v2(
            db_target=disposable_db,
            source_id="prismatic.cron-authority.sqlite/cron_registry_snapshots_v2",
            registry_generation=1,
            snapshot_digest=res["snapshot_digest"],
        )
        assert read_back["schedule"] == "0 12 * * *"
        assert read_back["schedule_timezone"] == "America/New_York"
    finally:
        CronAuthorityStore.set_trusted_release_parent(orig_parent)


def test_22_projection_source_reader_adversarial(disposable_db: Path):
    """Projection source reader returns all 6 row families, handles WAL, rejects connection objects, and causes zero side-effects."""
    from prismatic.cron_authority import (
        CronProjectionSource,
        read_cron_projection_source,
    )

    migrate_cron_authority(disposable_db)

    conn = connect_cron_authority(disposable_db)
    with pytest.raises(CronAuthorityError) as conn_exc:
        read_cron_projection_source(conn)
    assert conn_exc.value.code == "connection_rejected"
    conn.close()

    with pytest.raises(CronAuthorityError) as non_exc:
        read_cron_projection_source(disposable_db / "nonexistent.db")
    assert non_exc.value.code == "nonexistent_database"

    proj = read_cron_projection_source(disposable_db)
    assert isinstance(proj, CronProjectionSource)
    assert isinstance(proj.registry_snapshots, tuple)
    assert isinstance(proj.trigger_deliveries, tuple)
    assert isinstance(proj.execution_aggregates, tuple)
    assert isinstance(proj.execution_attempts, tuple)
    assert isinstance(proj.terminal_receipts, tuple)
    assert isinstance(proj.sweep_cursors, tuple)

    proj2 = read_cron_projection_source(disposable_db)
    assert proj == proj2


def _create_true_v2_db(db_path: Path) -> dict:
    """Private test helper building a true schema version 2 SQLite database directly from frozen v2 DDL/objects."""
    # Independent oracle frozen from the accepted legacy-v2 object identity.
    # Runtime migration code must never import or consume this test-local DDL.
    frozen_v2_ddl = (
        """
CREATE TABLE IF NOT EXISTS main.cron_authority_schema_version (
    authority_id INTEGER PRIMARY KEY CHECK (authority_id = 1),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version >= 1),
    installed_at TEXT NOT NULL CHECK (is_utc_timestamp(installed_at) = 1)
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_execution_aggregates (
    execution_id TEXT PRIMARY KEY CHECK (length(execution_id) >= 1 AND length(execution_id) <= 128),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    schedule_bucket TEXT NOT NULL CHECK (is_utc_timestamp(schedule_bucket) = 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    release_digest TEXT NOT NULL CHECK (length(release_digest) = 64 AND release_digest NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    CONSTRAINT uq_cron_aggregate UNIQUE (cron_id, registry_generation, schedule_bucket, command_digest)
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_evidence (
    evidence_digest TEXT PRIMARY KEY CHECK (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 4000),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    CONSTRAINT ck_evidence_content_address CHECK (evidence_digest = sha256_hex(canonical_bytes))
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_execution_attempts (
    execution_id TEXT NOT NULL REFERENCES cron_execution_aggregates(execution_id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),
    state TEXT NOT NULL CHECK (state IN ('admitted', 'claimed', 'running', 'reconciling', 'terminal')),
    runner_id TEXT CHECK (runner_id IS NULL OR (length(runner_id) >= 1 AND length(runner_id) <= 128)),
    fence_token INTEGER CHECK (fence_token IS NULL OR (typeof(fence_token) = 'integer' AND fence_token > 0)),
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR is_utc_timestamp(lease_expires_at) = 1),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),
    CONSTRAINT ck_attempt_ownership CHECK (
        state IN ('admitted', 'terminal')
        OR (runner_id IS NOT NULL AND fence_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    PRIMARY KEY (execution_id, attempt)
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_receipts (
    receipt_id TEXT PRIMARY KEY CHECK (length(receipt_id) >= 1 AND length(receipt_id) <= 128),
    execution_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'timed_out', 'cancelled', 'blocked', 'missed_during_offline', 'awaiting_operator_approval', 'orphaned', 'reconciled')),
    runner_id TEXT NOT NULL CHECK (length(runner_id) >= 1 AND length(runner_id) <= 128),
    runner_release_digest TEXT NOT NULL CHECK (length(runner_release_digest) = 64 AND runner_release_digest NOT GLOB '*[^0-9a-f]*'),
    started_at TEXT NOT NULL CHECK (is_utc_timestamp(started_at) = 1),
    finished_at TEXT NOT NULL CHECK (is_utc_timestamp(finished_at) = 1),
    error_classification TEXT CHECK (error_classification IS NULL OR length(error_classification) <= 128),
    evidence_digest TEXT REFERENCES cron_evidence(evidence_digest) ON DELETE RESTRICT CHECK (evidence_digest IS NULL OR (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*')),
    signing_key_id TEXT NOT NULL CHECK (length(signing_key_id) >= 1 AND length(signing_key_id) <= 128),
    signature TEXT NOT NULL CHECK (length(signature) >= 1 AND length(signature) <= 512),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    CONSTRAINT ck_required_outcome_evidence CHECK (
        outcome NOT IN ('failed', 'timed_out', 'blocked', 'missed_during_offline', 'awaiting_operator_approval', 'orphaned', 'reconciled')
        OR evidence_digest IS NOT NULL
    ),
    CONSTRAINT uq_receipt_execution_attempt UNIQUE (execution_id, attempt),
    CONSTRAINT fk_receipt_execution_attempt FOREIGN KEY (execution_id, attempt) REFERENCES cron_execution_attempts(execution_id, attempt) ON DELETE RESTRICT
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_sweep_cursors (
    scope_key TEXT PRIMARY KEY CHECK (length(scope_key) >= 1 AND length(scope_key) <= 256),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    catch_up_policy_version INTEGER NOT NULL CHECK (typeof(catch_up_policy_version) = 'integer' AND catch_up_policy_version >= 1),
    cursor_value INTEGER NOT NULL CHECK (typeof(cursor_value) = 'integer' AND cursor_value >= 0),
    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),
    CONSTRAINT uq_cron_sweep_scope UNIQUE (cron_id, registry_generation, command_digest, catch_up_policy_version)
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_trigger_deliveries (
    trigger_event_id TEXT PRIMARY KEY CHECK (length(trigger_event_id) >= 1 AND length(trigger_event_id) <= 128),
    trigger_digest TEXT NOT NULL CHECK (length(trigger_digest) = 64 AND trigger_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 4000),
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('scheduled', 'manual', 'retry', 'hook', 'recovery')),
    transport_kind TEXT NOT NULL CHECK (transport_kind IN ('http', 'hook', 'recovery', 'internal')),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    schedule_bucket TEXT NOT NULL CHECK (is_utc_timestamp(schedule_bucket) = 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    release_digest TEXT NOT NULL CHECK (length(release_digest) = 64 AND release_digest NOT GLOB '*[^0-9a-f]*'),
    execution_id TEXT REFERENCES cron_execution_aggregates(execution_id) ON DELETE RESTRICT,
    disposition TEXT NOT NULL CHECK (disposition IN ('accepted', 'rejected', 'converged')),
    reason_code TEXT NOT NULL CHECK (length(reason_code) >= 1 AND length(reason_code) <= 128),
    submitted_at TEXT NOT NULL CHECK (is_utc_timestamp(submitted_at) = 1),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    CONSTRAINT ck_trigger_delivery_content_address CHECK (trigger_digest = sha256_hex(canonical_bytes))
);
""",
        """
CREATE TABLE IF NOT EXISTS main.cron_registry_snapshots_v1 (
    source_id TEXT NOT NULL CHECK (source_id = 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v1'),
    schema_id TEXT NOT NULL CHECK (schema_id = 'prismatic.cron.registry-snapshot'),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64 AND snapshot_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 1048576),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    PRIMARY KEY (source_id, registry_generation),
    CONSTRAINT uq_snapshot_digest UNIQUE (source_id, snapshot_digest)
);
""",
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_aggregate_insert_collision
    BEFORE INSERT ON cron_execution_aggregates
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_execution_aggregates
        WHERE execution_id = NEW.execution_id
           OR (
               cron_id = NEW.cron_id
               AND registry_generation = NEW.registry_generation
               AND schedule_bucket = NEW.schedule_bucket
               AND command_digest = NEW.command_digest
           )
    )
    BEGIN
        SELECT RAISE(ABORT, 'cron execution aggregates cannot be replaced');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_aggregates_no_update
    BEFORE UPDATE ON cron_execution_aggregates
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron execution aggregates are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_aggregates_no_delete
    BEFORE DELETE ON cron_execution_aggregates
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron execution aggregates are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_evidence_insert_collision
    BEFORE INSERT ON cron_evidence
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_evidence WHERE evidence_digest = NEW.evidence_digest
    )
    BEGIN
        SELECT RAISE(ABORT, 'cron_evidence rows cannot be replaced');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_receipt_insert_collision
    BEFORE INSERT ON cron_receipts
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_receipts
        WHERE receipt_id = NEW.receipt_id
           OR (execution_id = NEW.execution_id AND attempt = NEW.attempt)
    )
    BEGIN
        SELECT RAISE(ABORT, 'cron_receipts rows cannot be replaced');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_cursor_insert_collision
    BEFORE INSERT ON cron_sweep_cursors
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_sweep_cursors
        WHERE scope_key = NEW.scope_key
           OR (
               cron_id = NEW.cron_id
               AND registry_generation = NEW.registry_generation
               AND command_digest = NEW.command_digest
               AND catch_up_policy_version = NEW.catch_up_policy_version
           )
    )
    BEGIN
        SELECT RAISE(ABORT, 'cursor rows cannot be replaced');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_evidence_no_update
    BEFORE UPDATE ON cron_evidence
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_evidence rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_evidence_no_delete
    BEFORE DELETE ON cron_evidence
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_evidence rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_receipts_no_update
    BEFORE UPDATE ON cron_receipts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_receipts rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_receipts_no_delete
    BEFORE DELETE ON cron_receipts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_receipts rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_sweep_cursors_prevent_regression
    BEFORE UPDATE ON cron_sweep_cursors
    FOR EACH ROW
    BEGIN
        SELECT CASE
            WHEN NEW.cursor_value < OLD.cursor_value THEN
                RAISE(ABORT, 'cursor_value regression rejected')
        END;
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_sweep_scope_immutable
    BEFORE UPDATE ON cron_sweep_cursors
    FOR EACH ROW
    WHEN NEW.scope_key IS NOT OLD.scope_key
      OR NEW.cron_id IS NOT OLD.cron_id
      OR NEW.registry_generation IS NOT OLD.registry_generation
      OR NEW.command_digest IS NOT OLD.command_digest
      OR NEW.catch_up_policy_version IS NOT OLD.catch_up_policy_version
    BEGIN
        SELECT RAISE(ABORT, 'cursor scope is immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_attempt_transition_guard
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN NEW.state != OLD.state AND NOT (
        (OLD.state = 'admitted' AND NEW.state IN ('claimed', 'terminal'))
        OR (OLD.state = 'claimed' AND NEW.state IN ('running', 'reconciling', 'terminal'))
        OR (OLD.state = 'running' AND NEW.state IN ('reconciling', 'terminal'))
        OR (OLD.state = 'reconciling' AND NEW.state = 'terminal')
    )
    BEGIN
        SELECT RAISE(ABORT, 'illegal attempt state transition');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_attempt_fence_guard
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN OLD.fence_token IS NOT NULL AND (
        NEW.fence_token IS NULL
        OR NEW.fence_token < OLD.fence_token
        OR (NEW.runner_id IS OLD.runner_id AND NEW.fence_token != OLD.fence_token)
        OR (NEW.runner_id IS NOT OLD.runner_id AND NEW.fence_token <= OLD.fence_token)
    )
    BEGIN
        SELECT RAISE(ABORT, 'attempt fence regression or invalid renewal');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_terminal_attempt_immutable
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN OLD.state = 'terminal'
    BEGIN
        SELECT RAISE(ABORT, 'terminal attempts are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_receipt_identity_guard
    BEFORE INSERT ON cron_receipts
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1
        FROM cron_execution_attempts AS attempt
        JOIN cron_execution_aggregates AS aggregate
          ON aggregate.execution_id = attempt.execution_id
        WHERE attempt.execution_id = NEW.execution_id
          AND attempt.attempt = NEW.attempt
          AND aggregate.cron_id = NEW.cron_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'receipt identity or terminal attempt mismatch');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_sweep_cursors_no_delete
    BEFORE DELETE ON cron_sweep_cursors
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cursor rows cannot be deleted');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_attempt_insert_guard
    BEFORE INSERT ON cron_execution_attempts
    FOR EACH ROW
    WHEN NEW.state = 'terminal'
      OR NEW.attempt != COALESCE(
          (SELECT MAX(attempt) + 1
           FROM cron_execution_attempts
           WHERE execution_id = NEW.execution_id),
          1
      )
    BEGIN
        SELECT RAISE(ABORT, 'attempts must start at 1, increment by 1, and cannot insert terminal');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_attempts_no_delete
    BEFORE DELETE ON cron_execution_attempts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'attempt rows cannot be deleted');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_terminal_requires_receipt
    BEFORE UPDATE OF state ON cron_execution_attempts
    FOR EACH ROW
    WHEN NEW.state = 'terminal'
      AND OLD.state != 'terminal'
      AND NOT EXISTS (
          SELECT 1 FROM cron_receipts
          WHERE execution_id = NEW.execution_id AND attempt = NEW.attempt
      )
    BEGIN
        SELECT RAISE(ABORT, 'terminal transition requires matching receipt');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_receipt_finalizes_attempt
    AFTER INSERT ON cron_receipts
    FOR EACH ROW
    BEGIN
        UPDATE cron_execution_attempts
        SET state = 'terminal', updated_at = NEW.created_at
        WHERE execution_id = NEW.execution_id AND attempt = NEW.attempt;
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_trigger_deliveries_insert_collision
    BEFORE INSERT ON cron_trigger_deliveries
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_trigger_deliveries WHERE trigger_event_id = NEW.trigger_event_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows cannot be replaced');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_trigger_deliveries_no_update
    BEFORE UPDATE ON cron_trigger_deliveries
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_trigger_deliveries_no_delete
    BEFORE DELETE ON cron_trigger_deliveries
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_registry_snapshots_insert_collision
    BEFORE INSERT ON cron_registry_snapshots_v1
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_registry_snapshots_v1
        WHERE (source_id = NEW.source_id AND registry_generation = NEW.registry_generation AND (snapshot_digest != NEW.snapshot_digest OR canonical_bytes != NEW.canonical_bytes))
           OR (source_id = NEW.source_id AND snapshot_digest = NEW.snapshot_digest AND (registry_generation != NEW.registry_generation OR canonical_bytes != NEW.canonical_bytes))
    )
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 duplicate insertion conflict');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_registry_snapshots_no_update
    BEFORE UPDATE ON cron_registry_snapshots_v1
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 rows are immutable');
    END;
    """,
        """
    CREATE TRIGGER IF NOT EXISTS main.trg_cron_registry_snapshots_no_delete
    BEFORE DELETE ON cron_registry_snapshots_v1
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 rows cannot be deleted');
    END;
    """,
    )
    assert hashlib.sha256("\0".join(frozen_v2_ddl).encode()).hexdigest() == (
        "5f33be838c133be02e3a7b6165f650dd451062c78ae3713a3fd5c857a1d46765"
    )

    conn = connect_cron_authority(db_path)

    # 1. Create tables and triggers directly from test-local frozen v2 DDL.
    for ddl in frozen_v2_ddl:
        conn.execute(ddl)

    conn.execute(
        "CREATE TABLE main.unrelated_caller_table (id INTEGER PRIMARY KEY, caller_data TEXT);"
    )

    # 2. Populate rows
    conn.execute(
        "INSERT INTO main.cron_authority_schema_version VALUES (1, 2, '2026-07-28T00:00:00Z');"
    )

    agg1 = (
        "exec-v2-001",
        "cron.job-a",
        1,
        "2026-07-28T04:00:00Z",
        "a" * 64,
        "b" * 64,
        "2026-07-28T04:00:00Z",
    )
    agg2 = (
        "exec-v2-002",
        "cron.job-b",
        1,
        "2026-07-28T05:00:00Z",
        "c" * 64,
        "d" * 64,
        "2026-07-28T05:00:00Z",
    )
    conn.execute(
        "INSERT INTO main.cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);", agg1
    )
    conn.execute(
        "INSERT INTO main.cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?, ?);", agg2
    )

    att1_1 = (
        "exec-v2-001",
        1,
        "claimed",
        "runner-node-01",
        1,
        "2026-07-28T04:10:00Z",
        "2026-07-28T04:00:00Z",
        "2026-07-28T04:00:00Z",
    )
    att1_2 = (
        "exec-v2-001",
        2,
        "running",
        "runner-node-01",
        2,
        "2026-07-28T04:20:00Z",
        "2026-07-28T04:10:00Z",
        "2026-07-28T04:15:00Z",
    )
    att2_1 = (
        "exec-v2-002",
        1,
        "admitted",
        None,
        None,
        None,
        "2026-07-28T05:00:00Z",
        "2026-07-28T05:00:00Z",
    )
    conn.execute(
        "INSERT INTO main.cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        att1_1,
    )
    conn.execute(
        "INSERT INTO main.cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        att1_2,
    )
    conn.execute(
        "INSERT INTO main.cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        att2_1,
    )

    ev_bytes_1 = b'{"status":"succeeded","log":"ok"}'
    ev_digest_1 = hashlib.sha256(ev_bytes_1).hexdigest()
    ev1 = (ev_digest_1, ev_bytes_1, "2026-07-28T04:05:00Z")
    conn.execute("INSERT INTO main.cron_evidence VALUES (?, ?, ?);", ev1)

    rcpt1 = (
        "rcpt-v2-001",
        "exec-v2-001",
        1,
        "cron.job-a",
        "succeeded",
        "runner-node-01",
        "f" * 64,
        "2026-07-28T04:00:00Z",
        "2026-07-28T04:05:00Z",
        None,
        ev_digest_1,
        "key-v1",
        "sig-test-v2",
        1,
        "2026-07-28T04:05:00Z",
    )
    conn.execute(
        "INSERT INTO main.cron_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        rcpt1,
    )

    cur1 = (
        "scope-job-a-gen-1",
        "cron.job-a",
        1,
        "a" * 64,
        1,
        42,
        "2026-07-28T04:00:00Z",
    )
    conn.execute(
        "INSERT INTO main.cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);", cur1
    )

    trig_bytes = b'{"trigger":"scheduled"}'
    trig_digest = hashlib.sha256(trig_bytes).hexdigest()
    trig1 = (
        "trig-evt-001",
        trig_digest,
        trig_bytes,
        "scheduled",
        "http",
        "cron.job-a",
        1,
        "2026-07-28T04:00:00Z",
        "a" * 64,
        "b" * 64,
        "exec-v2-001",
        "accepted",
        "reason-ok",
        "2026-07-28T04:00:00Z",
        "2026-07-28T04:00:00Z",
    )
    conn.execute(
        "INSERT INTO main.cron_trigger_deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
        trig1,
    )

    snap_bytes = b'{"cron_id":"cron.job-a","generation":1}'
    snap_digest = hashlib.sha256(
        b"prismatic.cron.registry-snapshot.v1\x00" + snap_bytes
    ).hexdigest()
    snap1 = (
        "prismatic.cron-authority.sqlite/cron_registry_snapshots_v1",
        "prismatic.cron.registry-snapshot",
        1,
        1,
        snap_digest,
        snap_bytes,
        "2026-07-28T04:00:00Z",
    )
    conn.execute(
        "INSERT INTO main.cron_registry_snapshots_v1 VALUES (?, ?, ?, ?, ?, ?, ?);",
        snap1,
    )

    conn.execute(
        "INSERT INTO main.unrelated_caller_table VALUES (999, 'unrelated_data_v2');"
    )

    conn.commit()

    assert conn.execute("PRAGMA foreign_key_check;").fetchall() == []
    assert conn.execute("PRAGMA integrity_check;").fetchall() == [("ok",)]
    assert conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)

    tables = [
        "cron_authority_schema_version",
        "cron_execution_aggregates",
        "cron_evidence",
        "cron_execution_attempts",
        "cron_receipts",
        "cron_sweep_cursors",
        "cron_trigger_deliveries",
        "cron_registry_snapshots_v1",
        "unrelated_caller_table",
    ]
    projections = {}
    for tbl in tables:
        projections[tbl] = conn.execute(
            f"SELECT * FROM main.{tbl} ORDER BY 1 ASC;"
        ).fetchall()

    schema_sql = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM main.sqlite_master
        WHERE (tbl_name LIKE 'cron\\_%' ESCAPE '\\'
               OR name LIKE 'cron\\_%' ESCAPE '\\')
        ORDER BY type ASC, name ASC;
        """
    ).fetchall()
    index_names = {
        table: tuple(
            row[1]
            for row in conn.execute(f"PRAGMA main.index_list({table});").fetchall()
        )
        for table in tables[:-1]
    }
    trigger_names = tuple(
        row[0]
        for row in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='trigger' "
            "AND tbl_name LIKE 'cron\\_%' ESCAPE '\\' ORDER BY name ASC;"
        ).fetchall()
    )
    foreign_keys = {
        table: conn.execute(f"PRAGMA main.foreign_key_list({table});").fetchall()
        for table in tables[:-1]
    }
    relationships = {
        "receipt_chain": conn.execute(
            """
            SELECT r.receipt_id, r.execution_id, r.attempt, a.state,
                   a.execution_id, g.execution_id, r.evidence_digest, e.evidence_digest
            FROM main.cron_receipts AS r
            JOIN main.cron_execution_attempts AS a
              ON a.execution_id = r.execution_id AND a.attempt = r.attempt
            JOIN main.cron_execution_aggregates AS g
              ON g.execution_id = a.execution_id
            JOIN main.cron_evidence AS e
              ON e.evidence_digest = r.evidence_digest
            ORDER BY r.receipt_id ASC;
            """
        ).fetchall(),
        "trigger_delivery_chain": conn.execute(
            """
            SELECT d.trigger_event_id, d.execution_id, g.execution_id
            FROM main.cron_trigger_deliveries AS d
            JOIN main.cron_execution_aggregates AS g
              ON g.execution_id = d.execution_id
            ORDER BY d.trigger_event_id ASC;
            """
        ).fetchall(),
    }

    conn.close()
    return {
        "tables": tuple(tables),
        "projections": projections,
        "schema_sql": schema_sql,
        "index_names": index_names,
        "trigger_names": trigger_names,
        "foreign_keys": foreign_keys,
        "relationships": relationships,
        "ev_digest_1": ev_digest_1,
        "trig_digest": trig_digest,
        "snap_digest": snap_digest,
    }


_V3_ATTEMPT_COLUMNS = (
    "execution_id",
    "attempt",
    "state",
    "runner_id",
    "fence_token",
    "lease_expires_at",
    "source_id",
    "schema_id",
    "schema_version",
    "registry_generation",
    "snapshot_digest",
    "canonical_snapshot_bytes",
    "trusted_runner_identity",
    "command_digest",
    "release_digest",
    "dependency_digest",
    "created_at",
    "updated_at",
)
_V3_TABLE_NAMES = {
    "cron_authority_schema_version",
    "cron_evidence",
    "cron_execution_aggregates",
    "cron_execution_attempts",
    "cron_receipts",
    "cron_registry_snapshots_v1",
    "cron_registry_snapshots_v2",
    "cron_sweep_cursors",
    "cron_trigger_deliveries",
}
_V3_TRIGGER_NAMES = {
    "trg_cron_aggregate_insert_collision",
    "trg_cron_aggregates_no_delete",
    "trg_cron_aggregates_no_update",
    "trg_cron_attempt_fence_guard",
    "trg_cron_attempt_insert_guard",
    "trg_cron_attempt_transition_guard",
    "trg_cron_attempts_no_delete",
    "trg_cron_cursor_insert_collision",
    "trg_cron_evidence_insert_collision",
    "trg_cron_evidence_no_delete",
    "trg_cron_evidence_no_update",
    "trg_cron_receipt_finalizes_attempt",
    "trg_cron_receipt_identity_guard",
    "trg_cron_receipt_insert_collision",
    "trg_cron_receipts_no_delete",
    "trg_cron_receipts_no_update",
    "trg_cron_registry_snapshots_insert_collision",
    "trg_cron_registry_snapshots_no_delete",
    "trg_cron_registry_snapshots_no_update",
    "trg_cron_registry_snapshots_v2_insert_collision",
    "trg_cron_registry_snapshots_v2_no_delete",
    "trg_cron_registry_snapshots_v2_no_update",
    "trg_cron_sweep_cursors_no_delete",
    "trg_cron_sweep_cursors_prevent_regression",
    "trg_cron_sweep_scope_immutable",
    "trg_cron_terminal_attempt_immutable",
    "trg_cron_terminal_requires_receipt",
    "trg_cron_trigger_deliveries_insert_collision",
    "trg_cron_trigger_deliveries_no_delete",
    "trg_cron_trigger_deliveries_no_update",
}
_V3_SNAPSHOT_V2_INDEXES = (
    "sqlite_autoindex_cron_registry_snapshots_v2_2",
    "sqlite_autoindex_cron_registry_snapshots_v2_1",
)


_FROZEN_V3_SCHEMA_SQL = (
    ("index", "sqlite_autoindex_cron_evidence_1", "cron_evidence", None),
    (
        "index",
        "sqlite_autoindex_cron_execution_aggregates_1",
        "cron_execution_aggregates",
        None,
    ),
    (
        "index",
        "sqlite_autoindex_cron_execution_aggregates_2",
        "cron_execution_aggregates",
        None,
    ),
    (
        "index",
        "sqlite_autoindex_cron_execution_attempts_1",
        "cron_execution_attempts",
        None,
    ),
    ("index", "sqlite_autoindex_cron_receipts_1", "cron_receipts", None),
    ("index", "sqlite_autoindex_cron_receipts_2", "cron_receipts", None),
    (
        "index",
        "sqlite_autoindex_cron_registry_snapshots_v1_1",
        "cron_registry_snapshots_v1",
        None,
    ),
    (
        "index",
        "sqlite_autoindex_cron_registry_snapshots_v1_2",
        "cron_registry_snapshots_v1",
        None,
    ),
    (
        "index",
        "sqlite_autoindex_cron_registry_snapshots_v2_1",
        "cron_registry_snapshots_v2",
        None,
    ),
    (
        "index",
        "sqlite_autoindex_cron_registry_snapshots_v2_2",
        "cron_registry_snapshots_v2",
        None,
    ),
    ("index", "sqlite_autoindex_cron_sweep_cursors_1", "cron_sweep_cursors", None),
    ("index", "sqlite_autoindex_cron_sweep_cursors_2", "cron_sweep_cursors", None),
    (
        "index",
        "sqlite_autoindex_cron_trigger_deliveries_1",
        "cron_trigger_deliveries",
        None,
    ),
    (
        "table",
        "cron_authority_schema_version",
        "cron_authority_schema_version",
        "CREATE TABLE cron_authority_schema_version (\n"
        "    authority_id INTEGER PRIMARY KEY CHECK (authority_id = 1),\n"
        "    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND "
        "schema_version >= 1),\n"
        "    installed_at TEXT NOT NULL CHECK (is_utc_timestamp(installed_at) = 1)\n"
        ")",
    ),
    (
        "table",
        "cron_evidence",
        "cron_evidence",
        "CREATE TABLE cron_evidence (\n"
        "    evidence_digest TEXT PRIMARY KEY CHECK (length(evidence_digest) = 64 AND evidence_digest "
        "NOT GLOB '*[^0-9a-f]*'),\n"
        "    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND "
        "length(canonical_bytes) <= 4000),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    CONSTRAINT ck_evidence_content_address CHECK (evidence_digest = "
        "sha256_hex(canonical_bytes))\n"
        ")",
    ),
    (
        "table",
        "cron_execution_aggregates",
        "cron_execution_aggregates",
        "CREATE TABLE cron_execution_aggregates (\n"
        "    execution_id TEXT PRIMARY KEY CHECK (length(execution_id) >= 1 AND length(execution_id) <= "
        "128),\n"
        "    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),\n"
        "    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND "
        "registry_generation >= 1),\n"
        "    schedule_bucket TEXT NOT NULL CHECK (is_utc_timestamp(schedule_bucket) = 1),\n"
        "    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB "
        "'*[^0-9a-f]*'),\n"
        "    release_digest TEXT NOT NULL CHECK (length(release_digest) = 64 AND release_digest NOT GLOB "
        "'*[^0-9a-f]*'),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    CONSTRAINT uq_cron_aggregate UNIQUE (cron_id, registry_generation, schedule_bucket, "
        "command_digest)\n"
        ")",
    ),
    (
        "table",
        "cron_execution_attempts",
        "cron_execution_attempts",
        "CREATE TABLE cron_execution_attempts (\n"
        "    execution_id TEXT NOT NULL REFERENCES cron_execution_aggregates(execution_id) ON DELETE "
        "RESTRICT,\n"
        "    attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),\n"
        "    state TEXT NOT NULL CHECK (state IN ('admitted', 'claimed', 'running', 'reconciling', "
        "'terminal')),\n"
        "    runner_id TEXT CHECK (runner_id IS NULL OR (length(runner_id) >= 1 AND length(runner_id) <= "
        "128)),\n"
        "    fence_token INTEGER CHECK (fence_token IS NULL OR (typeof(fence_token) = 'integer' AND "
        "fence_token > 0)),\n"
        "    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR is_utc_timestamp(lease_expires_at) "
        "= 1),\n"
        "    source_id TEXT CHECK (source_id IS NULL OR source_id IN "
        "('prismatic.cron-authority.sqlite/cron_registry_snapshots_v1', "
        "'prismatic.cron-authority.sqlite/cron_registry_snapshots_v2')),\n"
        "    schema_id TEXT CHECK (schema_id IS NULL OR schema_id = "
        "'prismatic.cron.registry-snapshot'),\n"
        "    schema_version INTEGER CHECK (schema_version IS NULL OR schema_version IN (1, 2)),\n"
        "    registry_generation INTEGER CHECK (registry_generation IS NULL OR registry_generation >= "
        "1),\n"
        "    snapshot_digest TEXT CHECK (snapshot_digest IS NULL OR (length(snapshot_digest) = 64 AND "
        "snapshot_digest NOT GLOB '*[^0-9a-f]*')),\n"
        "    canonical_snapshot_bytes BLOB CHECK (canonical_snapshot_bytes IS NULL OR "
        "typeof(canonical_snapshot_bytes) = 'blob'),\n"
        "    trusted_runner_identity TEXT CHECK (trusted_runner_identity IS NULL OR "
        "length(trusted_runner_identity) >= 1),\n"
        "    command_digest TEXT CHECK (command_digest IS NULL OR (length(command_digest) = 64 AND "
        "command_digest NOT GLOB '*[^0-9a-f]*')),\n"
        "    release_digest TEXT CHECK (release_digest IS NULL OR (length(release_digest) = 64 AND "
        "release_digest NOT GLOB '*[^0-9a-f]*')),\n"
        "    dependency_digest TEXT CHECK (dependency_digest IS NULL OR (length(dependency_digest) = 64 "
        "AND dependency_digest NOT GLOB '*[^0-9a-f]*')),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),\n"
        "    CONSTRAINT ck_attempt_ownership CHECK (\n"
        "        state IN ('admitted', 'terminal')\n"
        "        OR (runner_id IS NOT NULL AND fence_token IS NOT NULL AND lease_expires_at IS NOT "
        "NULL)\n"
        "    ),\n"
        "    CONSTRAINT ck_attempt_coherent_snapshot CHECK (\n"
        "        (source_id IS NULL AND schema_version IS NULL AND snapshot_digest IS NULL AND "
        "canonical_snapshot_bytes IS NULL AND registry_generation IS NULL AND schema_id IS NULL)\n"
        "        OR (source_id = 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v1' AND "
        "schema_version = 1 AND schema_id = 'prismatic.cron.registry-snapshot' AND snapshot_digest IS "
        "NOT NULL AND canonical_snapshot_bytes IS NOT NULL AND registry_generation IS NOT NULL)\n"
        "        OR (source_id = 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v2' AND "
        "schema_version = 2 AND schema_id = 'prismatic.cron.registry-snapshot' AND snapshot_digest IS "
        "NOT NULL AND canonical_snapshot_bytes IS NOT NULL AND registry_generation IS NOT NULL)\n"
        "    ),\n"
        "    PRIMARY KEY (execution_id, attempt)\n"
        ")",
    ),
    (
        "table",
        "cron_receipts",
        "cron_receipts",
        "CREATE TABLE cron_receipts (\n"
        "    receipt_id TEXT PRIMARY KEY CHECK (length(receipt_id) >= 1 AND length(receipt_id) <= 128),\n"
        "    execution_id TEXT NOT NULL,\n"
        "    attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),\n"
        "    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),\n"
        "    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'timed_out', 'cancelled', "
        "'blocked', 'missed_during_offline', 'awaiting_operator_approval', 'orphaned', 'reconciled')),\n"
        "    runner_id TEXT NOT NULL CHECK (length(runner_id) >= 1 AND length(runner_id) <= 128),\n"
        "    runner_release_digest TEXT NOT NULL CHECK (length(runner_release_digest) = 64 AND "
        "runner_release_digest NOT GLOB '*[^0-9a-f]*'),\n"
        "    started_at TEXT NOT NULL CHECK (is_utc_timestamp(started_at) = 1),\n"
        "    finished_at TEXT NOT NULL CHECK (is_utc_timestamp(finished_at) = 1),\n"
        "    error_classification TEXT CHECK (error_classification IS NULL OR "
        "length(error_classification) <= 128),\n"
        "    evidence_digest TEXT REFERENCES cron_evidence(evidence_digest) ON DELETE RESTRICT CHECK "
        "(evidence_digest IS NULL OR (length(evidence_digest) = 64 AND evidence_digest NOT GLOB "
        "'*[^0-9a-f]*')),\n"
        "    signing_key_id TEXT NOT NULL CHECK (length(signing_key_id) >= 1 AND length(signing_key_id) "
        "<= 128),\n"
        "    signature TEXT NOT NULL CHECK (length(signature) >= 1 AND length(signature) <= 512),\n"
        "    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND "
        "schema_version = 1),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    CONSTRAINT ck_required_outcome_evidence CHECK (\n"
        "        outcome NOT IN ('failed', 'timed_out', 'blocked', 'missed_during_offline', "
        "'awaiting_operator_approval', 'orphaned', 'reconciled')\n"
        "        OR evidence_digest IS NOT NULL\n"
        "    ),\n"
        "    CONSTRAINT uq_receipt_execution_attempt UNIQUE (execution_id, attempt),\n"
        "    CONSTRAINT fk_receipt_execution_attempt FOREIGN KEY (execution_id, attempt) REFERENCES "
        "cron_execution_attempts(execution_id, attempt) ON DELETE RESTRICT\n"
        ")",
    ),
    (
        "table",
        "cron_registry_snapshots_v1",
        "cron_registry_snapshots_v1",
        "CREATE TABLE cron_registry_snapshots_v1 (\n"
        "    source_id TEXT NOT NULL CHECK (source_id = "
        "'prismatic.cron-authority.sqlite/cron_registry_snapshots_v1'),\n"
        "    schema_id TEXT NOT NULL CHECK (schema_id = 'prismatic.cron.registry-snapshot'),\n"
        "    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND "
        "schema_version = 1),\n"
        "    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND "
        "registry_generation >= 1),\n"
        "    snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64 AND snapshot_digest NOT "
        "GLOB '*[^0-9a-f]*'),\n"
        "    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND "
        "length(canonical_bytes) <= 1048576),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    PRIMARY KEY (source_id, registry_generation),\n"
        "    CONSTRAINT uq_snapshot_digest UNIQUE (source_id, snapshot_digest)\n"
        ")",
    ),
    (
        "table",
        "cron_registry_snapshots_v2",
        "cron_registry_snapshots_v2",
        "CREATE TABLE cron_registry_snapshots_v2 (\n"
        "    source_id TEXT NOT NULL CHECK (source_id = "
        "'prismatic.cron-authority.sqlite/cron_registry_snapshots_v2'),\n"
        "    schema_id TEXT NOT NULL CHECK (schema_id = 'prismatic.cron.registry-snapshot'),\n"
        "    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND "
        "schema_version = 2),\n"
        "    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND "
        "registry_generation >= 1),\n"
        "    snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64 AND snapshot_digest NOT "
        "GLOB '*[^0-9a-f]*'),\n"
        "    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND "
        "length(canonical_bytes) <= 1048576),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    PRIMARY KEY (source_id, registry_generation),\n"
        "    CONSTRAINT uq_snapshot_v2_digest UNIQUE (source_id, snapshot_digest)\n"
        ")",
    ),
    (
        "table",
        "cron_sweep_cursors",
        "cron_sweep_cursors",
        "CREATE TABLE cron_sweep_cursors (\n"
        "    scope_key TEXT PRIMARY KEY CHECK (length(scope_key) >= 1 AND length(scope_key) <= 256),\n"
        "    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),\n"
        "    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND "
        "registry_generation >= 1),\n"
        "    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB "
        "'*[^0-9a-f]*'),\n"
        "    catch_up_policy_version INTEGER NOT NULL CHECK (typeof(catch_up_policy_version) = 'integer' "
        "AND catch_up_policy_version >= 1),\n"
        "    cursor_value INTEGER NOT NULL CHECK (typeof(cursor_value) = 'integer' AND cursor_value >= "
        "0),\n"
        "    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),\n"
        "    CONSTRAINT uq_cron_sweep_scope UNIQUE (cron_id, registry_generation, command_digest, "
        "catch_up_policy_version)\n"
        ")",
    ),
    (
        "table",
        "cron_trigger_deliveries",
        "cron_trigger_deliveries",
        "CREATE TABLE cron_trigger_deliveries (\n"
        "    trigger_event_id TEXT PRIMARY KEY CHECK (length(trigger_event_id) >= 1 AND "
        "length(trigger_event_id) <= 128),\n"
        "    trigger_digest TEXT NOT NULL CHECK (length(trigger_digest) = 64 AND trigger_digest NOT GLOB "
        "'*[^0-9a-f]*'),\n"
        "    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND "
        "length(canonical_bytes) <= 4000),\n"
        "    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('scheduled', 'manual', 'retry', 'hook', "
        "'recovery')),\n"
        "    transport_kind TEXT NOT NULL CHECK (transport_kind IN ('http', 'hook', 'recovery', "
        "'internal')),\n"
        "    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),\n"
        "    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND "
        "registry_generation >= 1),\n"
        "    schedule_bucket TEXT NOT NULL CHECK (is_utc_timestamp(schedule_bucket) = 1),\n"
        "    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB "
        "'*[^0-9a-f]*'),\n"
        "    release_digest TEXT NOT NULL CHECK (length(release_digest) = 64 AND release_digest NOT GLOB "
        "'*[^0-9a-f]*'),\n"
        "    execution_id TEXT REFERENCES cron_execution_aggregates(execution_id) ON DELETE RESTRICT,\n"
        "    disposition TEXT NOT NULL CHECK (disposition IN ('accepted', 'rejected', 'converged')),\n"
        "    reason_code TEXT NOT NULL CHECK (length(reason_code) >= 1 AND length(reason_code) <= 128),\n"
        "    submitted_at TEXT NOT NULL CHECK (is_utc_timestamp(submitted_at) = 1),\n"
        "    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),\n"
        "    CONSTRAINT ck_trigger_delivery_content_address CHECK (trigger_digest = "
        "sha256_hex(canonical_bytes))\n"
        ")",
    ),
    (
        "trigger",
        "trg_cron_aggregate_insert_collision",
        "cron_execution_aggregates",
        "CREATE TRIGGER trg_cron_aggregate_insert_collision\n"
        "    BEFORE INSERT ON cron_execution_aggregates\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_execution_aggregates\n"
        "        WHERE execution_id = NEW.execution_id\n"
        "           OR (\n"
        "               cron_id = NEW.cron_id\n"
        "               AND registry_generation = NEW.registry_generation\n"
        "               AND schedule_bucket = NEW.schedule_bucket\n"
        "               AND command_digest = NEW.command_digest\n"
        "           )\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron execution aggregates cannot be replaced');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_aggregates_no_delete",
        "cron_execution_aggregates",
        "CREATE TRIGGER trg_cron_aggregates_no_delete\n"
        "    BEFORE DELETE ON cron_execution_aggregates\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron execution aggregates are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_aggregates_no_update",
        "cron_execution_aggregates",
        "CREATE TRIGGER trg_cron_aggregates_no_update\n"
        "    BEFORE UPDATE ON cron_execution_aggregates\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron execution aggregates are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_attempt_fence_guard",
        "cron_execution_attempts",
        "CREATE TRIGGER trg_cron_attempt_fence_guard\n"
        "    BEFORE UPDATE ON cron_execution_attempts\n"
        "    FOR EACH ROW\n"
        "    WHEN OLD.fence_token IS NOT NULL AND (\n"
        "        NEW.fence_token IS NULL\n"
        "        OR NEW.fence_token < OLD.fence_token\n"
        "        OR (NEW.runner_id IS OLD.runner_id AND NEW.fence_token != OLD.fence_token)\n"
        "        OR (NEW.runner_id IS NOT OLD.runner_id AND NEW.fence_token <= OLD.fence_token)\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'attempt fence regression or invalid renewal');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_attempt_insert_guard",
        "cron_execution_attempts",
        "CREATE TRIGGER trg_cron_attempt_insert_guard\n"
        "    BEFORE INSERT ON cron_execution_attempts\n"
        "    FOR EACH ROW\n"
        "    WHEN NEW.state = 'terminal'\n"
        "      OR NEW.attempt != COALESCE(\n"
        "          (SELECT MAX(attempt) + 1\n"
        "           FROM cron_execution_attempts\n"
        "           WHERE execution_id = NEW.execution_id),\n"
        "          1\n"
        "      )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'attempts must start at 1, increment by 1, and cannot insert "
        "terminal');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_attempt_transition_guard",
        "cron_execution_attempts",
        "CREATE TRIGGER trg_cron_attempt_transition_guard\n"
        "    BEFORE UPDATE ON cron_execution_attempts\n"
        "    FOR EACH ROW\n"
        "    WHEN NEW.state != OLD.state AND NOT (\n"
        "        (OLD.state = 'admitted' AND NEW.state IN ('claimed', 'terminal'))\n"
        "        OR (OLD.state = 'claimed' AND NEW.state IN ('running', 'reconciling', 'terminal'))\n"
        "        OR (OLD.state = 'running' AND NEW.state IN ('reconciling', 'terminal'))\n"
        "        OR (OLD.state = 'reconciling' AND NEW.state = 'terminal')\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'illegal attempt state transition');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_attempts_no_delete",
        "cron_execution_attempts",
        "CREATE TRIGGER trg_cron_attempts_no_delete\n"
        "    BEFORE DELETE ON cron_execution_attempts\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'attempt rows cannot be deleted');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_cursor_insert_collision",
        "cron_sweep_cursors",
        "CREATE TRIGGER trg_cron_cursor_insert_collision\n"
        "    BEFORE INSERT ON cron_sweep_cursors\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_sweep_cursors\n"
        "        WHERE scope_key = NEW.scope_key\n"
        "           OR (\n"
        "               cron_id = NEW.cron_id\n"
        "               AND registry_generation = NEW.registry_generation\n"
        "               AND command_digest = NEW.command_digest\n"
        "               AND catch_up_policy_version = NEW.catch_up_policy_version\n"
        "           )\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cursor rows cannot be replaced');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_evidence_insert_collision",
        "cron_evidence",
        "CREATE TRIGGER trg_cron_evidence_insert_collision\n"
        "    BEFORE INSERT ON cron_evidence\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_evidence WHERE evidence_digest = NEW.evidence_digest\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_evidence rows cannot be replaced');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_evidence_no_delete",
        "cron_evidence",
        "CREATE TRIGGER trg_cron_evidence_no_delete\n"
        "    BEFORE DELETE ON cron_evidence\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_evidence rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_evidence_no_update",
        "cron_evidence",
        "CREATE TRIGGER trg_cron_evidence_no_update\n"
        "    BEFORE UPDATE ON cron_evidence\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_evidence rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_receipt_finalizes_attempt",
        "cron_receipts",
        "CREATE TRIGGER trg_cron_receipt_finalizes_attempt\n"
        "    AFTER INSERT ON cron_receipts\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        UPDATE cron_execution_attempts\n"
        "        SET state = 'terminal', updated_at = NEW.created_at\n"
        "        WHERE execution_id = NEW.execution_id AND attempt = NEW.attempt;\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_receipt_identity_guard",
        "cron_receipts",
        "CREATE TRIGGER trg_cron_receipt_identity_guard\n"
        "    BEFORE INSERT ON cron_receipts\n"
        "    FOR EACH ROW\n"
        "    WHEN NOT EXISTS (\n"
        "        SELECT 1\n"
        "        FROM cron_execution_attempts AS attempt\n"
        "        JOIN cron_execution_aggregates AS aggregate\n"
        "          ON aggregate.execution_id = attempt.execution_id\n"
        "        WHERE attempt.execution_id = NEW.execution_id\n"
        "          AND attempt.attempt = NEW.attempt\n"
        "          AND aggregate.cron_id = NEW.cron_id\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'receipt identity or terminal attempt mismatch');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_receipt_insert_collision",
        "cron_receipts",
        "CREATE TRIGGER trg_cron_receipt_insert_collision\n"
        "    BEFORE INSERT ON cron_receipts\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_receipts\n"
        "        WHERE receipt_id = NEW.receipt_id\n"
        "           OR (execution_id = NEW.execution_id AND attempt = NEW.attempt)\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_receipts rows cannot be replaced');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_receipts_no_delete",
        "cron_receipts",
        "CREATE TRIGGER trg_cron_receipts_no_delete\n"
        "    BEFORE DELETE ON cron_receipts\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_receipts rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_receipts_no_update",
        "cron_receipts",
        "CREATE TRIGGER trg_cron_receipts_no_update\n"
        "    BEFORE UPDATE ON cron_receipts\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_receipts rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_registry_snapshots_insert_collision",
        "cron_registry_snapshots_v1",
        "CREATE TRIGGER trg_cron_registry_snapshots_insert_collision\n"
        "    BEFORE INSERT ON cron_registry_snapshots_v1\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_registry_snapshots_v1\n"
        "        WHERE (source_id = NEW.source_id AND registry_generation = NEW.registry_generation AND "
        "(snapshot_digest != NEW.snapshot_digest OR canonical_bytes != NEW.canonical_bytes))\n"
        "           OR (source_id = NEW.source_id AND snapshot_digest = NEW.snapshot_digest AND "
        "(registry_generation != NEW.registry_generation OR canonical_bytes != NEW.canonical_bytes))\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 duplicate insertion conflict');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_registry_snapshots_no_delete",
        "cron_registry_snapshots_v1",
        "CREATE TRIGGER trg_cron_registry_snapshots_no_delete\n"
        "    BEFORE DELETE ON cron_registry_snapshots_v1\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 rows cannot be deleted');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_registry_snapshots_no_update",
        "cron_registry_snapshots_v1",
        "CREATE TRIGGER trg_cron_registry_snapshots_no_update\n"
        "    BEFORE UPDATE ON cron_registry_snapshots_v1\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_registry_snapshots_v2_insert_collision",
        "cron_registry_snapshots_v2",
        "CREATE TRIGGER trg_cron_registry_snapshots_v2_insert_collision\n"
        "    BEFORE INSERT ON cron_registry_snapshots_v2\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_registry_snapshots_v2\n"
        "        WHERE (source_id = NEW.source_id AND registry_generation = NEW.registry_generation AND "
        "(snapshot_digest != NEW.snapshot_digest OR canonical_bytes != NEW.canonical_bytes))\n"
        "           OR (source_id = NEW.source_id AND snapshot_digest = NEW.snapshot_digest AND "
        "(registry_generation != NEW.registry_generation OR canonical_bytes != NEW.canonical_bytes))\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_registry_snapshots_v2 duplicate insertion conflict');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_registry_snapshots_v2_no_delete",
        "cron_registry_snapshots_v2",
        "CREATE TRIGGER trg_cron_registry_snapshots_v2_no_delete\n"
        "    BEFORE DELETE ON cron_registry_snapshots_v2\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_registry_snapshots_v2 rows cannot be deleted');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_registry_snapshots_v2_no_update",
        "cron_registry_snapshots_v2",
        "CREATE TRIGGER trg_cron_registry_snapshots_v2_no_update\n"
        "    BEFORE UPDATE ON cron_registry_snapshots_v2\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_registry_snapshots_v2 rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_sweep_cursors_no_delete",
        "cron_sweep_cursors",
        "CREATE TRIGGER trg_cron_sweep_cursors_no_delete\n"
        "    BEFORE DELETE ON cron_sweep_cursors\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cursor rows cannot be deleted');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_sweep_cursors_prevent_regression",
        "cron_sweep_cursors",
        "CREATE TRIGGER trg_cron_sweep_cursors_prevent_regression\n"
        "    BEFORE UPDATE ON cron_sweep_cursors\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT CASE\n"
        "            WHEN NEW.cursor_value < OLD.cursor_value THEN\n"
        "                RAISE(ABORT, 'cursor_value regression rejected')\n"
        "        END;\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_sweep_scope_immutable",
        "cron_sweep_cursors",
        "CREATE TRIGGER trg_cron_sweep_scope_immutable\n"
        "    BEFORE UPDATE ON cron_sweep_cursors\n"
        "    FOR EACH ROW\n"
        "    WHEN NEW.scope_key IS NOT OLD.scope_key\n"
        "      OR NEW.cron_id IS NOT OLD.cron_id\n"
        "      OR NEW.registry_generation IS NOT OLD.registry_generation\n"
        "      OR NEW.command_digest IS NOT OLD.command_digest\n"
        "      OR NEW.catch_up_policy_version IS NOT OLD.catch_up_policy_version\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cursor scope is immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_terminal_attempt_immutable",
        "cron_execution_attempts",
        "CREATE TRIGGER trg_cron_terminal_attempt_immutable\n"
        "    BEFORE UPDATE ON cron_execution_attempts\n"
        "    FOR EACH ROW\n"
        "    WHEN OLD.state = 'terminal'\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'terminal attempts are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_terminal_requires_receipt",
        "cron_execution_attempts",
        "CREATE TRIGGER trg_cron_terminal_requires_receipt\n"
        "    BEFORE UPDATE OF state ON cron_execution_attempts\n"
        "    FOR EACH ROW\n"
        "    WHEN NEW.state = 'terminal'\n"
        "      AND OLD.state != 'terminal'\n"
        "      AND NOT EXISTS (\n"
        "          SELECT 1 FROM cron_receipts\n"
        "          WHERE execution_id = NEW.execution_id AND attempt = NEW.attempt\n"
        "      )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'terminal transition requires matching receipt');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_trigger_deliveries_insert_collision",
        "cron_trigger_deliveries",
        "CREATE TRIGGER trg_cron_trigger_deliveries_insert_collision\n"
        "    BEFORE INSERT ON cron_trigger_deliveries\n"
        "    FOR EACH ROW\n"
        "    WHEN EXISTS (\n"
        "        SELECT 1 FROM cron_trigger_deliveries WHERE trigger_event_id = NEW.trigger_event_id\n"
        "    )\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows cannot be replaced');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_trigger_deliveries_no_delete",
        "cron_trigger_deliveries",
        "CREATE TRIGGER trg_cron_trigger_deliveries_no_delete\n"
        "    BEFORE DELETE ON cron_trigger_deliveries\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows are immutable');\n"
        "    END",
    ),
    (
        "trigger",
        "trg_cron_trigger_deliveries_no_update",
        "cron_trigger_deliveries",
        "CREATE TRIGGER trg_cron_trigger_deliveries_no_update\n"
        "    BEFORE UPDATE ON cron_trigger_deliveries\n"
        "    FOR EACH ROW\n"
        "    BEGIN\n"
        "        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows are immutable');\n"
        "    END",
    ),
)

_FROZEN_V3_TABLE_INFO = {
    "cron_authority_schema_version": (
        (0, "authority_id", "INTEGER", 0, None, 1),
        (1, "schema_version", "INTEGER", 1, None, 0),
        (2, "installed_at", "TEXT", 1, None, 0),
    ),
    "cron_evidence": (
        (0, "evidence_digest", "TEXT", 0, None, 1),
        (1, "canonical_bytes", "BLOB", 1, None, 0),
        (2, "created_at", "TEXT", 1, None, 0),
    ),
    "cron_execution_aggregates": (
        (0, "execution_id", "TEXT", 0, None, 1),
        (1, "cron_id", "TEXT", 1, None, 0),
        (2, "registry_generation", "INTEGER", 1, None, 0),
        (3, "schedule_bucket", "TEXT", 1, None, 0),
        (4, "command_digest", "TEXT", 1, None, 0),
        (5, "release_digest", "TEXT", 1, None, 0),
        (6, "created_at", "TEXT", 1, None, 0),
    ),
    "cron_execution_attempts": (
        (0, "execution_id", "TEXT", 1, None, 1),
        (1, "attempt", "INTEGER", 1, None, 2),
        (2, "state", "TEXT", 1, None, 0),
        (3, "runner_id", "TEXT", 0, None, 0),
        (4, "fence_token", "INTEGER", 0, None, 0),
        (5, "lease_expires_at", "TEXT", 0, None, 0),
        (6, "source_id", "TEXT", 0, None, 0),
        (7, "schema_id", "TEXT", 0, None, 0),
        (8, "schema_version", "INTEGER", 0, None, 0),
        (9, "registry_generation", "INTEGER", 0, None, 0),
        (10, "snapshot_digest", "TEXT", 0, None, 0),
        (11, "canonical_snapshot_bytes", "BLOB", 0, None, 0),
        (12, "trusted_runner_identity", "TEXT", 0, None, 0),
        (13, "command_digest", "TEXT", 0, None, 0),
        (14, "release_digest", "TEXT", 0, None, 0),
        (15, "dependency_digest", "TEXT", 0, None, 0),
        (16, "created_at", "TEXT", 1, None, 0),
        (17, "updated_at", "TEXT", 1, None, 0),
    ),
    "cron_receipts": (
        (0, "receipt_id", "TEXT", 0, None, 1),
        (1, "execution_id", "TEXT", 1, None, 0),
        (2, "attempt", "INTEGER", 1, None, 0),
        (3, "cron_id", "TEXT", 1, None, 0),
        (4, "outcome", "TEXT", 1, None, 0),
        (5, "runner_id", "TEXT", 1, None, 0),
        (6, "runner_release_digest", "TEXT", 1, None, 0),
        (7, "started_at", "TEXT", 1, None, 0),
        (8, "finished_at", "TEXT", 1, None, 0),
        (9, "error_classification", "TEXT", 0, None, 0),
        (10, "evidence_digest", "TEXT", 0, None, 0),
        (11, "signing_key_id", "TEXT", 1, None, 0),
        (12, "signature", "TEXT", 1, None, 0),
        (13, "schema_version", "INTEGER", 1, None, 0),
        (14, "created_at", "TEXT", 1, None, 0),
    ),
    "cron_registry_snapshots_v1": (
        (0, "source_id", "TEXT", 1, None, 1),
        (1, "schema_id", "TEXT", 1, None, 0),
        (2, "schema_version", "INTEGER", 1, None, 0),
        (3, "registry_generation", "INTEGER", 1, None, 2),
        (4, "snapshot_digest", "TEXT", 1, None, 0),
        (5, "canonical_bytes", "BLOB", 1, None, 0),
        (6, "created_at", "TEXT", 1, None, 0),
    ),
    "cron_registry_snapshots_v2": (
        (0, "source_id", "TEXT", 1, None, 1),
        (1, "schema_id", "TEXT", 1, None, 0),
        (2, "schema_version", "INTEGER", 1, None, 0),
        (3, "registry_generation", "INTEGER", 1, None, 2),
        (4, "snapshot_digest", "TEXT", 1, None, 0),
        (5, "canonical_bytes", "BLOB", 1, None, 0),
        (6, "created_at", "TEXT", 1, None, 0),
    ),
    "cron_sweep_cursors": (
        (0, "scope_key", "TEXT", 0, None, 1),
        (1, "cron_id", "TEXT", 1, None, 0),
        (2, "registry_generation", "INTEGER", 1, None, 0),
        (3, "command_digest", "TEXT", 1, None, 0),
        (4, "catch_up_policy_version", "INTEGER", 1, None, 0),
        (5, "cursor_value", "INTEGER", 1, None, 0),
        (6, "updated_at", "TEXT", 1, None, 0),
    ),
    "cron_trigger_deliveries": (
        (0, "trigger_event_id", "TEXT", 0, None, 1),
        (1, "trigger_digest", "TEXT", 1, None, 0),
        (2, "canonical_bytes", "BLOB", 1, None, 0),
        (3, "trigger_kind", "TEXT", 1, None, 0),
        (4, "transport_kind", "TEXT", 1, None, 0),
        (5, "cron_id", "TEXT", 1, None, 0),
        (6, "registry_generation", "INTEGER", 1, None, 0),
        (7, "schedule_bucket", "TEXT", 1, None, 0),
        (8, "command_digest", "TEXT", 1, None, 0),
        (9, "release_digest", "TEXT", 1, None, 0),
        (10, "execution_id", "TEXT", 0, None, 0),
        (11, "disposition", "TEXT", 1, None, 0),
        (12, "reason_code", "TEXT", 1, None, 0),
        (13, "submitted_at", "TEXT", 1, None, 0),
        (14, "created_at", "TEXT", 1, None, 0),
    ),
}

_FROZEN_V3_INDEX_LIST = {
    "cron_authority_schema_version": (),
    "cron_evidence": ((0, "sqlite_autoindex_cron_evidence_1", 1, "pk", 0),),
    "cron_execution_aggregates": (
        (0, "sqlite_autoindex_cron_execution_aggregates_2", 1, "u", 0),
        (1, "sqlite_autoindex_cron_execution_aggregates_1", 1, "pk", 0),
    ),
    "cron_execution_attempts": (
        (0, "sqlite_autoindex_cron_execution_attempts_1", 1, "pk", 0),
    ),
    "cron_receipts": (
        (0, "sqlite_autoindex_cron_receipts_2", 1, "u", 0),
        (1, "sqlite_autoindex_cron_receipts_1", 1, "pk", 0),
    ),
    "cron_registry_snapshots_v1": (
        (0, "sqlite_autoindex_cron_registry_snapshots_v1_2", 1, "u", 0),
        (1, "sqlite_autoindex_cron_registry_snapshots_v1_1", 1, "pk", 0),
    ),
    "cron_registry_snapshots_v2": (
        (0, "sqlite_autoindex_cron_registry_snapshots_v2_2", 1, "u", 0),
        (1, "sqlite_autoindex_cron_registry_snapshots_v2_1", 1, "pk", 0),
    ),
    "cron_sweep_cursors": (
        (0, "sqlite_autoindex_cron_sweep_cursors_2", 1, "u", 0),
        (1, "sqlite_autoindex_cron_sweep_cursors_1", 1, "pk", 0),
    ),
    "cron_trigger_deliveries": (
        (0, "sqlite_autoindex_cron_trigger_deliveries_1", 1, "pk", 0),
    ),
}

_FROZEN_V3_FOREIGN_KEYS = {
    "cron_authority_schema_version": (),
    "cron_evidence": (),
    "cron_execution_aggregates": (),
    "cron_execution_attempts": (
        (
            0,
            0,
            "cron_execution_aggregates",
            "execution_id",
            "execution_id",
            "NO ACTION",
            "RESTRICT",
            "NONE",
        ),
    ),
    "cron_receipts": (
        (
            0,
            0,
            "cron_execution_attempts",
            "execution_id",
            "execution_id",
            "NO ACTION",
            "RESTRICT",
            "NONE",
        ),
        (
            0,
            1,
            "cron_execution_attempts",
            "attempt",
            "attempt",
            "NO ACTION",
            "RESTRICT",
            "NONE",
        ),
        (
            1,
            0,
            "cron_evidence",
            "evidence_digest",
            "evidence_digest",
            "NO ACTION",
            "RESTRICT",
            "NONE",
        ),
    ),
    "cron_registry_snapshots_v1": (),
    "cron_registry_snapshots_v2": (),
    "cron_sweep_cursors": (),
    "cron_trigger_deliveries": (
        (
            0,
            0,
            "cron_execution_aggregates",
            "execution_id",
            "execution_id",
            "NO ACTION",
            "RESTRICT",
            "NONE",
        ),
    ),
}

_FROZEN_V3_NEW_TABLE_ROWS = {"cron_registry_snapshots_v2": ()}

_FROZEN_V3_ORACLE_SHA256 = (
    "927d7abcedcb9c77957d1493d67c140cba3686976072639a1f49ab2afbeb80a5"
)


def _authority_schema_sql(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM main.sqlite_master
        WHERE (tbl_name LIKE 'cron\\_%' ESCAPE '\\'
               OR name LIKE 'cron\\_%' ESCAPE '\\')
        ORDER BY type ASC, name ASC;
        """
    ).fetchall()


def _relationship_projection(conn: sqlite3.Connection):
    return {
        "receipt_chain": conn.execute(
            """
            SELECT r.receipt_id, r.execution_id, r.attempt, a.state,
                   a.execution_id, g.execution_id, r.evidence_digest, e.evidence_digest
            FROM main.cron_receipts AS r
            JOIN main.cron_execution_attempts AS a
              ON a.execution_id = r.execution_id AND a.attempt = r.attempt
            JOIN main.cron_execution_aggregates AS g
              ON g.execution_id = a.execution_id
            JOIN main.cron_evidence AS e
              ON e.evidence_digest = r.evidence_digest
            ORDER BY r.receipt_id ASC;
            """
        ).fetchall(),
        "trigger_delivery_chain": conn.execute(
            """
            SELECT d.trigger_event_id, d.execution_id, g.execution_id
            FROM main.cron_trigger_deliveries AS d
            JOIN main.cron_execution_aggregates AS g
              ON g.execution_id = d.execution_id
            ORDER BY d.trigger_event_id ASC;
            """
        ).fetchall(),
    }


def _canonical_state(conn: sqlite3.Connection):
    tables = tuple(
        row[0]
        for row in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' "
            "AND (name LIKE 'cron\\_%' ESCAPE '\\' "
            "OR name='unrelated_caller_table') ORDER BY name ASC;"
        ).fetchall()
    )
    return {
        "rows": {
            table: conn.execute(
                f"SELECT * FROM main.{table} ORDER BY 1 ASC;"
            ).fetchall()
            for table in tables
        },
        "schema_sql": _authority_schema_sql(conn),
        "indexes": {
            table: tuple(
                row[1]
                for row in conn.execute(f"PRAGMA main.index_list({table});").fetchall()
            )
            for table in tables
            if table != "unrelated_caller_table"
        },
        "foreign_keys": {
            table: conn.execute(f"PRAGMA main.foreign_key_list({table});").fetchall()
            for table in tables
            if table != "unrelated_caller_table"
        },
        "relationships": _relationship_projection(conn),
    }


def _assert_exact_v2_state(conn: sqlite3.Connection, oracle: dict) -> None:
    for table in oracle["tables"]:
        assert (
            conn.execute(f"SELECT * FROM main.{table} ORDER BY 1 ASC;").fetchall()
            == oracle["projections"][table]
        )
    assert _authority_schema_sql(conn) == oracle["schema_sql"]
    for table, expected in oracle["index_names"].items():
        assert (
            tuple(
                row[1]
                for row in conn.execute(f"PRAGMA main.index_list({table});").fetchall()
            )
            == expected
        )
    assert (
        tuple(
            row[0]
            for row in conn.execute(
                "SELECT name FROM main.sqlite_master WHERE type='trigger' "
                "AND tbl_name LIKE 'cron\\_%' ESCAPE '\\' ORDER BY name ASC;"
            ).fetchall()
        )
        == oracle["trigger_names"]
    )
    for table, expected in oracle["foreign_keys"].items():
        assert (
            conn.execute(f"PRAGMA main.foreign_key_list({table});").fetchall()
            == expected
        )
    assert _relationship_projection(conn) == oracle["relationships"]
    assert conn.execute("PRAGMA main.foreign_key_check;").fetchall() == []
    assert conn.execute("PRAGMA main.integrity_check;").fetchall() == [("ok",)]
    assert conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)


def _assert_exact_v3_state(conn: sqlite3.Connection, oracle: dict) -> None:
    expected_rows = {
        table: tuple(rows) for table, rows in oracle["projections"].items()
    }
    expected_rows["cron_authority_schema_version"] = tuple(
        (authority_id, 3, installed_at)
        for authority_id, _old_version, installed_at in oracle["projections"][
            "cron_authority_schema_version"
        ]
    )
    expected_rows["cron_execution_attempts"] = tuple(
        row[:6] + (None,) * 10 + row[6:]
        for row in oracle["projections"]["cron_execution_attempts"]
    )
    unrelated_rows = tuple(expected_rows.pop("unrelated_caller_table"))
    expected_rows.update(_FROZEN_V3_NEW_TABLE_ROWS)
    assert set(expected_rows) == _V3_TABLE_NAMES
    assert (
        tuple(
            conn.execute(
                "SELECT * FROM main.unrelated_caller_table ORDER BY 1 ASC;"
            ).fetchall()
        )
        == unrelated_rows
    )

    for table in sorted(_V3_TABLE_NAMES):
        actual_rows = tuple(
            conn.execute(f"SELECT * FROM main.{table} ORDER BY 1 ASC;").fetchall()
        )
        assert actual_rows == expected_rows[table]
        assert (
            tuple(conn.execute(f"PRAGMA main.table_info({table});").fetchall())
            == _FROZEN_V3_TABLE_INFO[table]
        )
        assert (
            tuple(conn.execute(f"PRAGMA main.index_list({table});").fetchall())
            == _FROZEN_V3_INDEX_LIST[table]
        )
        assert (
            tuple(conn.execute(f"PRAGMA main.foreign_key_list({table});").fetchall())
            == _FROZEN_V3_FOREIGN_KEYS[table]
        )

    assert tuple(_authority_schema_sql(conn)) == _FROZEN_V3_SCHEMA_SQL
    objects = tuple((kind, name) for kind, name, _table, _sql in _FROZEN_V3_SCHEMA_SQL)
    assert len(objects) == len(set(objects))
    assert {name for kind, name in objects if kind == "table"} == _V3_TABLE_NAMES
    assert {name for kind, name in objects if kind == "trigger"} == _V3_TRIGGER_NAMES
    assert not conn.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='cron_execution_attempts_old';"
    ).fetchall()
    assert not conn.execute(
        "SELECT 1 FROM temp.sqlite_master WHERE name GLOB 'cron_*';"
    ).fetchall()
    assert _relationship_projection(conn) == oracle["relationships"]
    assert conn.execute("PRAGMA main.foreign_key_check;").fetchall() == []
    assert conn.execute("PRAGMA main.integrity_check;").fetchall() == [("ok",)]
    assert conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)


def test_23_true_v2_migration_success(disposable_db: Path):
    """Test 23: True populated v2 database migrates to v3 preserving rows, relationships, and FK/integrity."""
    oracle = _create_true_v2_db(disposable_db)
    migrate_cron_authority(disposable_db)

    conn = connect_cron_authority(disposable_db)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT authority_id, schema_version FROM cron_authority_schema_version;"
    )
    assert cursor.fetchone() == (1, 3)

    cursor.execute(
        "SELECT execution_id, attempt, state, runner_id, fence_token, lease_expires_at, source_id, schema_version FROM cron_execution_attempts ORDER BY execution_id ASC, attempt ASC;"
    )
    attempts = cursor.fetchall()
    assert len(attempts) == 3
    assert attempts[0][0:6] == (
        "exec-v2-001",
        1,
        "terminal",
        "runner-node-01",
        1,
        "2026-07-28T04:10:00Z",
    )
    assert attempts[0][6] is None
    assert attempts[0][7] is None

    assert attempts[1][0:6] == (
        "exec-v2-001",
        2,
        "running",
        "runner-node-01",
        2,
        "2026-07-28T04:20:00Z",
    )
    assert attempts[2][0:6] == (
        "exec-v2-002",
        1,
        "admitted",
        None,
        None,
        None,
    )

    cursor.execute("SELECT id, caller_data FROM unrelated_caller_table;")
    assert cursor.fetchone() == (999, "unrelated_data_v2")

    assert cursor.execute("PRAGMA foreign_key_check;").fetchall() == []
    assert cursor.execute("PRAGMA integrity_check;").fetchall() == [("ok",)]
    assert cursor.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    _assert_exact_v3_state(conn, oracle)
    conn.close()


def test_24_second_migration_idempotent_v2_to_v3(disposable_db: Path):
    """Test 24: Second migration preserves the complete canonical v3 state."""
    oracle = _create_true_v2_db(disposable_db)
    migrate_cron_authority(disposable_db)

    first = connect_cron_authority(disposable_db)
    _assert_exact_v3_state(first, oracle)
    first_state = _canonical_state(first)
    first.close()

    migrate_cron_authority(disposable_db)

    second = connect_cron_authority(disposable_db)
    _assert_exact_v3_state(second, oracle)
    assert _canonical_state(second) == first_state
    second.close()


class WrapperCursor:
    def __init__(self, real_cursor: sqlite3.Cursor, check_sql_fn=None):
        self.real_cursor = real_cursor
        self.check_sql_fn = check_sql_fn

    def execute(self, sql: str, *args, **kwargs):
        if self.check_sql_fn:
            self.check_sql_fn(sql)
        return self.real_cursor.execute(sql, *args, **kwargs)

    def fetchone(self):
        return self.real_cursor.fetchone()

    def fetchall(self):
        return self.real_cursor.fetchall()


class InjectedFailureConnectionWrapper:
    def __init__(self, real_conn: sqlite3.Connection, fail_pattern: str):
        self.real_conn = real_conn
        self.fail_pattern = fail_pattern

    def check_sql(self, sql: str):
        if self.fail_pattern in sql:
            raise sqlite3.OperationalError(f"injected_failure_{self.fail_pattern}")

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor(), self.check_sql)

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        self.check_sql(sql)
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


def test_25_injected_failure_before_rename(disposable_db: Path):
    """Test 25: Injected failure before rename rolls back, restoring v2 state and FK mode."""
    oracle = _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = InjectedFailureConnectionWrapper(
        raw_conn, "RENAME TO cron_execution_attempts_old"
    )

    with pytest.raises(sqlite3.OperationalError, match="injected_failure"):
        migrate_cron_authority(wrapper)

    cursor = raw_conn.cursor()
    cursor.execute("SELECT schema_version FROM cron_authority_schema_version;")
    assert cursor.fetchone() == (2,)
    assert raw_conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    _assert_exact_v2_state(raw_conn, oracle)
    raw_conn.close()


def test_26_injected_failure_after_rename(disposable_db: Path):
    """Test 26: Injected failure after rename/before copy completion rolls back without temporary table leak."""
    oracle = _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = InjectedFailureConnectionWrapper(
        raw_conn, "DROP TABLE cron_execution_attempts_old"
    )

    with pytest.raises(sqlite3.OperationalError, match="injected_failure"):
        migrate_cron_authority(wrapper)

    cursor = raw_conn.cursor()
    cursor.execute("SELECT schema_version FROM cron_authority_schema_version;")
    assert cursor.fetchone() == (2,)
    tables = [
        r[0]
        for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    ]
    assert "cron_execution_attempts_old" not in tables
    assert raw_conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    _assert_exact_v2_state(raw_conn, oracle)
    raw_conn.close()


def test_27_schema_object_validation_failures(disposable_db: Path):
    """Test 27: Malformed/extra/TEMP schema objects fail validation before destructive DDL."""
    _create_true_v2_db(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute("CREATE TABLE main.cron_extra_unauthorized (id INT);")
    conn.commit()

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(conn)
    assert exc_info.value.code == "schema_object_mismatch"
    conn.close()


def test_28_active_caller_transaction_rejected(disposable_db: Path):
    """Test 28: Active transaction on caller connection is rejected without commit/rollback ownership theft."""
    _create_true_v2_db(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute("BEGIN;")
    assert conn.in_transaction is True

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(conn)
    assert exc_info.value.code == "active_caller_transaction"
    assert conn.in_transaction is True
    conn.rollback()
    conn.close()


class BeginFailureNoTxWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.rollback_calls = 0

    def check_sql(self, sql: str):
        if "BEGIN IMMEDIATE" in sql:
            raise sqlite3.OperationalError("begin_failed_no_tx")

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor(), self.check_sql)

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        self.check_sql(sql)
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        self.rollback_calls += 1
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


def test_29_begin_failure_handling_outside_tx(disposable_db: Path):
    """Test 29: BEGIN failure with in_transaction=False performs 0 rollbacks, restores FK, and retains connection usability."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = BeginFailureNoTxWrapper(raw_conn)

    with pytest.raises(sqlite3.OperationalError, match="begin_failed_no_tx"):
        migrate_cron_authority(wrapper)

    assert wrapper.rollback_calls == 0
    assert raw_conn.in_transaction is False
    assert raw_conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    res = raw_conn.execute("SELECT 1;").fetchone()
    assert res == (1,)
    raw_conn.close()


class BeginFailureWithTxWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.rollback_calls = 0

    def check_sql(self, sql: str):
        if "BEGIN IMMEDIATE" in sql:
            self.real_conn.execute(sql)
            raise sqlite3.OperationalError("begin_failed_with_tx")

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor(), self.check_sql)

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        self.check_sql(sql)
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        self.rollback_calls += 1
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


def test_30_begin_failure_handling_inside_tx(disposable_db: Path):
    """Test 30: BEGIN failure with in_transaction=True closes connection without rollback attempts."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = BeginFailureWithTxWrapper(raw_conn)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.code == "begin_failed_active_transaction"
    assert str(exc_info.value) == "BEGIN failed after transaction state became active"
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert str(exc_info.value.__cause__) == "begin_failed_with_tx"
    assert wrapper.rollback_calls == 0
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        raw_conn.execute("SELECT 1;")


class CommitFailureWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.rollback_calls = 0

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor())

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        raise sqlite3.OperationalError("commit_failed_injected")

    def rollback(self):
        self.rollback_calls += 1
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


def test_31_commit_failure_handling(disposable_db: Path):
    """Test 31: Commit failure triggers conditional rollback, restores FK mode, and preserves primary error."""
    oracle = _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = CommitFailureWrapper(raw_conn)

    with pytest.raises(sqlite3.OperationalError, match="commit_failed_injected"):
        migrate_cron_authority(wrapper)

    assert wrapper.rollback_calls == 1
    assert raw_conn.in_transaction is False
    assert raw_conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    cursor = raw_conn.cursor()
    cursor.execute("SELECT schema_version FROM cron_authority_schema_version;")
    assert cursor.fetchone() == (2,)
    _assert_exact_v2_state(raw_conn, oracle)
    raw_conn.close()


class RollbackFailureWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn

    def check_sql(self, sql: str):
        if "cron_authority_schema_version SET schema_version = 3" in sql:
            raise sqlite3.OperationalError("post_begin_error")

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor(), self.check_sql)

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        self.check_sql(sql)
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        raise sqlite3.OperationalError("rollback_failed_injected")

    def close(self):
        return self.real_conn.close()


def test_32_rollback_failure_handling(disposable_db: Path):
    """Test 32: Rollback failure invalidates connection and raises transaction_closure_failed chained error."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = RollbackFailureWrapper(raw_conn)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.code == "transaction_closure_failed"
    assert exc_info.value.context == {"rollback_error_type": "OperationalError"}
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert exc_info.value.__cause__.args[0] == "post_begin_error"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        raw_conn.execute("SELECT 1;")


class RollbackRealThenFailWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.rollback_calls = 0

    def check_sql(self, sql: str):
        if "cron_authority_schema_version SET schema_version = 3" in sql:
            raise sqlite3.OperationalError("post_begin_error")

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor(), self.check_sql)

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        self.check_sql(sql)
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        self.rollback_calls += 1
        self.real_conn.rollback()
        raise sqlite3.OperationalError("rollback_wrapper_error")

    def close(self):
        return self.real_conn.close()


def test_33_rollback_failure_after_real_rollback(disposable_db: Path):
    """Test 33: Rollback failure after underlying real rollback closed transaction restores FK mode and reports primary error."""
    oracle = _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = RollbackRealThenFailWrapper(raw_conn)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.code == "migration_failed_with_rollback_error"
    assert str(exc_info.value) == "Migration failed after rollback reported an error"
    assert exc_info.value.context == {"rollback_error_type": "OperationalError"}
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert str(exc_info.value.__cause__) == "post_begin_error"
    assert wrapper.rollback_calls == 1
    assert raw_conn.in_transaction is False
    assert raw_conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    _assert_exact_v2_state(raw_conn, oracle)
    raw_conn.close()


def test_34_fresh_db_and_v3_paths_remain_green(disposable_db: Path):
    """Test 34: Fresh DB and already-v3 DB migration paths remain green and keep FK enforcement enabled."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)
    assert conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    assert conn.execute(
        "SELECT authority_id, schema_version FROM cron_authority_schema_version;"
    ).fetchone() == (1, 3)
    conn.close()

    migrate_cron_authority(disposable_db)
    conn2 = connect_cron_authority(disposable_db)
    assert conn2.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    conn2.close()


def test_35_production_db_isolation():
    """Test 35: Proves no test touches /home/ubuntu/.prismatic/bus/event_log.sqlite or production paths."""
    prod_path = Path("/home/ubuntu/.prismatic/bus/event_log.sqlite")
    with pytest.raises(AssertionError):
        sqlite3.connect(str(prod_path))


class _SingleRowResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class ForeignKeyDisableReadbackFailureWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.off_calls = 0
        self.readback_calls = 0
        self.post_off_readback_calls = 0
        self.off_issued = False
        self.close_calls = 0

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def cursor(self):
        return self.real_conn.cursor()

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        if sql == "PRAGMA foreign_keys = OFF;":
            self.off_calls += 1
            self.off_issued = True
            return self.real_conn.execute(sql, *args, **kwargs)
        if sql == "PRAGMA main.foreign_keys;":
            self.readback_calls += 1
            if self.off_issued:
                self.post_off_readback_calls += 1
            return _SingleRowResult((1,))
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        return self.real_conn.rollback()

    def close(self):
        self.close_calls += 1
        return self.real_conn.close()


class ForeignKeyRestoreReadbackFailureWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.off_calls = 0
        self.on_calls = 0
        self.post_off_on_calls = 0
        self.off_issued = False
        self.readback_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def check_sql(self, sql: str):
        if "cron_authority_schema_version SET schema_version = 3" in sql:
            raise sqlite3.OperationalError("post_begin_error")

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def cursor(self):
        return WrapperCursor(self.real_conn.cursor(), self.check_sql)

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        self.check_sql(sql)
        if sql == "PRAGMA foreign_keys = OFF;":
            self.off_calls += 1
            self.off_issued = True
        elif sql == "PRAGMA foreign_keys = ON;":
            self.on_calls += 1
            if self.off_issued:
                self.post_off_on_calls += 1
        result = self.real_conn.execute(sql, *args, **kwargs)
        if sql == "PRAGMA main.foreign_keys;":
            self.readback_calls += 1
            if self.readback_calls == 3:
                return _SingleRowResult((0,))
        return result

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        self.rollback_calls += 1
        return self.real_conn.rollback()

    def close(self):
        self.close_calls += 1
        return self.real_conn.close()


def test_36_fk_disable_readback_failure_invalidates(disposable_db: Path):
    """Test 36: A failed FK-disable readback closes the caller connection without retry."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = ForeignKeyDisableReadbackFailureWrapper(raw_conn)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.code == "foreign_keys_disable_failed"
    assert str(exc_info.value) == "Failed to disable foreign keys before migration"
    assert wrapper.off_calls == 1
    assert wrapper.readback_calls == 2
    assert wrapper.post_off_readback_calls == 1
    assert wrapper.close_calls >= 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        raw_conn.execute("SELECT 1;")


def test_37_fk_restore_readback_failure_invalidates(disposable_db: Path):
    """Test 37: A failed FK-restore readback closes the caller connection without retry."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = ForeignKeyRestoreReadbackFailureWrapper(raw_conn)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.code == "foreign_keys_restore_failed"
    assert str(exc_info.value) == "Foreign key restoration failed after migration error"
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert str(exc_info.value.__cause__) == "post_begin_error"
    assert wrapper.off_calls == 1
    assert wrapper.on_calls == 2
    assert wrapper.post_off_on_calls == 1
    assert wrapper.readback_calls == 3
    assert wrapper.rollback_calls == 1
    assert wrapper.close_calls >= 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        raw_conn.execute("SELECT 1;")


class PostCommitRestoreReadbackFailureWrapper:
    def __init__(self, real_conn: sqlite3.Connection, *, raise_on_readback: bool):
        self.real_conn = real_conn
        self.raise_on_readback = raise_on_readback
        self.off_calls = 0
        self.on_calls = 0
        self.post_off_on_calls = 0
        self.off_issued = False
        self.readback_calls = 0
        self.close_calls = 0

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def cursor(self):
        return self.real_conn.cursor()

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        if sql == "PRAGMA foreign_keys = OFF;":
            self.off_calls += 1
            self.off_issued = True
        elif sql == "PRAGMA foreign_keys = ON;":
            self.on_calls += 1
            if self.off_issued:
                self.post_off_on_calls += 1
        result = self.real_conn.execute(sql, *args, **kwargs)
        if sql == "PRAGMA main.foreign_keys;":
            self.readback_calls += 1
            if self.readback_calls == 3:
                if self.raise_on_readback:
                    raise sqlite3.OperationalError("post_commit_restore_readback_error")
                return _SingleRowResult((0,))
        return result

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        return self.real_conn.rollback()

    def close(self):
        self.close_calls += 1
        return self.real_conn.close()


def _assert_post_commit_restore_failure(
    wrapper: PostCommitRestoreReadbackFailureWrapper,
    raw_conn: sqlite3.Connection,
    exc: CronAuthorityError,
) -> None:
    assert exc.code == "foreign_keys_restore_failed"
    assert str(exc) == "Failed to restore foreign keys after migration"
    assert wrapper.off_calls == 1
    assert wrapper.on_calls == 2
    assert wrapper.post_off_on_calls == 1
    assert wrapper.readback_calls == 3
    assert wrapper.close_calls >= 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        raw_conn.execute("SELECT 1;")


def test_38_post_commit_restore_nonzero_readback_invalidates(disposable_db: Path):
    """Test 38: Nonzero post-commit restore readback invalidates without retry."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = PostCommitRestoreReadbackFailureWrapper(raw_conn, raise_on_readback=False)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.__cause__ is None
    _assert_post_commit_restore_failure(wrapper, raw_conn, exc_info.value)


def test_39_post_commit_restore_readback_exception_invalidates(disposable_db: Path):
    """Test 39: Exceptional post-commit restore readback invalidates without retry."""
    _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = PostCommitRestoreReadbackFailureWrapper(raw_conn, raise_on_readback=True)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert str(exc_info.value.__cause__) == "post_commit_restore_readback_error"
    _assert_post_commit_restore_failure(wrapper, raw_conn, exc_info.value)


class RowCopyBindingMismatchCursor(WrapperCursor):
    def execute(self, sql: str, *args, **kwargs):
        if "INSERT INTO main.cron_execution_attempts" in sql:
            return self.real_cursor.execute(sql, ("unexpected-binding",))
        return self.real_cursor.execute(sql, *args, **kwargs)


class RowCopyBindingMismatchWrapper:
    def __init__(self, real_conn: sqlite3.Connection):
        self.real_conn = real_conn
        self.rollback_calls = 0

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def cursor(self):
        return RowCopyBindingMismatchCursor(self.real_conn.cursor())

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        self.rollback_calls += 1
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


class InjectedValidationCursor:
    def __init__(self, real_cursor: sqlite3.Cursor, failure_kind: str):
        self.real_cursor = real_cursor
        self.failure_kind = failure_kind
        self.fake_rows = None

    def execute(self, sql: str, *args, **kwargs):
        if (
            self.failure_kind == "foreign_key"
            and "PRAGMA main.foreign_key_check" in sql
        ):
            self.fake_rows = [("cron_receipts", 1, "cron_execution_attempts", 0)]
            return self
        if self.failure_kind == "integrity" and "PRAGMA main.integrity_check" in sql:
            self.fake_rows = [("injected_integrity_failure",)]
            return self
        self.fake_rows = None
        return self.real_cursor.execute(sql, *args, **kwargs)

    def fetchall(self):
        if self.fake_rows is not None:
            rows = self.fake_rows
            self.fake_rows = None
            return rows
        return self.real_cursor.fetchall()

    def fetchone(self):
        return self.real_cursor.fetchone()


class InjectedValidationFailureWrapper:
    def __init__(self, real_conn: sqlite3.Connection, failure_kind: str):
        self.real_conn = real_conn
        self.failure_kind = failure_kind
        self.rollback_calls = 0

    @property
    def in_transaction(self):
        return self.real_conn.in_transaction

    def cursor(self):
        return InjectedValidationCursor(self.real_conn.cursor(), self.failure_kind)

    def create_function(self, *args, **kwargs):
        return self.real_conn.create_function(*args, **kwargs)

    def execute(self, sql: str, *args, **kwargs):
        return self.real_conn.execute(sql, *args, **kwargs)

    def commit(self):
        return self.real_conn.commit()

    def rollback(self):
        self.rollback_calls += 1
        return self.real_conn.rollback()

    def close(self):
        return self.real_conn.close()


def test_40_row_copy_binding_mismatch_rolls_back_exactly(disposable_db: Path):
    """Test 40: A real binding mismatch during row copy restores exact v2 state."""
    oracle = _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = RowCopyBindingMismatchWrapper(raw_conn)

    with pytest.raises(sqlite3.ProgrammingError, match="bindings"):
        migrate_cron_authority(wrapper)

    assert wrapper.rollback_calls == 1
    assert raw_conn.in_transaction is False
    _assert_exact_v2_state(raw_conn, oracle)
    raw_conn.close()


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("foreign_key", "foreign_key_violation"),
        ("integrity", "integrity_check_failure"),
    ],
)
def test_41_injected_validation_failure_rolls_back_exactly(
    disposable_db: Path, failure_kind: str, expected_code: str
):
    """Test 41: Injected FK/integrity failures block commit and restore exact v2 state."""
    oracle = _create_true_v2_db(disposable_db)
    raw_conn = connect_cron_authority(disposable_db)
    wrapper = InjectedValidationFailureWrapper(raw_conn, failure_kind)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(wrapper)

    assert exc_info.value.code == expected_code
    assert wrapper.rollback_calls == 1
    assert raw_conn.in_transaction is False
    _assert_exact_v2_state(raw_conn, oracle)
    raw_conn.close()


def _mutate_v2_schema(conn: sqlite3.Connection, mutation: str) -> None:
    if mutation == "missing_trigger":
        conn.execute("DROP TRIGGER main.trg_cron_attempt_fence_guard;")
    elif mutation == "malformed_trigger":
        conn.execute("DROP TRIGGER main.trg_cron_attempt_fence_guard;")
        conn.execute(
            """
            CREATE TRIGGER main.trg_cron_attempt_fence_guard
            BEFORE UPDATE ON cron_execution_attempts
            BEGIN
                SELECT 1;
            END;
            """
        )
    elif mutation == "renamed_column":
        conn.execute(
            "ALTER TABLE main.cron_execution_attempts "
            "RENAME COLUMN lease_expires_at TO lease_expires_bad;"
        )
    elif mutation == "extra_index":
        conn.execute(
            "CREATE INDEX main.cron_extra_attempt_state "
            "ON cron_execution_attempts(state);"
        )
    elif mutation == "temp_collision":
        conn.execute("CREATE TEMP TABLE cron_execution_attempts (id INTEGER);")
    else:  # pragma: no cover - test parameter is closed
        raise AssertionError(mutation)
    conn.commit()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_trigger", "schema_object_mismatch"),
        ("malformed_trigger", "schema_object_mismatch"),
        ("renamed_column", "schema_object_mismatch"),
        ("extra_index", "schema_object_mismatch"),
        ("temp_collision", "schema_object_mismatch"),
    ],
)
def test_42_malformed_v2_schema_fails_before_destructive_ddl(
    disposable_db: Path, mutation: str, expected_code: str
):
    """Test 42: Malformed/missing/extra/TEMP v2 objects fail before destructive DDL."""
    _create_true_v2_db(disposable_db)
    conn = connect_cron_authority(disposable_db)
    _mutate_v2_schema(conn, mutation)

    with pytest.raises(CronAuthorityError) as exc_info:
        migrate_cron_authority(conn)

    assert exc_info.value.code == expected_code
    assert conn.execute(
        "SELECT schema_version FROM main.cron_authority_schema_version;"
    ).fetchone() == (2,)
    assert not conn.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='cron_execution_attempts_old';"
    ).fetchall()
    assert not conn.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='cron_registry_snapshots_v2';"
    ).fetchall()
    assert conn.execute("PRAGMA foreign_keys;").fetchone() == (1,)
    conn.close()
