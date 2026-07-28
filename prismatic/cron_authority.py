"""Idempotent, versioned SQLite schema foundation for cron execution authority.

This module provides the schema migration engine, transactional boundary, table definitions,
and database triggers for the cron execution authority subsystem (GRO-4345 / CRONAUTH-1).

Path Resolution and Boundary Rules:
- The migration API accepts an explicit SQLite database path (str or Path) or an active
  sqlite3.Connection target.
- Relative paths are resolved deterministically against the current working directory via
  Path(target).resolve(). Special targets such as ":memory:" or URI filenames starting with
  "file:" are preserved as-is.
- Tests and callers must provide an explicit disposable target; this module deliberately
  does not default to or mutate the production bus database (/home/ubuntu/.prismatic/bus/event_log.sqlite).
- Connections are configured with foreign keys enabled (`PRAGMA foreign_keys = ON;`) and
  bounded lock timeouts.
- Migrations execute inside an atomic `BEGIN IMMEDIATE` transaction. Any error triggers a full
  rollback (`conn.rollback()`), leaving no partial tables or version rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

SCHEMA_VERSION: int = 1

VALID_ATTEMPT_STATES: frozenset[str] = frozenset(
    {
        "admitted",
        "claimed",
        "running",
        "reconciling",
        "terminal",
    }
)

VALID_RECEIPT_OUTCOMES: frozenset[str] = frozenset(
    {
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "blocked",
        "missed_during_offline",
        "awaiting_operator_approval",
        "orphaned",
        "reconciled",
    }
)


class CronAuthorityError(ValueError):
    """Exception raised for cron authority schema and invariant violations."""

    def __init__(self, message: str, code: str = "cron_authority_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def resolve_db_target(target: Any) -> Any:
    """Resolve database target string, Path, or Connection deterministically."""
    if hasattr(target, "cursor") and hasattr(target, "execute"):
        return target
    target_str = str(target)
    if target_str == ":memory:" or target_str.startswith("file:"):
        return target_str
    return str(Path(target_str).resolve())


def connect_cron_authority(target: Any, timeout: float = 30.0) -> sqlite3.Connection:
    """Create or configure a transaction-free SQLite connection for authority work."""
    resolved = resolve_db_target(target)
    if hasattr(resolved, "cursor") and hasattr(resolved, "execute"):
        conn = resolved
        if conn.in_transaction:
            raise CronAuthorityError(
                "Caller connection has an active transaction",
                code="active_caller_transaction",
            )
    else:
        conn = sqlite3.connect(
            resolved,
            timeout=timeout,
            uri=isinstance(resolved, str) and resolved.startswith("file:"),
        )

    conn.execute("PRAGMA foreign_keys = ON;")
    if conn.execute("PRAGMA foreign_keys;").fetchone() != (1,):
        raise CronAuthorityError(
            "SQLite foreign keys could not be enabled",
            code="foreign_keys_unavailable",
        )
    conn.create_function(
        "sha256_hex",
        1,
        lambda value: (
            hashlib.sha256(bytes(value)).hexdigest()
            if isinstance(value, (bytes, bytearray, memoryview))
            else None
        ),
        deterministic=True,
    )
    return conn


_CREATE_VERSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_authority_schema_version (
    authority_id INTEGER PRIMARY KEY CHECK (authority_id = 1),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version >= 1),
    installed_at TEXT NOT NULL CHECK (installed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(installed_at) IS NOT NULL)
);
"""

_CREATE_AGGREGATES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_execution_aggregates (
    execution_id TEXT PRIMARY KEY CHECK (length(execution_id) >= 1 AND length(execution_id) <= 128),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    schedule_bucket TEXT NOT NULL CHECK (schedule_bucket GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(schedule_bucket) IS NOT NULL),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    release_digest TEXT NOT NULL CHECK (length(release_digest) = 64 AND release_digest NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(created_at) IS NOT NULL),
    CONSTRAINT uq_cron_aggregate UNIQUE (cron_id, registry_generation, schedule_bucket, command_digest, release_digest)
);
"""

_CREATE_EVIDENCE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_evidence (
    evidence_digest TEXT PRIMARY KEY CHECK (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 4000),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(created_at) IS NOT NULL),
    CONSTRAINT ck_evidence_content_address CHECK (evidence_digest = sha256_hex(canonical_bytes))
);
"""

