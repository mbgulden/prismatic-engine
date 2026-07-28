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
    """Create or configure a sqlite3 Connection for cron authority operations.

    Enforces `PRAGMA foreign_keys = ON;` and sets specified timeout.
    """
    resolved = resolve_db_target(target)
    if hasattr(resolved, "cursor") and hasattr(resolved, "execute"):
        conn = resolved
    else:
        conn = sqlite3.connect(resolved, timeout=timeout)

    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


_CREATE_VERSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_authority_schema_version (
    schema_version INTEGER PRIMARY KEY,
    installed_at TEXT NOT NULL CHECK (installed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z')
);
"""

_CREATE_AGGREGATES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_execution_aggregates (
    execution_id TEXT PRIMARY KEY CHECK (length(execution_id) >= 1 AND length(execution_id) <= 128),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (registry_generation >= 1),
    schedule_bucket TEXT NOT NULL CHECK (schedule_bucket GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    CONSTRAINT uq_cron_aggregate UNIQUE (cron_id, registry_generation, schedule_bucket, command_digest)
);
"""

_CREATE_EVIDENCE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_evidence (
    evidence_digest TEXT PRIMARY KEY CHECK (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (length(canonical_bytes) <= 4000),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z')
);
"""

_CREATE_ATTEMPTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_execution_attempts (
    execution_id TEXT NOT NULL REFERENCES cron_execution_aggregates(execution_id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    state TEXT NOT NULL CHECK (state IN ('admitted', 'claimed', 'running', 'reconciling', 'terminal')),
    runner_id TEXT CHECK (runner_id IS NULL OR (length(runner_id) >= 1 AND length(runner_id) <= 128)),
    fence_token INTEGER CHECK (fence_token IS NULL OR fence_token > 0),
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR lease_expires_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    PRIMARY KEY (execution_id, attempt)
);
"""

_CREATE_RECEIPTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_receipts (
    receipt_id TEXT PRIMARY KEY CHECK (length(receipt_id) >= 1 AND length(receipt_id) <= 128),
    execution_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'timed_out', 'cancelled', 'blocked', 'missed_during_offline', 'awaiting_operator_approval', 'orphaned', 'reconciled')),
    runner_id TEXT NOT NULL CHECK (length(runner_id) >= 1 AND length(runner_id) <= 128),
    runner_release_digest TEXT NOT NULL CHECK (length(runner_release_digest) = 64 AND runner_release_digest NOT GLOB '*[^0-9a-f]*'),
    started_at TEXT NOT NULL CHECK (started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    finished_at TEXT NOT NULL CHECK (finished_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    error_classification TEXT CHECK (error_classification IS NULL OR length(error_classification) <= 128),
    evidence_digest TEXT REFERENCES cron_evidence(evidence_digest) ON DELETE RESTRICT CHECK (evidence_digest IS NULL OR (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*')),
    signing_key_id TEXT NOT NULL CHECK (length(signing_key_id) >= 1 AND length(signing_key_id) <= 128),
    signature TEXT NOT NULL CHECK (length(signature) >= 1 AND length(signature) <= 512),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z'),
    CONSTRAINT uq_receipt_execution_attempt UNIQUE (execution_id, attempt),
    CONSTRAINT fk_receipt_execution_attempt FOREIGN KEY (execution_id, attempt) REFERENCES cron_execution_attempts(execution_id, attempt) ON DELETE RESTRICT
);
"""

_CREATE_CURSORS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_sweep_cursors (
    scope_key TEXT PRIMARY KEY CHECK (length(scope_key) >= 1 AND length(scope_key) <= 256),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (registry_generation >= 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    catch_up_policy_version INTEGER NOT NULL CHECK (catch_up_policy_version >= 1),
    cursor_value TEXT NOT NULL CHECK (length(cursor_value) >= 1 AND length(cursor_value) <= 128),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*Z')
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
]


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
            cursor.execute(
                "SELECT schema_version FROM cron_authority_schema_version ORDER BY schema_version DESC LIMIT 1;"
            )
            row = cursor.fetchone()
            if row is not None:
                existing_version = row[0]
                if existing_version > SCHEMA_VERSION:
                    raise CronAuthorityError(
                        f"Unsupported cron authority schema version: {existing_version} > {SCHEMA_VERSION}",
                        code="unsupported_schema_version",
                    )
                if existing_version == SCHEMA_VERSION:
                    # Idempotent migration; version 1 already installed
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

        # Record schema version
        now_utc = datetime.now(timezone.utc).isoformat()
        if now_utc.endswith("+00:00"):
            now_utc = now_utc[:-6] + "Z"

        cursor.execute(
            "INSERT INTO cron_authority_schema_version (schema_version, installed_at) VALUES (?, ?);",
            (SCHEMA_VERSION, now_utc),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if close_connection_on_exit:
            conn.close()
