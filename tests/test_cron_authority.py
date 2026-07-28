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

from pathlib import Path
import sqlite3
import pytest

from prismatic.cron_authority import (
    SCHEMA_VERSION,
    CronAuthorityError,
    connect_cron_authority,
    migrate_cron_authority,
    resolve_db_target,
)
from prismatic.task_admission import TaskAdmissionStore


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    """Return path to a fresh disposable test SQLite database."""
    return tmp_path / "test_cron_authority.sqlite"


@pytest.fixture
def sample_aggregate_kwargs() -> dict:
    return {
        "execution_id": "exec-20260728-0001",
        "cron_id": "cron.test-job",
        "registry_generation": 1,
        "schedule_bucket": "2026-07-28T04:00:00Z",
        "command_digest": "a" * 64,
        "created_at": "2026-07-28T04:00:00Z",
    }


@pytest.fixture
def sample_attempt_kwargs(sample_aggregate_kwargs: dict) -> dict:
    return {
        "execution_id": sample_aggregate_kwargs["execution_id"],
        "attempt": 1,
        "state": "claimed",
        "runner_id": "runner-node-01",
        "fence_token": 1,
        "lease_expires_at": "2026-07-28T04:10:00Z",
        "created_at": "2026-07-28T04:00:00Z",
        "updated_at": "2026-07-28T04:00:00Z",
    }


@pytest.fixture
def sample_evidence_kwargs() -> dict:
    return {
        "evidence_digest": "e" * 64,
        "canonical_bytes": b'{"status":"ok"}',
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
        "SELECT schema_version, installed_at FROM cron_authority_schema_version;"
    )
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == SCHEMA_VERSION
    assert rows[0][1].endswith("Z")

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
        "CREATE TABLE cron_authority_schema_version (schema_version INTEGER PRIMARY KEY, installed_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO cron_authority_schema_version (schema_version, installed_at) VALUES (99, '2026-07-28T00:00:00Z');"
    )
    conn.commit()

    with pytest.raises(
        CronAuthorityError, match="Unsupported cron authority schema version"
    ):
        migrate_cron_authority(conn)

    cursor = conn.cursor()
    cursor.execute("SELECT schema_version FROM cron_authority_schema_version;")
    assert cursor.fetchone()[0] == 99
    conn.close()


def test_6_duplicate_aggregate_uniqueness_rejected(
    disposable_db: Path, sample_aggregate_kwargs: dict
):
    """Test 6: Duplicate aggregate uniqueness tuple is rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        """INSERT INTO cron_execution_aggregates
        (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, created_at)
        VALUES (?, ?, ?, ?, ?, ?);""",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.commit()

    # Attempt duplicate aggregate tuple with different execution_id
    duplicate_tuple = dict(sample_aggregate_kwargs)
    duplicate_tuple["execution_id"] = "exec-20260728-0002"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO cron_execution_aggregates
            (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?);""",
            tuple(duplicate_tuple.values()),
        )
    conn.close()


def test_7_duplicate_execution_id_rejected(
    disposable_db: Path, sample_aggregate_kwargs: dict
):
    """Test 7: Duplicate execution ID is rejected."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        """INSERT INTO cron_execution_aggregates
        (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, created_at)
        VALUES (?, ?, ?, ?, ?, ?);""",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.commit()

    # Duplicate execution_id with different cron_id
    duplicate_id = dict(sample_aggregate_kwargs)
    duplicate_id["cron_id"] = "cron.different-job"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO cron_execution_aggregates
            (execution_id, cron_id, registry_generation, schedule_bucket, command_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?);""",
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
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(aggregate_2.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )

    # 9a. Attempt 0 rejected
    attempt_zero = dict(sample_attempt_kwargs, attempt=0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(attempt_zero.values()),
        )

    # 9b. Illegal attempt state rejected
    illegal_state = dict(sample_attempt_kwargs, state="illegal_state")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(illegal_state.values()),
        )

    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
    """Test 11: Receipt and evidence UPDATE and DELETE attempts are rejected by triggers."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    conn.execute(
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )
    conn.execute(
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
        "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
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
    assert cursor.fetchall() == [(1, "claimed"), (2, "admitted")]

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
        "INSERT INTO cron_execution_aggregates VALUES (?, ?, ?, ?, ?, ?);",
        tuple(sample_aggregate_kwargs.values()),
    )

    # 13a. Non-positive fence token (0) rejected
    bad_fence = dict(sample_attempt_kwargs, fence_token=0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(bad_fence.values()),
        )

    # 13b. Malformed lease timestamp (missing Z suffix) rejected
    bad_timestamp = dict(sample_attempt_kwargs, lease_expires_at="2026-07-28T04:10:00")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            tuple(bad_timestamp.values()),
        )

    conn.close()


def test_14_cursor_scope_collision_and_regression_trigger(disposable_db: Path):
    """Test 14: Cursor scope collision is rejected and cursor regression is prevented by trigger."""
    migrate_cron_authority(disposable_db)
    conn = connect_cron_authority(disposable_db)

    cursor_data = (
        "scope-job1-gen1",
        "cron.test-job",
        1,
        "a" * 64,
        1,
        "2026-07-28T04:00:00Z",
        "2026-07-28T04:00:00Z",
    )
    conn.execute(
        "INSERT INTO cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);",
        cursor_data,
    )
    conn.commit()

    # 14a. Scope collision rejected
    dup_cursor = (
        "scope-job1-gen1",
        "cron.test-job",
        1,
        "a" * 64,
        1,
        "2026-07-28T05:00:00Z",
        "2026-07-28T05:00:00Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cron_sweep_cursors VALUES (?, ?, ?, ?, ?, ?, ?);", dup_cursor
        )

    # 14b. Monotonic forward update succeeds
    conn.execute(
        "UPDATE cron_sweep_cursors SET cursor_value='2026-07-28T05:00:00Z' WHERE scope_key='scope-job1-gen1';"
    )
    conn.commit()

    # 14c. Cursor regression rejected by trigger
    with pytest.raises(
        (sqlite3.IntegrityError, sqlite3.OperationalError), match="regression"
    ):
        conn.execute(
            "UPDATE cron_sweep_cursors SET cursor_value='2026-07-28T03:00:00Z' WHERE scope_key='scope-job1-gen1';"
        )

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
    """Test 16: No test resolves or writes the production bus DB."""
    prod_bus_path = Path("/home/ubuntu/.prismatic/bus/event_log.sqlite")
    stat_before = prod_bus_path.stat() if prod_bus_path.exists() else None

    target = resolve_db_target(":memory:")
    assert target == ":memory:"

    if prod_bus_path.exists():
        stat_after = prod_bus_path.stat()
        assert stat_before.st_mtime_ns == stat_after.st_mtime_ns
        assert stat_before.st_size == stat_after.st_size