_CREATE_ATTEMPTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_execution_attempts (
    execution_id TEXT NOT NULL REFERENCES cron_execution_aggregates(execution_id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),
    state TEXT NOT NULL CHECK (state IN ('admitted', 'claimed', 'running', 'reconciling', 'terminal')),
    runner_id TEXT CHECK (runner_id IS NULL OR (length(runner_id) >= 1 AND length(runner_id) <= 128)),
    fence_token INTEGER CHECK (fence_token IS NULL OR (typeof(fence_token) = 'integer' AND fence_token > 0)),
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR (lease_expires_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(lease_expires_at) IS NOT NULL)),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(updated_at) IS NOT NULL),
    CONSTRAINT ck_attempt_ownership CHECK (
        state IN ('admitted', 'terminal')
        OR (runner_id IS NOT NULL AND fence_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    PRIMARY KEY (execution_id, attempt)
);
"""

_CREATE_RECEIPTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_receipts (
    receipt_id TEXT PRIMARY KEY CHECK (length(receipt_id) >= 1 AND length(receipt_id) <= 128),
    execution_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (typeof(attempt) = 'integer' AND attempt >= 1),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'timed_out', 'cancelled', 'blocked', 'missed_during_offline', 'awaiting_operator_approval', 'orphaned', 'reconciled')),
    runner_id TEXT NOT NULL CHECK (length(runner_id) >= 1 AND length(runner_id) <= 128),
    runner_release_digest TEXT NOT NULL CHECK (length(runner_release_digest) = 64 AND runner_release_digest NOT GLOB '*[^0-9a-f]*'),
    started_at TEXT NOT NULL CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(started_at) IS NOT NULL),
    finished_at TEXT NOT NULL CHECK (finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(finished_at) IS NOT NULL),
    error_classification TEXT CHECK (error_classification IS NULL OR length(error_classification) <= 128),
    evidence_digest TEXT REFERENCES cron_evidence(evidence_digest) ON DELETE RESTRICT CHECK (evidence_digest IS NULL OR (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*')),
    signing_key_id TEXT NOT NULL CHECK (length(signing_key_id) >= 1 AND length(signing_key_id) <= 128),
    signature TEXT NOT NULL CHECK (length(signature) >= 1 AND length(signature) <= 512),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(created_at) IS NOT NULL),
    CONSTRAINT ck_required_outcome_evidence CHECK (
        outcome NOT IN ('failed', 'timed_out', 'blocked', 'missed_during_offline', 'awaiting_operator_approval', 'orphaned', 'reconciled')
        OR evidence_digest IS NOT NULL
    ),
    CONSTRAINT uq_receipt_execution_attempt UNIQUE (execution_id, attempt),
    CONSTRAINT fk_receipt_execution_attempt FOREIGN KEY (execution_id, attempt) REFERENCES cron_execution_attempts(execution_id, attempt) ON DELETE RESTRICT
);
"""

_CREATE_CURSORS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_sweep_cursors (
    scope_key TEXT PRIMARY KEY CHECK (length(scope_key) >= 1 AND length(scope_key) <= 256),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    catch_up_policy_version INTEGER NOT NULL CHECK (typeof(catch_up_policy_version) = 'integer' AND catch_up_policy_version >= 1),
    cursor_value INTEGER NOT NULL CHECK (typeof(cursor_value) = 'integer' AND cursor_value >= 0),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z' AND julianday(updated_at) IS NOT NULL),
    CONSTRAINT uq_cron_sweep_scope UNIQUE (cron_id, registry_generation, command_digest, catch_up_policy_version)
);
"""

_TRIGGERS_DDL = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_evidence_no_update
    BEFORE UPDATE ON cron_evidence
    FOR EACH ROW
    BEGIN
        SELECT RAISE(FAIL, 'cron_evidence rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_evidence_no_delete
    BEFORE DELETE ON cron_evidence
    FOR EACH ROW
    BEGIN
        SELECT RAISE(FAIL, 'cron_evidence rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipts_no_update
    BEFORE UPDATE ON cron_receipts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(FAIL, 'cron_receipts rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipts_no_delete
    BEFORE DELETE ON cron_receipts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(FAIL, 'cron_receipts rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_sweep_cursors_prevent_regression
    BEFORE UPDATE ON cron_sweep_cursors
    FOR EACH ROW
    BEGIN
        SELECT CASE
            WHEN NEW.cursor_value < OLD.cursor_value THEN
                RAISE(FAIL, 'cursor_value regression rejected')
        END;
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_sweep_scope_immutable
    BEFORE UPDATE ON cron_sweep_cursors
    FOR EACH ROW
    WHEN NEW.scope_key IS NOT OLD.scope_key
      OR NEW.cron_id IS NOT OLD.cron_id
      OR NEW.registry_generation IS NOT OLD.registry_generation
      OR NEW.command_digest IS NOT OLD.command_digest
      OR NEW.catch_up_policy_version IS NOT OLD.catch_up_policy_version
    BEGIN
        SELECT RAISE(FAIL, 'cursor scope is immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_attempt_transition_guard
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN NEW.state != OLD.state AND NOT (
        (OLD.state = 'admitted' AND NEW.state IN ('claimed', 'terminal'))
        OR (OLD.state = 'claimed' AND NEW.state IN ('running', 'reconciling', 'terminal'))
        OR (OLD.state = 'running' AND NEW.state IN ('reconciling', 'terminal'))
        OR (OLD.state = 'reconciling' AND NEW.state = 'terminal')
    )
    BEGIN
        SELECT RAISE(FAIL, 'illegal attempt state transition');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_attempt_fence_guard
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN OLD.fence_token IS NOT NULL AND (
        NEW.fence_token IS NULL
        OR NEW.fence_token < OLD.fence_token
        OR (NEW.runner_id IS OLD.runner_id AND NEW.fence_token != OLD.fence_token)
        OR (NEW.runner_id IS NOT OLD.runner_id AND NEW.fence_token <= OLD.fence_token)
    )
    BEGIN
        SELECT RAISE(FAIL, 'attempt fence regression or invalid renewal');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_terminal_attempt_immutable
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN OLD.state = 'terminal'
    BEGIN
        SELECT RAISE(FAIL, 'terminal attempts are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipt_identity_guard
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
        SELECT RAISE(FAIL, 'receipt identity or terminal attempt mismatch');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_sweep_cursors_no_delete
    BEFORE DELETE ON cron_sweep_cursors
    FOR EACH ROW
    BEGIN
        SELECT RAISE(FAIL, 'cursor rows cannot be deleted');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_attempt_insert_guard
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
        SELECT RAISE(FAIL, 'attempts must start at 1, increment by 1, and cannot insert terminal');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_attempts_no_delete
    BEFORE DELETE ON cron_execution_attempts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(FAIL, 'attempt rows cannot be deleted');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_terminal_requires_receipt
    BEFORE UPDATE OF state ON cron_execution_attempts
    FOR EACH ROW
    WHEN NEW.state = 'terminal'
      AND OLD.state != 'terminal'
      AND NOT EXISTS (
          SELECT 1 FROM cron_receipts
          WHERE execution_id = NEW.execution_id AND attempt = NEW.attempt
      )
    BEGIN
        SELECT RAISE(FAIL, 'terminal transition requires matching receipt');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipt_finalizes_attempt
    AFTER INSERT ON cron_receipts
    FOR EACH ROW
    BEGIN
        UPDATE cron_execution_attempts
        SET state = 'terminal', updated_at = NEW.created_at
        WHERE execution_id = NEW.execution_id AND attempt = NEW.attempt;
    END;
    """,
]

