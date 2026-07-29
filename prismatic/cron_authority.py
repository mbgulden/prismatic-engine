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

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION: int = 2

REGISTRY_SOURCE_ID: str = "prismatic.cron-authority.sqlite/cron_registry_snapshots_v1"
REGISTRY_SCHEMA_ID: str = "prismatic.cron.registry-snapshot"
REGISTRY_SCHEMA_VERSION: int = 1
REGISTRY_DIGEST_DOMAIN: bytes = b"prismatic.cron.registry-snapshot.v1"
MAX_CANONICAL_BYTES: int = 1048576
MAX_LEASE_DURATION: float = 300.0

_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

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

VALID_TRIGGER_KINDS: frozenset[str] = frozenset(
    {"scheduled", "manual", "retry", "hook", "recovery"}
)

VALID_TRANSPORT_KINDS: frozenset[str] = frozenset(
    {"http", "hook", "recovery", "internal"}
)

VALID_DISPOSITIONS: frozenset[str] = frozenset({"accepted", "rejected", "converged"})


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


_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


def _is_utc_timestamp(value: Any) -> int:
    """Return 1 only for strict, calendar-valid RFC3339 UTC timestamps."""
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return 0
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return 0
    return int(parsed.utcoffset() == timezone.utc.utcoffset(parsed))


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
    conn.execute("PRAGMA recursive_triggers = ON;")
    if conn.execute("PRAGMA recursive_triggers;").fetchone() != (1,):
        raise CronAuthorityError(
            "SQLite recursive triggers could not be enabled",
            code="recursive_triggers_unavailable",
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
    conn.create_function("is_utc_timestamp", 1, _is_utc_timestamp, deterministic=True)
    return conn


_CREATE_VERSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_authority_schema_version (
    authority_id INTEGER PRIMARY KEY CHECK (authority_id = 1),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version >= 1),
    installed_at TEXT NOT NULL CHECK (is_utc_timestamp(installed_at) = 1)
);
"""

_CREATE_AGGREGATES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_execution_aggregates (
    execution_id TEXT PRIMARY KEY CHECK (length(execution_id) >= 1 AND length(execution_id) <= 128),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    schedule_bucket TEXT NOT NULL CHECK (is_utc_timestamp(schedule_bucket) = 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    release_digest TEXT NOT NULL CHECK (length(release_digest) = 64 AND release_digest NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    CONSTRAINT uq_cron_aggregate UNIQUE (cron_id, registry_generation, schedule_bucket, command_digest)
);
"""

_CREATE_EVIDENCE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_evidence (
    evidence_digest TEXT PRIMARY KEY CHECK (length(evidence_digest) = 64 AND evidence_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 4000),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
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
    lease_expires_at TEXT CHECK (lease_expires_at IS NULL OR is_utc_timestamp(lease_expires_at) = 1),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),
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
"""

_CREATE_CURSORS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_sweep_cursors (
    scope_key TEXT PRIMARY KEY CHECK (length(scope_key) >= 1 AND length(scope_key) <= 256),
    cron_id TEXT NOT NULL CHECK (length(cron_id) >= 1 AND length(cron_id) <= 128),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    command_digest TEXT NOT NULL CHECK (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*'),
    catch_up_policy_version INTEGER NOT NULL CHECK (typeof(catch_up_policy_version) = 'integer' AND catch_up_policy_version >= 1),
    cursor_value INTEGER NOT NULL CHECK (typeof(cursor_value) = 'integer' AND cursor_value >= 0),
    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),
    CONSTRAINT uq_cron_sweep_scope UNIQUE (cron_id, registry_generation, command_digest, catch_up_policy_version)
);
"""

