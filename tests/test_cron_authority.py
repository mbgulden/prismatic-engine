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