_TRIGGER_NAMES = (
    "trg_cron_evidence_no_update",
    "trg_cron_evidence_no_delete",
    "trg_cron_receipts_no_update",
    "trg_cron_receipts_no_delete",
    "trg_cron_sweep_cursors_prevent_regression",
    "trg_cron_sweep_scope_immutable",
    "trg_cron_attempt_transition_guard",
    "trg_cron_attempt_fence_guard",
    "trg_cron_terminal_attempt_immutable",
    "trg_cron_receipt_identity_guard",
    "trg_cron_sweep_cursors_no_delete",
    "trg_cron_attempt_insert_guard",
    "trg_cron_attempts_no_delete",
    "trg_cron_terminal_requires_receipt",
    "trg_cron_receipt_finalizes_attempt",
)


def _normalize_ddl(sql: str) -> str:
    """Normalize SQLite-preserved DDL for exact object validation."""
    return "".join(sql.lower().replace("if not exists", "").replace(";", "").split())


def _validate_schema_objects(cursor: sqlite3.Cursor) -> None:
    expected = {
        ("table", "cron_authority_schema_version"): _CREATE_VERSION_TABLE_DDL,
        ("table", "cron_execution_aggregates"): _CREATE_AGGREGATES_TABLE_DDL,
        ("table", "cron_evidence"): _CREATE_EVIDENCE_TABLE_DDL,
        ("table", "cron_execution_attempts"): _CREATE_ATTEMPTS_TABLE_DDL,
        ("table", "cron_receipts"): _CREATE_RECEIPTS_TABLE_DDL,
        ("table", "cron_sweep_cursors"): _CREATE_CURSORS_TABLE_DDL,
    }
    expected.update(
        {
            ("trigger", name): ddl
            for name, ddl in zip(_TRIGGER_NAMES, _TRIGGERS_DDL, strict=True)
        }
    )
    for (object_type, name), expected_ddl in expected.items():
        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type=? AND name=?;",
            (object_type, name),
        ).fetchone()
        if (
            row is None
            or row[0] is None
            or _normalize_ddl(row[0]) != _normalize_ddl(expected_ddl)
        ):
            raise CronAuthorityError(
                f"Cron authority schema object mismatch: {object_type} {name}",
                code="schema_object_mismatch",
            )