_CREATE_TRIGGER_DELIVERIES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_trigger_deliveries (
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
"""

_CREATE_SNAPSHOTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_registry_snapshots_v1 (
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
"""

_TRIGGERS_DDL = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_aggregate_insert_collision
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_aggregates_no_update
    BEFORE UPDATE ON cron_execution_aggregates
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron execution aggregates are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_aggregates_no_delete
    BEFORE DELETE ON cron_execution_aggregates
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron execution aggregates are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_evidence_insert_collision
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipt_insert_collision
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_cursor_insert_collision
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_evidence_no_update
    BEFORE UPDATE ON cron_evidence
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_evidence rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_evidence_no_delete
    BEFORE DELETE ON cron_evidence
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_evidence rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipts_no_update
    BEFORE UPDATE ON cron_receipts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_receipts rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_receipts_no_delete
    BEFORE DELETE ON cron_receipts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_receipts rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_sweep_cursors_prevent_regression
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_sweep_scope_immutable
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
        SELECT RAISE(ABORT, 'illegal attempt state transition');
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
        SELECT RAISE(ABORT, 'attempt fence regression or invalid renewal');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_terminal_attempt_immutable
    BEFORE UPDATE ON cron_execution_attempts
    FOR EACH ROW
    WHEN OLD.state = 'terminal'
    BEGIN
        SELECT RAISE(ABORT, 'terminal attempts are immutable');
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
        SELECT RAISE(ABORT, 'receipt identity or terminal attempt mismatch');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_sweep_cursors_no_delete
    BEFORE DELETE ON cron_sweep_cursors
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cursor rows cannot be deleted');
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
        SELECT RAISE(ABORT, 'attempts must start at 1, increment by 1, and cannot insert terminal');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_attempts_no_delete
    BEFORE DELETE ON cron_execution_attempts
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'attempt rows cannot be deleted');
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
        SELECT RAISE(ABORT, 'terminal transition requires matching receipt');
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

_V2_TRIGGERS_DDL = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_trigger_deliveries_insert_collision
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_trigger_deliveries_no_update
    BEFORE UPDATE ON cron_trigger_deliveries
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_trigger_deliveries_no_delete
    BEFORE DELETE ON cron_trigger_deliveries
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_trigger_deliveries rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_registry_snapshots_insert_collision
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
    CREATE TRIGGER IF NOT EXISTS trg_cron_registry_snapshots_no_update
    BEFORE UPDATE ON cron_registry_snapshots_v1
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_registry_snapshots_no_delete
    BEFORE DELETE ON cron_registry_snapshots_v1
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v1 rows cannot be deleted');
    END;
    """,
]

_V1_TRIGGER_NAMES = (
    "trg_cron_aggregate_insert_collision",
    "trg_cron_aggregates_no_update",
    "trg_cron_aggregates_no_delete",
    "trg_cron_evidence_insert_collision",
    "trg_cron_receipt_insert_collision",
    "trg_cron_cursor_insert_collision",
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

_V2_TRIGGER_NAMES = (
    "trg_cron_trigger_deliveries_insert_collision",
    "trg_cron_trigger_deliveries_no_update",
    "trg_cron_trigger_deliveries_no_delete",
    "trg_cron_registry_snapshots_insert_collision",
    "trg_cron_registry_snapshots_no_update",
    "trg_cron_registry_snapshots_no_delete",
)

_TRIGGER_NAMES = _V1_TRIGGER_NAMES + _V2_TRIGGER_NAMES

_V1_TABLE_NAMES = (
    "cron_authority_schema_version",
    "cron_execution_aggregates",
    "cron_evidence",
    "cron_execution_attempts",
    "cron_receipts",
    "cron_sweep_cursors",
)

_V2_TABLE_NAMES = _V1_TABLE_NAMES + (
    "cron_trigger_deliveries",
    "cron_registry_snapshots_v1",
)
_TABLE_NAMES = _V2_TABLE_NAMES


def _main_ddl(sql: str) -> str:
    """Qualify authority DDL to the durable main schema."""
    for prefix in ("CREATE TABLE IF NOT EXISTS ", "CREATE TRIGGER IF NOT EXISTS "):
        if prefix in sql:
            return sql.replace(prefix, f"{prefix}main.", 1)
    raise CronAuthorityError("Unsupported authority DDL", code="schema_object_mismatch")


def _reject_temp_schema_collisions(cursor: sqlite3.Cursor) -> None:
    """Reject TEMP objects that could divert unqualified authority operations."""
    names = (*_TABLE_NAMES, *_TRIGGER_NAMES)
    placeholders = ",".join("?" for _ in names)
    rows = cursor.execute(
        f"SELECT type, name FROM temp.sqlite_master WHERE name IN ({placeholders});",
        names,
    ).fetchall()
    if rows:
        raise CronAuthorityError(
            f"TEMP schema collides with cron authority objects: {rows!r}",
            code="schema_object_mismatch",
        )


def _normalize_ddl(sql: str) -> str:
    """Normalize SQLite-preserved DDL for exact object validation."""
    return "".join(sql.lower().replace("if not exists", "").replace(";", "").split())


def _validate_schema_objects(cursor: sqlite3.Cursor, target_version: int = 2) -> None:
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
            for name, ddl in zip(_V1_TRIGGER_NAMES, _TRIGGERS_DDL, strict=True)
        }
    )
    if target_version >= 2:
        expected[("table", "cron_trigger_deliveries")] = (
            _CREATE_TRIGGER_DELIVERIES_TABLE_DDL
        )
        expected[("table", "cron_registry_snapshots_v1")] = _CREATE_SNAPSHOTS_TABLE_DDL
        expected.update(
            {
                ("trigger", name): ddl
                for name, ddl in zip(_V2_TRIGGER_NAMES, _V2_TRIGGERS_DDL, strict=True)
            }
        )

    for (object_type, name), expected_ddl in expected.items():
        row = cursor.execute(
            "SELECT sql FROM main.sqlite_master WHERE type=? AND name=?;",
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
    """Migrate SQLite database to cron authority schema v2 atomically and idempotently."""
    close_connection_on_exit = not (
        hasattr(target, "cursor") and hasattr(target, "execute")
    )
    conn = connect_cron_authority(target, timeout=timeout)

    try:
        # Atomic write lock
        conn.execute("BEGIN IMMEDIATE;")

        # Check existing version table
        cursor = conn.cursor()
        _reject_temp_schema_collisions(cursor)
        cursor.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name='cron_authority_schema_version';"
        )
        version_table_exists = cursor.fetchone() is not None

        if version_table_exists:
            try:
                cursor.execute(
                    "SELECT authority_id, schema_version FROM main.cron_authority_schema_version;"
                )
            except sqlite3.DatabaseError as exc:
                raise CronAuthorityError(
                    "Unsupported cron authority schema version table",
                    code="unsupported_schema_version",
                ) from exc
            rows = cursor.fetchall()
            if rows == [(1, 2)]:
                _validate_schema_objects(cursor, target_version=2)
                conn.commit()
                return
            elif rows == [(1, 1)]:
                _validate_schema_objects(cursor, target_version=1)
                cursor.execute(_main_ddl(_CREATE_TRIGGER_DELIVERIES_TABLE_DDL))
                cursor.execute(_main_ddl(_CREATE_SNAPSHOTS_TABLE_DDL))
                for trigger_sql in _V2_TRIGGERS_DDL:
                    cursor.execute(_main_ddl(trigger_sql))
                cursor.execute(
                    "UPDATE main.cron_authority_schema_version SET schema_version = 2 WHERE authority_id = 1;"
                )
                _validate_schema_objects(cursor, target_version=2)
                conn.commit()
                return
            else:
                raise CronAuthorityError(
                    f"Unsupported cron authority schema version rows: {rows!r}",
                    code="unsupported_schema_version",
                )

        # Fresh DB migration directly to v2
        cursor.execute(_main_ddl(_CREATE_VERSION_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_AGGREGATES_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_EVIDENCE_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_ATTEMPTS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_RECEIPTS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_CURSORS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_TRIGGER_DELIVERIES_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_SNAPSHOTS_TABLE_DDL))

        for trigger_sql in _TRIGGERS_DDL:
            cursor.execute(_main_ddl(trigger_sql))
        for trigger_sql in _V2_TRIGGERS_DDL:
            cursor.execute(_main_ddl(trigger_sql))

        _validate_schema_objects(cursor, target_version=2)

        # Record schema version
        now_utc = datetime.now(timezone.utc).isoformat()
        if now_utc.endswith("+00:00"):
            now_utc = now_utc[:-6] + "Z"

        cursor.execute(
            "INSERT INTO main.cron_authority_schema_version (authority_id, schema_version, installed_at) VALUES (1, ?, ?);",
            (SCHEMA_VERSION, now_utc),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if close_connection_on_exit:
            conn.close()


class CronAuthorityStore:
    """Normative transactional store interface for cron authority objects."""

    REGISTRY_SOURCE_ID = REGISTRY_SOURCE_ID
    REGISTRY_SCHEMA_ID = REGISTRY_SCHEMA_ID
    REGISTRY_SCHEMA_VERSION = REGISTRY_SCHEMA_VERSION

    def __init__(self, db_target: Any, timeout: float = 30.0) -> None:
        self.db_target = db_target
        self.timeout = timeout

    @classmethod
    def verify_pinned_db_path(cls, db_target: Any) -> Any:
        if hasattr(db_target, "cursor") and hasattr(db_target, "execute"):
            return db_target
        target_str = str(db_target)
        if target_str == ":memory:" or target_str.startswith("file:"):
            return target_str

        p = Path(target_str).resolve()
        if str(p) != os.path.realpath(target_str):
            raise CronAuthorityError(
                f"Symlink in database path rejected: {target_str!r}",
                code="symlink_rejected",
            )

        if p.exists():
            st = os.lstat(p)
            if (st.st_mode & 0o077) != 0:
                with contextlib.suppress(OSError):
                    os.chmod(p, st.st_mode & ~0o077)
                    st = os.lstat(p)
            if (st.st_mode & 0o077) != 0:
                raise CronAuthorityError(
                    f"Database permissions must be 0600 or stricter: {oct(st.st_mode)}",
                    code="insecure_db_permissions",
                )
            if st.st_uid not in (0, os.getuid()):
                raise CronAuthorityError(
                    f"Database owner mismatch: {st.st_uid}",
                    code="insecure_db_owner",
                )

        curr = p.parent
        while curr != curr.parent:
            if curr.exists():
                st_p = os.lstat(curr)
                if (st_p.st_mode & 0o1000) == 0 and (st_p.st_mode & 0o022) != 0:
                    raise CronAuthorityError(
                        f"Parent directory group/world writable: {curr}",
                        code="insecure_parent_permissions",
                    )
            curr = curr.parent

        return str(p)

    @classmethod
    def compute_snapshot_digest(cls, canonical_bytes: bytes) -> str:
        return hashlib.sha256(
            REGISTRY_DIGEST_DOMAIN + b"\x00" + canonical_bytes
        ).hexdigest()

    @classmethod
    def install_registry_snapshot_v1(
        cls,
        db_target: Any,
        registry_generation: int,
        snapshot_data: dict[str, Any] | bytes | str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        cls.verify_pinned_db_path(db_target)
        if type(registry_generation) is not int or registry_generation < 1:
            raise CronAuthorityError(
                "registry_generation must be int >= 1",
                code="invalid_registry_generation",
            )

        canonical_bytes, parsed_dict = cls._canonicalize_and_validate_snapshot(
            snapshot_data, expected_generation=registry_generation
        )
        snapshot_digest = cls.compute_snapshot_digest(canonical_bytes)

        migrate_cron_authority(db_target, timeout=timeout)
        conn = connect_cron_authority(db_target, timeout=timeout)
        close_conn = not hasattr(db_target, "cursor")

        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()
            now_utc = datetime.now(timezone.utc).isoformat()
            if now_utc.endswith("+00:00"):
                now_utc = now_utc[:-6] + "Z"

            row = cursor.execute(
                """
                SELECT snapshot_digest, canonical_bytes FROM main.cron_registry_snapshots_v1
                WHERE source_id = ? AND (registry_generation = ? OR snapshot_digest = ?);
                """,
                (REGISTRY_SOURCE_ID, registry_generation, snapshot_digest),
            ).fetchone()

            if row is not None:
                ex_digest, ex_bytes = row
                if ex_digest == snapshot_digest and bytes(ex_bytes) == canonical_bytes:
                    conn.commit()
                    return {
                        "status": "converged",
                        "source_id": REGISTRY_SOURCE_ID,
                        "registry_generation": registry_generation,
                        "snapshot_digest": snapshot_digest,
                        "canonical_bytes": canonical_bytes,
                        "parsed": parsed_dict,
                    }
                else:
                    conn.rollback()
                    raise CronAuthorityError(
                        f"Conflicting snapshot insertion for generation {registry_generation}",
                        code="snapshot_conflict",
                    )

            cursor.execute(
                """
                INSERT INTO main.cron_registry_snapshots_v1 (
                    source_id, schema_id, schema_version, registry_generation, snapshot_digest, canonical_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    REGISTRY_SOURCE_ID,
                    REGISTRY_SCHEMA_ID,
                    REGISTRY_SCHEMA_VERSION,
                    registry_generation,
                    snapshot_digest,
                    canonical_bytes,
                    now_utc,
                ),
            )
            conn.commit()
            return {
                "status": "installed",
                "source_id": REGISTRY_SOURCE_ID,
                "registry_generation": registry_generation,
                "snapshot_digest": snapshot_digest,
                "canonical_bytes": canonical_bytes,
                "parsed": parsed_dict,
            }
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            if close_conn:
                conn.close()

    @classmethod
    def read_registry_snapshot_v1(
        cls,
        db_target: Any,
        source_id: str,
        registry_generation: int,
        snapshot_digest: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        cls.verify_pinned_db_path(db_target)
        if source_id != REGISTRY_SOURCE_ID:
            raise CronAuthorityError(
                f"Invalid source_id: {source_id!r}", code="invalid_source_id"
            )
        if type(registry_generation) is not int or registry_generation < 1:
            raise CronAuthorityError(
                "registry_generation must be int >= 1",
                code="invalid_registry_generation",
            )
        if (
            not isinstance(snapshot_digest, str)
            or _HEX64_PATTERN.fullmatch(snapshot_digest) is None
        ):
            raise CronAuthorityError(
                "snapshot_digest must be 64 lowercase hex",
                code="invalid_snapshot_digest",
            )

        migrate_cron_authority(db_target, timeout=timeout)
        conn = connect_cron_authority(db_target, timeout=timeout)
        close_conn = not hasattr(db_target, "cursor")

        try:
            row = conn.execute(
                """
                SELECT schema_id, schema_version, canonical_bytes FROM main.cron_registry_snapshots_v1
                WHERE source_id = ? AND registry_generation = ? AND snapshot_digest = ?;
                """,
                (source_id, registry_generation, snapshot_digest),
            ).fetchone()

            if row is None:
                raise CronAuthorityError(
                    f"Registry snapshot not found for gen={registry_generation}, digest={snapshot_digest}",
                    code="snapshot_not_found",
                )

            s_id, s_ver, c_bytes_blob = row
            if s_id != REGISTRY_SCHEMA_ID or s_ver != REGISTRY_SCHEMA_VERSION:
                raise CronAuthorityError(
                    "Registry snapshot schema mismatch", code="schema_mismatch"
                )

            c_bytes = bytes(c_bytes_blob)
            computed_digest = cls.compute_snapshot_digest(c_bytes)
            if computed_digest != snapshot_digest:
                raise CronAuthorityError(
                    "Persisted snapshot digest mismatch",
                    code="snapshot_digest_mismatch",
                )

            parsed = cls._parse_and_validate_canonical_bytes(c_bytes)
            return parsed
        finally:
            if close_conn:
                conn.close()

    @classmethod
    def renew_execution_lease(
        cls,
        db_target: Any,
        execution_id: str,
        schedule_bucket: str,
        attempt: int,
        runner_id: str,
        fence_token: int,
        duration_seconds: float,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        cls.verify_pinned_db_path(db_target)
        if not isinstance(execution_id, str) or not (1 <= len(execution_id) <= 128):
            raise CronAuthorityError(
                "execution_id must be 1..128 chars", code="invalid_execution_id"
            )
        if not _is_utc_timestamp(schedule_bucket):
            raise CronAuthorityError(
                f"Invalid schedule_bucket: {schedule_bucket!r}",
                code="invalid_schedule_bucket",
            )
        if type(attempt) is not int or attempt < 1:
            raise CronAuthorityError("attempt must be int >= 1", code="invalid_attempt")
        if not isinstance(runner_id, str) or not (1 <= len(runner_id) <= 128):
            raise CronAuthorityError(
                "runner_id must be 1..128 chars", code="invalid_runner_id"
            )
        if type(fence_token) is not int or fence_token < 1:
            raise CronAuthorityError(
                "fence_token must be int >= 1", code="invalid_fence_token"
            )
        if (
            isinstance(duration_seconds, bool)
            or type(duration_seconds) not in (int, float)
            or duration_seconds <= 0
            or duration_seconds > MAX_LEASE_DURATION
        ):
            raise CronAuthorityError(
                f"duration_seconds must be a positive non-boolean number <= {MAX_LEASE_DURATION}",
                code="invalid_duration",
            )

        migrate_cron_authority(db_target, timeout=timeout)
        conn = connect_cron_authority(db_target, timeout=timeout)
        close_conn = not hasattr(db_target, "cursor")

        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()

            row_agg = cursor.execute(
                "SELECT schedule_bucket FROM main.cron_execution_aggregates WHERE execution_id = ?;",
                (execution_id,),
            ).fetchone()
            if row_agg is None or row_agg[0] != schedule_bucket:
                conn.rollback()
                raise CronAuthorityError(
                    "Schedule bucket or aggregate identity mismatch",
                    code="identity_mismatch",
                )

            row_att = cursor.execute(
                "SELECT state, runner_id, fence_token FROM main.cron_execution_attempts WHERE execution_id = ? AND attempt = ?;",
                (execution_id, attempt),
            ).fetchone()
            if row_att is None:
                conn.rollback()
                raise CronAuthorityError("Attempt not found", code="attempt_not_found")

            state, cur_runner, cur_fence = row_att
            if state not in ("claimed", "running"):
                conn.rollback()
                raise CronAuthorityError(
                    f"Cannot renew lease for attempt in state {state!r}",
                    code="non_renewable_state",
                )
            if cur_runner != runner_id or cur_fence != fence_token:
                conn.rollback()
                raise CronAuthorityError(
                    "Stale owner or fence mismatch", code="stale_owner_or_fence"
                )

            now_dt = datetime.now(timezone.utc)
            new_expires_dt = datetime.fromtimestamp(
                now_dt.timestamp() + float(duration_seconds), timezone.utc
            )
            new_expires_at = new_expires_dt.isoformat()
            if new_expires_at.endswith("+00:00"):
                new_expires_at = new_expires_at[:-6] + "Z"
            now_utc = datetime.now(timezone.utc).isoformat()
            if now_utc.endswith("+00:00"):
                now_utc = now_utc[:-6] + "Z"

            cursor.execute(
                """
                UPDATE main.cron_execution_attempts
                SET lease_expires_at = ?, updated_at = ?
                WHERE execution_id = ? AND attempt = ? AND runner_id = ? AND fence_token = ? AND state IN ('claimed', 'running');
                """,
                (
                    new_expires_at,
                    now_utc,
                    execution_id,
                    attempt,
                    runner_id,
                    fence_token,
                ),
            )
            conn.commit()
            return {
                "status": "renewed",
                "execution_id": execution_id,
                "attempt": attempt,
                "runner_id": runner_id,
                "fence_token": fence_token,
                "lease_expires_at": new_expires_at,
            }
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            if close_conn:
                conn.close()

    @classmethod
    def finalize_execution_receipt(
        cls,
        db_target: Any,
        receipt_material: Any,
        schema_version: int = 1,
        pre_spawn_snapshot: Any | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        cls.verify_pinned_db_path(db_target)
        if type(schema_version) is not int or schema_version != 1:
            raise CronAuthorityError(
                "schema_version must be non-boolean int 1",
                code="invalid_schema_version",
            )

        from prismatic.cron_receipts.schema import CronRunReceipt

        if isinstance(receipt_material, CronRunReceipt):
            rcpt = receipt_material
            rcpt.validate()
            c_dict = rcpt.to_dict()
            c_bytes = json.dumps(c_dict, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        elif isinstance(receipt_material, bytes):
            c_bytes = receipt_material
            if len(c_bytes) > MAX_CANONICAL_BYTES:
                raise CronAuthorityError(
                    "Receipt canonical bytes exceed limit", code="oversize_receipt"
                )
            if c_bytes.startswith(b"\xef\xbb\xbf"):
                raise CronAuthorityError(
                    "BOM prefix rejected in receipt", code="bom_rejected"
                )
            try:
                parsed = json.loads(c_bytes.decode("utf-8"))
            except Exception as exc:
                raise CronAuthorityError(
                    f"Malformed receipt bytes: {exc}", code="malformed_receipt"
                ) from exc
            rcpt = CronRunReceipt.from_dict(parsed)
            rcpt.validate()
            re_encoded = json.dumps(
                rcpt.to_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if re_encoded != c_bytes:
                raise CronAuthorityError(
                    "Non-canonical receipt JSON encoding", code="non_canonical_receipt"
                )
        elif isinstance(receipt_material, dict):
            rcpt = CronRunReceipt.from_dict(receipt_material)
            rcpt.validate()
            c_bytes = json.dumps(
                rcpt.to_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        else:
            raise CronAuthorityError(
                "Unsupported receipt material type", code="invalid_receipt_material"
            )

        migrate_cron_authority(db_target, timeout=timeout)
        conn = connect_cron_authority(db_target, timeout=timeout)
        close_conn = not hasattr(db_target, "cursor")

        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()

            row_agg = cursor.execute(
                "SELECT cron_id, registry_generation, schedule_bucket, command_digest, release_digest FROM main.cron_execution_aggregates WHERE execution_id = ?;",
                (rcpt.execution_id,),
            ).fetchone()
            if row_agg is None:
                conn.rollback()
                raise CronAuthorityError(
                    f"Execution aggregate not found: {rcpt.execution_id}",
                    code="aggregate_not_found",
                )

            agg_cron_id, agg_reg_gen, _agg_bucket, agg_cmd_dig, agg_rel_dig = row_agg
            if rcpt.cron_id != agg_cron_id:
                conn.rollback()
                raise CronAuthorityError(
                    "Receipt cron_id mismatch with aggregate",
                    code="cross_binding_mismatch",
                )

            row_att = cursor.execute(
                "SELECT state, runner_id, fence_token FROM main.cron_execution_attempts WHERE execution_id = ? AND attempt = ?;",
                (rcpt.execution_id, rcpt.attempt),
            ).fetchone()
            if row_att is None:
                conn.rollback()
                raise CronAuthorityError(
                    "Attempt not found for receipt", code="attempt_not_found"
                )

            _att_state, att_runner_id, _att_fence = row_att
            if rcpt.runner_id != att_runner_id:
                conn.rollback()
                raise CronAuthorityError(
                    "Receipt runner_id mismatch with attempt owner",
                    code="stale_owner_or_fence",
                )

            if pre_spawn_snapshot is not None and (
                pre_spawn_snapshot.command_digest != agg_cmd_dig
                or pre_spawn_snapshot.release_digest != agg_rel_dig
                or pre_spawn_snapshot.cron_id != agg_cron_id
                or pre_spawn_snapshot.registry_generation != agg_reg_gen
            ):
                conn.rollback()
                raise CronAuthorityError(
                    "Pre-spawn snapshot mismatch during finalization",
                    code="pre_spawn_snapshot_mismatch",
                )

            row_existing = cursor.execute(
                "SELECT receipt_id, outcome, runner_id, runner_release_digest, evidence_digest FROM main.cron_receipts WHERE execution_id = ? AND attempt = ?;",
                (rcpt.execution_id, rcpt.attempt),
            ).fetchone()

            now_utc = datetime.now(timezone.utc).isoformat()
            if now_utc.endswith("+00:00"):
                now_utc = now_utc[:-6] + "Z"

            if row_existing is not None:
                ex_rcpt_id, ex_outcome, ex_runner, ex_runner_rel, ex_ev_dig = (
                    row_existing
                )
                if (
                    ex_rcpt_id == rcpt.receipt_id
                    and ex_outcome == rcpt.outcome
                    and ex_runner == rcpt.runner_id
                    and ex_runner_rel == rcpt.runner_release_digest
                    and ex_ev_dig == rcpt.evidence_digest
                ):
                    conn.commit()
                    return {
                        "status": "converged",
                        "receipt_id": rcpt.receipt_id,
                        "execution_id": rcpt.execution_id,
                        "attempt": rcpt.attempt,
                        "outcome": rcpt.outcome,
                    }
                else:
                    conn.rollback()
                    raise CronAuthorityError(
                        "Receipt collision with changed payload",
                        code="receipt_collision",
                    )

            cursor.execute(
                """
                INSERT INTO main.cron_receipts (
                    receipt_id, execution_id, attempt, cron_id, outcome, runner_id, runner_release_digest,
                    started_at, finished_at, error_classification, evidence_digest, signing_key_id, signature,
                    schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
                """,
                (
                    rcpt.receipt_id,
                    rcpt.execution_id,
                    rcpt.attempt,
                    rcpt.cron_id,
                    rcpt.outcome,
                    rcpt.runner_id,
                    rcpt.runner_release_digest,
                    rcpt.started_at,
                    rcpt.finished_at,
                    rcpt.error_classification,
                    rcpt.evidence_digest,
                    rcpt.signing_key_id,
                    rcpt.signature,
                    now_utc,
                ),
            )
            conn.commit()
            return {
                "status": "finalized",
                "receipt_id": rcpt.receipt_id,
                "execution_id": rcpt.execution_id,
                "attempt": rcpt.attempt,
                "outcome": rcpt.outcome,
            }
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            if close_conn:
                conn.close()

    @classmethod
    def _canonicalize_and_validate_snapshot(
        cls, snapshot_data: dict[str, Any] | bytes | str, expected_generation: int
    ) -> tuple[bytes, dict[str, Any]]:
        if isinstance(snapshot_data, (bytes, str)):
            raw_bytes = (
                snapshot_data.encode("utf-8")
                if isinstance(snapshot_data, str)
                else snapshot_data
            )
            parsed = cls._parse_and_validate_canonical_bytes(raw_bytes)
        elif isinstance(snapshot_data, dict):
            parsed = dict(snapshot_data)
        else:
            raise CronAuthorityError(
                "Invalid snapshot data type", code="invalid_snapshot"
            )

        REQUIRED_KEYS = frozenset(
            {
                "schema_id",
                "schema_version",
                "source_id",
                "cron_id",
                "registry_generation",
                "trusted_runner_identity",
                "command_digest",
                "release_digest",
                "dependency_digest",
                "release_root",
                "release_root_evidence",
                "argv",
                "executable_evidence",
                "cwd",
                "cwd_evidence",
                "state",
                "depends_on",
                "catch_up_policy",
                "max_replay_buckets",
            }
        )
        if set(parsed.keys()) != REQUIRED_KEYS:
            raise CronAuthorityError(
                "Snapshot top-level keys mismatch", code="invalid_snapshot_keys"
            )

        if parsed["source_id"] != REGISTRY_SOURCE_ID:
            raise CronAuthorityError(
                f"Invalid source_id: {parsed['source_id']!r}",
                code="invalid_source_id",
            )
        if parsed["schema_id"] != REGISTRY_SCHEMA_ID:
            raise CronAuthorityError(
                f"Invalid schema_id: {parsed['schema_id']!r}",
                code="invalid_schema_id",
            )
        if type(parsed["schema_version"]) is not int or parsed["schema_version"] != 1:
            raise CronAuthorityError(
                "schema_version must be non-boolean int 1",
                code="invalid_schema_version",
            )
        if (
            type(parsed["registry_generation"]) is not int
            or parsed["registry_generation"] != expected_generation
        ):
            raise CronAuthorityError(
                "registry_generation mismatch", code="registry_generation_mismatch"
            )

        canonical_bytes = json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(canonical_bytes) > MAX_CANONICAL_BYTES:
            raise CronAuthorityError(
                "Canonical snapshot bytes exceed 1MB limit", code="oversize_snapshot"
            )

        return canonical_bytes, parsed

    @classmethod
    def _parse_and_validate_canonical_bytes(cls, raw_bytes: bytes) -> dict[str, Any]:
        if len(raw_bytes) > MAX_CANONICAL_BYTES:
            raise CronAuthorityError(
                "Canonical snapshot bytes exceed 1MB limit", code="oversize_snapshot"
            )
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            raise CronAuthorityError("BOM prefix rejected", code="bom_rejected")

        def _reject_dup(pairs):
            d = {}
            for k, v in pairs:
                if k in d:
                    raise CronAuthorityError(
                        f"Duplicate JSON key: {k!r}", code="duplicate_json_key"
                    )
                d[k] = v
            return d

        def _reject_float(val):
            raise CronAuthorityError(
                "Floats forbidden in snapshot", code="float_rejected"
            )

        try:
            parsed = json.loads(
                raw_bytes.decode("utf-8"),
                object_pairs_hook=_reject_dup,
                parse_float=_reject_float,
            )
        except Exception as exc:
            raise CronAuthorityError(
                f"Invalid UTF-8 or JSON syntax in snapshot: {exc}",
                code="malformed_json",
            ) from exc

        re_encoded = json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if re_encoded != raw_bytes:
            raise CronAuthorityError(
                "Non-canonical UTF-8 JSON encoding in snapshot",
                code="non_canonical_encoding",
            )

        return parsed