def migrate_cron_authority(target: Any, timeout: float = 30.0) -> None:
    """Migrate SQLite database to cron authority schema v1 atomically and idempotently.

    - Target can be a string path, Path object, or sqlite3.Connection.
    - Uses `BEGIN IMMEDIATE` write transaction.
    - Rejects unknown or newer schema versions fail-closed.
    - Idempotent at schema version 1.
    - Preserves pre-existing tables and rows in the database.
    - Any error rolls back all migration statements completely.
    """
    close_connection_on_exit = not (
        hasattr(target, "cursor") and hasattr(target, "execute")
    )
    conn = connect_cron_authority(target, timeout=timeout)

    try:
        # Atomic write lock
        conn.execute("BEGIN IMMEDIATE;")

        # Check existing version table
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_authority_schema_version';"
        )
        version_table_exists = cursor.fetchone() is not None

        if version_table_exists:
            try:
                cursor.execute(
                    "SELECT authority_id, schema_version FROM cron_authority_schema_version;"
                )
            except sqlite3.DatabaseError as exc:
                raise CronAuthorityError(
                    "Unsupported cron authority schema version table",
                    code="unsupported_schema_version",
                ) from exc
            rows = cursor.fetchall()
            if rows != [(1, SCHEMA_VERSION)]:
                raise CronAuthorityError(
                    f"Unsupported cron authority schema version rows: {rows!r}",
                    code="unsupported_schema_version",
                )
            _validate_schema_objects(cursor)
            # Idempotent migration requires the exact authoritative v1 objects.
            conn.commit()
            return

        # Execute DDL
        cursor.execute(_CREATE_VERSION_TABLE_DDL)
        cursor.execute(_CREATE_AGGREGATES_TABLE_DDL)
        cursor.execute(_CREATE_EVIDENCE_TABLE_DDL)
        cursor.execute(_CREATE_ATTEMPTS_TABLE_DDL)
        cursor.execute(_CREATE_RECEIPTS_TABLE_DDL)
        cursor.execute(_CREATE_CURSORS_TABLE_DDL)

        for trigger_sql in _TRIGGERS_DDL:
            cursor.execute(trigger_sql)

        _validate_schema_objects(cursor)

        # Record schema version
        now_utc = datetime.now(timezone.utc).isoformat()
        if now_utc.endswith("+00:00"):
            now_utc = now_utc[:-6] + "Z"

        cursor.execute(
            "INSERT INTO cron_authority_schema_version (authority_id, schema_version, installed_at) VALUES (1, ?, ?);",
            (SCHEMA_VERSION, now_utc),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if close_connection_on_exit:
            conn.close()
