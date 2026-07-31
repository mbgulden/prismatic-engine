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
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION: int = 3

REGISTRY_SOURCE_ID: str = "prismatic.cron-authority.sqlite/cron_registry_snapshots_v1"
REGISTRY_SOURCE_ID_V2: str = (
    "prismatic.cron-authority.sqlite/cron_registry_snapshots_v2"
)
REGISTRY_SCHEMA_ID: str = "prismatic.cron.registry-snapshot"
REGISTRY_SCHEMA_VERSION: int = 1
REGISTRY_SCHEMA_VERSION_V2: int = 2
REGISTRY_DIGEST_DOMAIN: bytes = b"prismatic.cron.registry-snapshot.v1"
REGISTRY_DIGEST_DOMAIN_V2: bytes = b"prismatic.cron.registry-snapshot.v2"
MAX_CANONICAL_BYTES: int = 1048576
MAX_LEASE_DURATION: float = 300.0

_TRUSTED_RELEASE_PARENT: Path = Path("/home/ubuntu/.prismatic/releases")


def get_trusted_release_parent() -> Path:
    return _TRUSTED_RELEASE_PARENT


def set_trusted_release_parent(path: Path | str) -> Path:
    global _TRUSTED_RELEASE_PARENT
    _TRUSTED_RELEASE_PARENT = Path(path).absolute()
    return _TRUSTED_RELEASE_PARENT


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

    def __init__(
        self,
        message: str,
        code: str = "cron_authority_error",
        context: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = context or {}


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
    source_id TEXT CHECK (source_id IS NULL OR source_id IN ('prismatic.cron-authority.sqlite/cron_registry_snapshots_v1', 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v2')),
    schema_id TEXT CHECK (schema_id IS NULL OR schema_id = 'prismatic.cron.registry-snapshot'),
    schema_version INTEGER CHECK (schema_version IS NULL OR schema_version IN (1, 2)),
    registry_generation INTEGER CHECK (registry_generation IS NULL OR registry_generation >= 1),
    snapshot_digest TEXT CHECK (snapshot_digest IS NULL OR (length(snapshot_digest) = 64 AND snapshot_digest NOT GLOB '*[^0-9a-f]*')),
    canonical_snapshot_bytes BLOB CHECK (canonical_snapshot_bytes IS NULL OR typeof(canonical_snapshot_bytes) = 'blob'),
    trusted_runner_identity TEXT CHECK (trusted_runner_identity IS NULL OR length(trusted_runner_identity) >= 1),
    command_digest TEXT CHECK (command_digest IS NULL OR (length(command_digest) = 64 AND command_digest NOT GLOB '*[^0-9a-f]*')),
    release_digest TEXT CHECK (release_digest IS NULL OR (length(release_digest) = 64 AND release_digest NOT GLOB '*[^0-9a-f]*')),
    dependency_digest TEXT CHECK (dependency_digest IS NULL OR (length(dependency_digest) = 64 AND dependency_digest NOT GLOB '*[^0-9a-f]*')),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    updated_at TEXT NOT NULL CHECK (is_utc_timestamp(updated_at) = 1),
    CONSTRAINT ck_attempt_ownership CHECK (
        state IN ('admitted', 'terminal')
        OR (runner_id IS NOT NULL AND fence_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT ck_attempt_coherent_snapshot CHECK (
        (source_id IS NULL AND schema_version IS NULL AND snapshot_digest IS NULL AND canonical_snapshot_bytes IS NULL AND registry_generation IS NULL AND schema_id IS NULL)
        OR (source_id = 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v1' AND schema_version = 1 AND schema_id = 'prismatic.cron.registry-snapshot' AND snapshot_digest IS NOT NULL AND canonical_snapshot_bytes IS NOT NULL AND registry_generation IS NOT NULL)
        OR (source_id = 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v2' AND schema_version = 2 AND schema_id = 'prismatic.cron.registry-snapshot' AND snapshot_digest IS NOT NULL AND canonical_snapshot_bytes IS NOT NULL AND registry_generation IS NOT NULL)
    ),
    PRIMARY KEY (execution_id, attempt)
);
"""

_CREATE_V1_ATTEMPTS_TABLE_DDL = """
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

_CREATE_SNAPSHOTS_V2_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cron_registry_snapshots_v2 (
    source_id TEXT NOT NULL CHECK (source_id = 'prismatic.cron-authority.sqlite/cron_registry_snapshots_v2'),
    schema_id TEXT NOT NULL CHECK (schema_id = 'prismatic.cron.registry-snapshot'),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer' AND schema_version = 2),
    registry_generation INTEGER NOT NULL CHECK (typeof(registry_generation) = 'integer' AND registry_generation >= 1),
    snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64 AND snapshot_digest NOT GLOB '*[^0-9a-f]*'),
    canonical_bytes BLOB NOT NULL CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 1048576),
    created_at TEXT NOT NULL CHECK (is_utc_timestamp(created_at) = 1),
    PRIMARY KEY (source_id, registry_generation),
    CONSTRAINT uq_snapshot_v2_digest UNIQUE (source_id, snapshot_digest)
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

_V3_TRIGGERS_DDL = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_registry_snapshots_v2_insert_collision
    BEFORE INSERT ON cron_registry_snapshots_v2
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM cron_registry_snapshots_v2
        WHERE (source_id = NEW.source_id AND registry_generation = NEW.registry_generation AND (snapshot_digest != NEW.snapshot_digest OR canonical_bytes != NEW.canonical_bytes))
           OR (source_id = NEW.source_id AND snapshot_digest = NEW.snapshot_digest AND (registry_generation != NEW.registry_generation OR canonical_bytes != NEW.canonical_bytes))
    )
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v2 duplicate insertion conflict');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_registry_snapshots_v2_no_update
    BEFORE UPDATE ON cron_registry_snapshots_v2
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v2 rows are immutable');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_cron_registry_snapshots_v2_no_delete
    BEFORE DELETE ON cron_registry_snapshots_v2
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'cron_registry_snapshots_v2 rows cannot be deleted');
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

_V3_TRIGGER_NAMES = (
    "trg_cron_registry_snapshots_v2_insert_collision",
    "trg_cron_registry_snapshots_v2_no_update",
    "trg_cron_registry_snapshots_v2_no_delete",
)

_TRIGGER_NAMES = _V1_TRIGGER_NAMES + _V2_TRIGGER_NAMES + _V3_TRIGGER_NAMES

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

_V3_TABLE_NAMES = _V2_TABLE_NAMES + ("cron_registry_snapshots_v2",)
_TABLE_NAMES = _V3_TABLE_NAMES


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
    lowered = sql.lower().replace("if not exists", "").replace(";", "")
    lowered = lowered.replace("table main.", "table ").replace(
        "trigger main.", "trigger "
    )
    return "".join(lowered.split())


def _validate_schema_objects(cursor: sqlite3.Cursor, target_version: int = 3) -> None:
    _reject_temp_schema_collisions(cursor)

    expected = {
        ("table", "cron_authority_schema_version"): _CREATE_VERSION_TABLE_DDL,
        ("table", "cron_execution_aggregates"): _CREATE_AGGREGATES_TABLE_DDL,
        ("table", "cron_evidence"): _CREATE_EVIDENCE_TABLE_DDL,
        ("table", "cron_execution_attempts"): (
            _CREATE_ATTEMPTS_TABLE_DDL
            if target_version >= 3
            else _CREATE_V1_ATTEMPTS_TABLE_DDL
        ),
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
    if target_version >= 3:
        expected[("table", "cron_registry_snapshots_v2")] = (
            _CREATE_SNAPSHOTS_V2_TABLE_DDL
        )
        expected.update(
            {
                ("trigger", name): ddl
                for name, ddl in zip(_V3_TRIGGER_NAMES, _V3_TRIGGERS_DDL, strict=True)
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

    expected_names = {name for _, name in expected}
    rows_all = cursor.execute(
        "SELECT type, name FROM main.sqlite_master WHERE name LIKE 'cron_%';"
    ).fetchall()
    for obj_type, obj_name in rows_all:
        if obj_name not in expected_names:
            raise CronAuthorityError(
                f"Unexpected cron authority schema object: {obj_type} {obj_name}",
                code="schema_object_mismatch",
            )

    if target_version < 3:
        cols = [
            r[1]
            for r in cursor.execute(
                "PRAGMA main.table_info(cron_execution_attempts);"
            ).fetchall()
        ]
        expected_cols = [
            "execution_id",
            "attempt",
            "state",
            "runner_id",
            "fence_token",
            "lease_expires_at",
            "created_at",
            "updated_at",
        ]
        if cols != expected_cols:
            raise CronAuthorityError(
                "Cron authority attempts table column mismatch for version 2",
                code="schema_object_mismatch",
            )


def _ensure_attempt_claim_columns(cursor: sqlite3.Cursor) -> None:
    cols = {
        r[1]
        for r in cursor.execute(
            "PRAGMA main.table_info(cron_execution_attempts);"
        ).fetchall()
    }
    new_cols = [
        ("source_id", "TEXT"),
        ("schema_id", "TEXT"),
        ("schema_version", "INTEGER"),
        ("registry_generation", "INTEGER"),
        ("snapshot_digest", "TEXT"),
        ("canonical_snapshot_bytes", "BLOB"),
        ("trusted_runner_identity", "TEXT"),
        ("command_digest", "TEXT"),
        ("release_digest", "TEXT"),
        ("dependency_digest", "TEXT"),
    ]
    for col_name, col_type in new_cols:
        if col_name not in cols:
            cursor.execute(
                f"ALTER TABLE main.cron_execution_attempts ADD COLUMN {col_name} {col_type};"
            )


def _rebuild_execution_attempts_table(cursor: sqlite3.Cursor) -> None:
    rows_before = cursor.execute(
        "SELECT execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at FROM cron_execution_attempts ORDER BY execution_id ASC, attempt ASC;"
    ).fetchall()
    count_before = len(rows_before)
    digest_before = hashlib.sha256(
        json.dumps(
            [
                [str(x) if isinstance(x, (bytes, memoryview)) else x for x in r]
                for r in rows_before
            ],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    attempt_triggers = (
        "trg_cron_attempt_transition_guard",
        "trg_cron_attempt_fence_guard",
        "trg_cron_terminal_attempt_immutable",
        "trg_cron_attempt_insert_guard",
        "trg_cron_attempts_no_delete",
        "trg_cron_terminal_requires_receipt",
        "trg_cron_receipt_finalizes_attempt",
    )
    for trg_name in attempt_triggers:
        cursor.execute(f"DROP TRIGGER IF EXISTS main.{trg_name};")

    cursor.execute("PRAGMA legacy_alter_table = ON;")
    try:
        cursor.execute(
            "ALTER TABLE cron_execution_attempts RENAME TO cron_execution_attempts_old;"
        )
        cursor.execute(_main_ddl(_CREATE_ATTEMPTS_TABLE_DDL))

        cursor.execute(
            """
            INSERT INTO main.cron_execution_attempts (
                execution_id, attempt, state, runner_id, fence_token, lease_expires_at,
                source_id, schema_id, schema_version, registry_generation, snapshot_digest,
                canonical_snapshot_bytes, trusted_runner_identity, command_digest, release_digest,
                dependency_digest, created_at, updated_at
            )
            SELECT
                execution_id, attempt, state, runner_id, fence_token, lease_expires_at,
                NULL, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL,
                NULL, created_at, updated_at
            FROM cron_execution_attempts_old;
            """
        )
        cursor.execute("DROP TABLE cron_execution_attempts_old;")
    finally:
        cursor.execute("PRAGMA legacy_alter_table = OFF;")

    for trg_name, trg_ddl in zip(_V1_TRIGGER_NAMES, _TRIGGERS_DDL, strict=True):
        if trg_name in attempt_triggers:
            cursor.execute(_main_ddl(trg_ddl))

    rows_after = cursor.execute(
        "SELECT execution_id, attempt, state, runner_id, fence_token, lease_expires_at, created_at, updated_at FROM cron_execution_attempts ORDER BY execution_id ASC, attempt ASC;"
    ).fetchall()
    count_after = len(rows_after)
    digest_after = hashlib.sha256(
        json.dumps(
            [
                [str(x) if isinstance(x, (bytes, memoryview)) else x for x in r]
                for r in rows_after
            ],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if count_before != count_after or digest_before != digest_after:
        raise CronAuthorityError(
            "cron_execution_attempts table rebuild integrity mismatch",
            code="migration_integrity_failure",
        )


def migrate_cron_authority(target: Any, timeout: float = 30.0) -> None:
    """Migrate SQLite database to cron authority schema v3 atomically and idempotently."""
    close_connection_on_exit = not (
        hasattr(target, "cursor") and hasattr(target, "execute")
    )
    conn = connect_cron_authority(target, timeout=timeout)

    fk_disabled = False
    begin_succeeded = False
    transaction_closed = False
    post_commit_restore_failed = False

    try:
        if conn.in_transaction:
            raise CronAuthorityError(
                "Caller connection has an active transaction",
                code="active_caller_transaction",
            )

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
            if rows == [(1, 3)]:
                try:
                    conn.execute("BEGIN IMMEDIATE;")
                    begin_succeeded = True
                except (sqlite3.DatabaseError, sqlite3.OperationalError) as begin_exc:
                    if not conn.in_transaction:
                        if close_connection_on_exit:
                            with contextlib.suppress(Exception):
                                conn.close()
                        raise
                    else:
                        with contextlib.suppress(Exception):
                            conn.close()
                        raise CronAuthorityError(
                            f"BEGIN failed with active transaction: {begin_exc}",
                            code="begin_failed_active_transaction",
                        ) from begin_exc

                _ensure_attempt_claim_columns(cursor)
                _validate_schema_objects(cursor, target_version=3)
                conn.commit()
                transaction_closed = not conn.in_transaction
                return
            elif rows in ([(1, 1)], [(1, 2)]):
                cur_ver = rows[0][1]
                fk_mode = conn.execute("PRAGMA main.foreign_keys;").fetchone()
                if fk_mode != (1,):
                    raise CronAuthorityError(
                        "SQLite foreign keys must be enabled before migration",
                        code="foreign_keys_disabled",
                    )

                conn.execute("PRAGMA foreign_keys = OFF;")
                fk_disabled = True
                try:
                    fk_disable_readback = conn.execute(
                        "PRAGMA main.foreign_keys;"
                    ).fetchone()
                except Exception as fk_disable_exc:
                    with contextlib.suppress(Exception):
                        conn.close()
                    raise CronAuthorityError(
                        "Failed to disable foreign keys before migration",
                        code="foreign_keys_disable_failed",
                    ) from fk_disable_exc
                if fk_disable_readback != (0,):
                    with contextlib.suppress(Exception):
                        conn.close()
                    raise CronAuthorityError(
                        "Failed to disable foreign keys before migration",
                        code="foreign_keys_disable_failed",
                    )

                try:
                    conn.execute("BEGIN IMMEDIATE;")
                    begin_succeeded = True
                except Exception as begin_exc:
                    if not conn.in_transaction:
                        if fk_disabled:
                            with contextlib.suppress(Exception):
                                conn.execute("PRAGMA foreign_keys = ON;")
                                if conn.execute(
                                    "PRAGMA main.foreign_keys;"
                                ).fetchone() == (1,):
                                    fk_disabled = False
                        if fk_disabled:
                            with contextlib.suppress(Exception):
                                conn.close()
                            raise CronAuthorityError(
                                "Foreign key restoration failed after BEGIN failure",
                                code="foreign_keys_restore_failed",
                            ) from begin_exc
                        if close_connection_on_exit:
                            with contextlib.suppress(Exception):
                                conn.close()
                        raise
                    else:
                        with contextlib.suppress(Exception):
                            conn.close()
                        raise CronAuthorityError(
                            "BEGIN failed after transaction state became active",
                            code="begin_failed_active_transaction",
                        ) from begin_exc

                if cur_ver == 1:
                    _validate_schema_objects(cursor, target_version=1)
                    cursor.execute(_main_ddl(_CREATE_TRIGGER_DELIVERIES_TABLE_DDL))
                    cursor.execute(_main_ddl(_CREATE_SNAPSHOTS_TABLE_DDL))
                    for trigger_sql in _V2_TRIGGERS_DDL:
                        cursor.execute(_main_ddl(trigger_sql))

                _validate_schema_objects(cursor, target_version=2)
                cursor.execute(_main_ddl(_CREATE_SNAPSHOTS_V2_TABLE_DDL))
                for trigger_sql in _V3_TRIGGERS_DDL:
                    cursor.execute(_main_ddl(trigger_sql))

                _rebuild_execution_attempts_table(cursor)

                cursor.execute(
                    "UPDATE main.cron_authority_schema_version SET schema_version = 3 WHERE authority_id = 1;"
                )
                _validate_schema_objects(cursor, target_version=3)

                fk_check = cursor.execute("PRAGMA main.foreign_key_check;").fetchall()
                if fk_check:
                    raise CronAuthorityError(
                        f"Foreign key integrity check failed after rebuild: {fk_check!r}",
                        code="foreign_key_violation",
                    )

                integrity = cursor.execute("PRAGMA main.integrity_check;").fetchall()
                if integrity != [("ok",)]:
                    raise CronAuthorityError(
                        f"Database integrity check failed after rebuild: {integrity!r}",
                        code="integrity_check_failure",
                    )

                conn.commit()
                transaction_closed = not conn.in_transaction

                try:
                    conn.execute("PRAGMA foreign_keys = ON;")
                    fk_restore_readback = conn.execute(
                        "PRAGMA main.foreign_keys;"
                    ).fetchone()
                except Exception as fk_restore_exc:
                    post_commit_restore_failed = True
                    with contextlib.suppress(Exception):
                        conn.close()
                    raise CronAuthorityError(
                        "Failed to restore foreign keys after migration",
                        code="foreign_keys_restore_failed",
                    ) from fk_restore_exc
                if fk_restore_readback != (1,):
                    post_commit_restore_failed = True
                    with contextlib.suppress(Exception):
                        conn.close()
                    raise CronAuthorityError(
                        "Failed to restore foreign keys after migration",
                        code="foreign_keys_restore_failed",
                    )
                fk_disabled = False
                return
            else:
                raise CronAuthorityError(
                    f"Unsupported cron authority schema version rows: {rows!r}",
                    code="unsupported_schema_version",
                )

        # Fresh DB migration directly to v3
        try:
            conn.execute("BEGIN IMMEDIATE;")
            begin_succeeded = True
        except Exception:
            if not conn.in_transaction:
                if close_connection_on_exit:
                    with contextlib.suppress(Exception):
                        conn.close()
                raise
            else:
                with contextlib.suppress(Exception):
                    conn.close()
                raise

        cursor.execute(_main_ddl(_CREATE_VERSION_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_AGGREGATES_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_EVIDENCE_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_ATTEMPTS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_RECEIPTS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_CURSORS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_TRIGGER_DELIVERIES_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_SNAPSHOTS_TABLE_DDL))
        cursor.execute(_main_ddl(_CREATE_SNAPSHOTS_V2_TABLE_DDL))

        for trigger_sql in _TRIGGERS_DDL:
            cursor.execute(_main_ddl(trigger_sql))
        for trigger_sql in _V2_TRIGGERS_DDL:
            cursor.execute(_main_ddl(trigger_sql))
        for trigger_sql in _V3_TRIGGERS_DDL:
            cursor.execute(_main_ddl(trigger_sql))

        _validate_schema_objects(cursor, target_version=3)

        now_utc = datetime.now(timezone.utc).isoformat()
        if now_utc.endswith("+00:00"):
            now_utc = now_utc[:-6] + "Z"

        cursor.execute(
            "INSERT INTO main.cron_authority_schema_version (authority_id, schema_version, installed_at) VALUES (1, ?, ?);",
            (SCHEMA_VERSION, now_utc),
        )

        conn.commit()
        transaction_closed = not conn.in_transaction
    except Exception as primary_exc:
        if post_commit_restore_failed:
            raise
        conn_is_closed = False
        try:
            in_tx = conn.in_transaction
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            conn_is_closed = True
            in_tx = False

        if not begin_succeeded:
            if not in_tx and not conn_is_closed:
                if fk_disabled:
                    with contextlib.suppress(Exception):
                        conn.execute("PRAGMA foreign_keys = ON;")
                        if conn.execute("PRAGMA main.foreign_keys;").fetchone() == (1,):
                            fk_disabled = False
                if fk_disabled:
                    with contextlib.suppress(Exception):
                        conn.close()
                    raise CronAuthorityError(
                        "Foreign key restoration failed after BEGIN failure",
                        code="foreign_keys_restore_failed",
                    ) from primary_exc
                if close_connection_on_exit:
                    with contextlib.suppress(Exception):
                        conn.close()
                raise
            else:
                with contextlib.suppress(Exception):
                    conn.close()
                raise
        else:
            rollback_exc: Exception | None = None
            if not transaction_closed and not conn_is_closed:
                if in_tx:
                    try:
                        conn.rollback()
                    except Exception as exc:
                        rollback_exc = exc
                try:
                    transaction_closed = not conn.in_transaction
                except (
                    sqlite3.ProgrammingError,
                    sqlite3.OperationalError,
                    AttributeError,
                ):
                    transaction_closed = False

            if transaction_closed and fk_disabled and not conn_is_closed:
                with contextlib.suppress(Exception):
                    conn.execute("PRAGMA foreign_keys = ON;")
                    if conn.execute("PRAGMA main.foreign_keys;").fetchone() == (1,):
                        fk_disabled = False

            if not transaction_closed or fk_disabled or conn_is_closed:
                with contextlib.suppress(Exception):
                    conn.close()
                rollback_context = (
                    {"rollback_error_type": type(rollback_exc).__name__}
                    if rollback_exc is not None
                    else None
                )
                if not transaction_closed:
                    raise CronAuthorityError(
                        "Transaction closure could not be proven after migration error",
                        code="transaction_closure_failed",
                        context=rollback_context,
                    ) from primary_exc
                else:
                    raise CronAuthorityError(
                        "Foreign key restoration failed after migration error",
                        code="foreign_keys_restore_failed",
                        context=rollback_context,
                    ) from primary_exc

            if rollback_exc is not None:
                raise CronAuthorityError(
                    "Migration failed after rollback reported an error",
                    code="migration_failed_with_rollback_error",
                    context={"rollback_error_type": type(rollback_exc).__name__},
                ) from primary_exc

            if close_connection_on_exit:
                with contextlib.suppress(Exception):
                    conn.close()
            raise
    finally:
        if close_connection_on_exit:
            with contextlib.suppress(Exception):
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

        if hasattr(db_target, "cursor") and hasattr(db_target, "execute"):
            conn = db_target
            close_conn = False
        else:
            migrate_cron_authority(db_target, timeout=timeout)
            conn = connect_cron_authority(db_target, timeout=timeout)
            close_conn = True

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
    def compute_snapshot_digest_v2(cls, canonical_bytes: bytes) -> str:
        return hashlib.sha256(
            REGISTRY_DIGEST_DOMAIN_V2 + b"\x00" + canonical_bytes
        ).hexdigest()

    @classmethod
    def install_registry_snapshot_v2(
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

        canonical_bytes, parsed_dict = cls._canonicalize_and_validate_snapshot_v2(
            snapshot_data, expected_generation=registry_generation
        )
        snapshot_digest = cls.compute_snapshot_digest_v2(canonical_bytes)

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
                SELECT snapshot_digest, canonical_bytes FROM main.cron_registry_snapshots_v2
                WHERE source_id = ? AND (registry_generation = ? OR snapshot_digest = ?);
                """,
                (REGISTRY_SOURCE_ID_V2, registry_generation, snapshot_digest),
            ).fetchone()

            if row is not None:
                ex_digest, ex_bytes = row
                if ex_digest == snapshot_digest and bytes(ex_bytes) == canonical_bytes:
                    conn.commit()
                    return {
                        "status": "converged",
                        "source_id": REGISTRY_SOURCE_ID_V2,
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
                INSERT INTO main.cron_registry_snapshots_v2 (
                    source_id, schema_id, schema_version, registry_generation, snapshot_digest, canonical_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    REGISTRY_SOURCE_ID_V2,
                    REGISTRY_SCHEMA_ID,
                    REGISTRY_SCHEMA_VERSION_V2,
                    registry_generation,
                    snapshot_digest,
                    canonical_bytes,
                    now_utc,
                ),
            )
            conn.commit()
            return {
                "status": "installed",
                "source_id": REGISTRY_SOURCE_ID_V2,
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
    def read_registry_snapshot_v2(
        cls,
        db_target: Any,
        source_id: str,
        registry_generation: int,
        snapshot_digest: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        cls.verify_pinned_db_path(db_target)
        if source_id != REGISTRY_SOURCE_ID_V2:
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

        if hasattr(db_target, "cursor") and hasattr(db_target, "execute"):
            conn = db_target
            close_conn = False
        else:
            migrate_cron_authority(db_target, timeout=timeout)
            conn = connect_cron_authority(db_target, timeout=timeout)
            close_conn = True

        try:
            row = conn.execute(
                """
                SELECT schema_id, schema_version, canonical_bytes FROM main.cron_registry_snapshots_v2
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
            if s_id != REGISTRY_SCHEMA_ID or s_ver != REGISTRY_SCHEMA_VERSION_V2:
                raise CronAuthorityError(
                    "Registry snapshot schema mismatch", code="schema_mismatch"
                )

            c_bytes = bytes(c_bytes_blob)
            computed_digest = cls.compute_snapshot_digest_v2(c_bytes)
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

        for ev_key in ("release_root_evidence", "cwd_evidence", "executable_evidence"):
            ev = parsed[ev_key]
            if not isinstance(ev, dict) or not ev:
                raise CronAuthorityError(
                    f"Evidence dictionary {ev_key} cannot be empty",
                    code="invalid_object_evidence",
                )
            req = {"canonical_path", "device", "inode", "object_type", "owner", "mode"}
            if not req.issubset(ev.keys()):
                raise CronAuthorityError(
                    f"Evidence dictionary {ev_key} missing required keys",
                    code="invalid_object_evidence",
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
    def _canonicalize_and_validate_snapshot_v2(
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

        REQUIRED_KEYS_V2 = frozenset(
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
                "schedule",
                "schedule_timezone",
            }
        )
        if set(parsed.keys()) != REQUIRED_KEYS_V2:
            raise CronAuthorityError(
                "Snapshot top-level keys mismatch for v2", code="invalid_snapshot_keys"
            )

        if parsed["source_id"] != REGISTRY_SOURCE_ID_V2:
            raise CronAuthorityError(
                f"Invalid source_id: {parsed['source_id']!r}",
                code="invalid_source_id",
            )
        if parsed["schema_id"] != REGISTRY_SCHEMA_ID:
            raise CronAuthorityError(
                f"Invalid schema_id: {parsed['schema_id']!r}",
                code="invalid_schema_id",
            )
        if type(parsed["schema_version"]) is not int or parsed["schema_version"] != 2:
            raise CronAuthorityError(
                "schema_version must be non-boolean int 2 for snapshot v2",
                code="invalid_schema_version",
            )
        if (
            type(parsed["registry_generation"]) is not int
            or parsed["registry_generation"] != expected_generation
        ):
            raise CronAuthorityError(
                "registry_generation mismatch", code="registry_generation_mismatch"
            )

        sched = parsed.get("schedule")
        sched_tz = parsed.get("schedule_timezone")
        if not isinstance(sched, str) or not isinstance(sched_tz, str):
            raise CronAuthorityError(
                "schedule and schedule_timezone must be non-empty strings",
                code="invalid_snapshot_schedule",
            )

        parts = sched.strip().split()
        if len(parts) != 5:
            raise CronAuthorityError(
                "schedule must be exactly 5 space-separated cron fields",
                code="invalid_cron_schedule",
            )

        from croniter import croniter

        if not croniter.is_valid(sched):
            raise CronAuthorityError(
                f"Invalid cron expression: {sched!r}",
                code="invalid_cron_schedule",
            )

        import zoneinfo

        try:
            zoneinfo.ZoneInfo(sched_tz)
        except Exception as exc:
            raise CronAuthorityError(
                f"Invalid IANA timezone: {sched_tz!r}",
                code="invalid_schedule_timezone",
            ) from exc

        for ev_key in ("release_root_evidence", "cwd_evidence", "executable_evidence"):
            ev = parsed[ev_key]
            if not isinstance(ev, dict) or not ev:
                raise CronAuthorityError(
                    f"Evidence dictionary {ev_key} cannot be empty",
                    code="invalid_object_evidence",
                )
            req = {"canonical_path", "device", "inode", "object_type", "owner", "mode"}
            if not req.issubset(ev.keys()):
                raise CronAuthorityError(
                    f"Evidence dictionary {ev_key} missing required keys",
                    code="invalid_object_evidence",
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

    @classmethod
    def get_trusted_release_parent(cls) -> Path:
        return get_trusted_release_parent()

    @classmethod
    def set_trusted_release_parent(cls, path: Path | str) -> Path:
        return set_trusted_release_parent(path)

    @classmethod
    def validate_and_pin_execution_objects(cls, snapshot: Any) -> dict[str, int]:
        """Validate release root, cwd, and executable using component-aware descriptor-based no-follow traversal."""
        for name in ("release_root_evidence", "cwd_evidence", "executable_evidence"):
            ev = getattr(snapshot, name, None)
            if not isinstance(ev, dict) or not ev:
                raise CronAuthorityError(
                    f"Evidence dictionary {name} cannot be empty",
                    code="invalid_object_evidence",
                )
            req = {"canonical_path", "device", "inode", "object_type", "owner", "mode"}
            if not req.issubset(ev.keys()):
                raise CronAuthorityError(
                    f"Evidence dictionary {name} missing required keys",
                    code="invalid_object_evidence",
                )

        trusted_parent = cls.get_trusted_release_parent()
        rel_root = getattr(snapshot, "release_root", "")

        tp_str = str(trusted_parent)
        pattern = re.compile(r"^" + re.escape(tp_str) + r"/([0-9a-f]{40})$")
        m = pattern.fullmatch(rel_root)
        if not m:
            raise CronAuthorityError(
                f"release_root must be exact canonical absolute {tp_str}/<40-hex>: {rel_root!r}",
                code="nonexistent_release_root",
            )
        if ".." in rel_root or "/latest" in rel_root or "//" in rel_root:
            raise CronAuthorityError(
                f"release_root contains relative path or alias: {rel_root!r}",
                code="alias_root_rejected",
            )

        cwd = getattr(snapshot, "cwd", "")
        if cwd != rel_root and not cwd.startswith(rel_root + "/"):
            raise CronAuthorityError(
                f"cwd must be inside release_root: {cwd!r}", code="cwd_escape"
            )
        if ".." in cwd or "/latest" in cwd or "//" in cwd:
            raise CronAuthorityError(
                f"cwd contains relative path or alias: {cwd!r}", code="cwd_escape"
            )

        argv = getattr(snapshot, "argv", ())
        if not argv or not isinstance(argv, (tuple, list)):
            raise CronAuthorityError("argv cannot be empty", code="invalid_snapshot")
        argv0 = argv[0]
        if argv0.startswith("/"):
            exe_path = argv0
        else:
            exe_path = os.path.normpath(os.path.join(cwd, argv0))

        if exe_path != rel_root and not exe_path.startswith(rel_root + "/"):
            raise CronAuthorityError(
                f"executable must resolve inside release_root: {exe_path!r}",
                code="executable_escape",
            )
        if ".." in exe_path or "/latest" in exe_path or "//" in exe_path:
            raise CronAuthorityError(
                f"executable path contains relative path or alias: {exe_path!r}",
                code="executable_escape",
            )

        fds: dict[str, int] = {}
        try:
            try:
                fd_tp = os.open(
                    str(trusted_parent),
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                )
                fds["trusted_parent"] = fd_tp
            except OSError as exc:
                raise CronAuthorityError(
                    f"Cannot open trusted release parent: {trusted_parent}",
                    code="nonexistent_release_root",
                ) from exc

            st_tp = os.fstat(fd_tp)
            if (
                st_tp.st_nlink == 0
                or (st_tp.st_mode & 0o022) != 0
                or st_tp.st_uid not in (0, os.getuid())
            ):
                raise CronAuthorityError(
                    f"Trusted release parent insecure or unlinked: {trusted_parent}",
                    code=(
                        "insecure_permissions"
                        if (st_tp.st_mode & 0o022) != 0
                        else "insecure_owner"
                    ),
                )

            hex40_comp = m.group(1)
            try:
                fd_root = os.open(
                    hex40_comp,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                    dir_fd=fd_tp,
                )
                fds["root"] = fd_root
            except OSError as exc:
                raise CronAuthorityError(
                    f"Cannot open release_root: {rel_root}",
                    code="nonexistent_release_root",
                ) from exc

            st_root = os.fstat(fd_root)
            if st_root.st_nlink == 0 or not stat.S_ISDIR(st_root.st_mode):
                raise CronAuthorityError(
                    f"release_root is not a directory or unlinked: {rel_root}",
                    code="nonexistent_release_root",
                )
            if (st_root.st_mode & 0o022) != 0:
                raise CronAuthorityError(
                    f"release_root group/world writable: {rel_root}",
                    code="insecure_permissions",
                )
            if st_root.st_uid not in (0, os.getuid()):
                raise CronAuthorityError(
                    f"release_root wrong owner: {rel_root}",
                    code="insecure_owner",
                )

            rel_cwd_sub = os.path.relpath(cwd, rel_root)
            if rel_cwd_sub == ".":
                fd_cwd = os.open(
                    ".",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                    dir_fd=fd_root,
                )
            else:
                curr_fd = fd_root
                cwd_comps = rel_cwd_sub.split("/")
                for comp in cwd_comps:
                    if comp in ("", ".", "..", "latest"):
                        raise CronAuthorityError(
                            f"Invalid cwd component: {comp!r}", code="cwd_escape"
                        )
                    try:
                        next_fd = os.open(
                            comp,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                            dir_fd=curr_fd,
                        )
                    except OSError as exc:
                        raise CronAuthorityError(
                            f"Cannot open cwd component: {cwd}",
                            code="nonexistent_cwd",
                        ) from exc
                    if curr_fd != fd_root:
                        os.close(curr_fd)
                    curr_fd = next_fd
                fd_cwd = curr_fd
            fds["cwd"] = fd_cwd

            st_cwd = os.fstat(fd_cwd)
            if st_cwd.st_nlink == 0 or not stat.S_ISDIR(st_cwd.st_mode):
                raise CronAuthorityError(
                    f"cwd is not a directory or unlinked: {cwd}", code="nonexistent_cwd"
                )
            if (st_cwd.st_mode & 0o022) != 0:
                raise CronAuthorityError(
                    f"cwd group/world writable: {cwd}",
                    code="insecure_permissions",
                )
            if st_cwd.st_uid not in (0, os.getuid()):
                raise CronAuthorityError(
                    f"cwd wrong owner: {cwd}",
                    code="insecure_owner",
                )

            rel_exe_sub = os.path.relpath(exe_path, rel_root)
            curr_fd = fd_root
            exe_comps = rel_exe_sub.split("/")
            for i, comp in enumerate(exe_comps):
                if comp in ("", ".", "..", "latest"):
                    raise CronAuthorityError(
                        f"Invalid executable component: {comp!r}",
                        code="executable_escape",
                    )
                is_last = i == len(exe_comps) - 1
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                if not is_last:
                    flags |= os.O_DIRECTORY
                try:
                    next_fd = os.open(comp, flags, dir_fd=curr_fd)
                except OSError as exc:
                    raise CronAuthorityError(
                        f"Cannot open executable component: {exe_path}",
                        code="nonexistent_executable",
                    ) from exc
                if curr_fd not in (fd_root, fd_cwd):
                    os.close(curr_fd)
                curr_fd = next_fd
            fd_exe = curr_fd
            fds["exe"] = fd_exe

            st_exe = os.fstat(fd_exe)
            if st_exe.st_nlink == 0 or not stat.S_ISREG(st_exe.st_mode):
                raise CronAuthorityError(
                    f"executable is not a regular file or unlinked: {exe_path}",
                    code="nonexistent_executable",
                )
            if (st_exe.st_mode & 0o111) == 0:
                raise CronAuthorityError(
                    f"executable is not executable: {exe_path}",
                    code="nonexistent_executable",
                )
            if (st_exe.st_mode & 0o022) != 0:
                raise CronAuthorityError(
                    f"executable group/world writable: {exe_path}",
                    code="insecure_permissions",
                )
            if st_exe.st_uid not in (0, os.getuid()):
                raise CronAuthorityError(
                    f"executable wrong owner: {exe_path}",
                    code="insecure_owner",
                )

            cls._verify_evidence_dict(
                snapshot.release_root_evidence,
                rel_root,
                st_root,
                "directory",
                "release_root",
            )
            cls._verify_evidence_dict(
                snapshot.cwd_evidence, cwd, st_cwd, "directory", "cwd"
            )
            cls._verify_evidence_dict(
                snapshot.executable_evidence,
                exe_path,
                st_exe,
                "regular_executable",
                "executable",
                fd=fd_exe,
            )

            return fds
        except Exception:
            cls.close_pinned_fds(fds)
            raise

    @classmethod
    def _verify_evidence_dict(
        cls,
        ev: dict[str, Any],
        expected_path: str,
        st: os.stat_result,
        expected_type: str,
        label: str,
        fd: int | None = None,
    ) -> None:
        if ev.get("canonical_path") != expected_path:
            raise CronAuthorityError(
                f"{label} canonical_path mismatch: expected {expected_path}, got {ev.get('canonical_path')}",
                code="snapshot_evidence_mismatch",
            )
        if "device" in ev and ev["device"] != st.st_dev:
            raise CronAuthorityError(
                f"{label} device mismatch", code="snapshot_evidence_mismatch"
            )
        if "inode" in ev and ev["inode"] != st.st_ino:
            raise CronAuthorityError(
                f"{label} inode mismatch", code="snapshot_evidence_mismatch"
            )
        if "owner" in ev and str(ev["owner"]) != str(st.st_uid):
            raise CronAuthorityError(
                f"{label} owner mismatch", code="snapshot_evidence_mismatch"
            )
        if "mode" in ev and (st.st_mode & 0o777) != (ev["mode"] & 0o777):
            raise CronAuthorityError(
                f"{label} mode mismatch", code="snapshot_evidence_mismatch"
            )
        if "content_digest" in ev and fd is not None:
            content = os.pread(fd, 1048576, 0)
            digest = hashlib.sha256(content).hexdigest()
            if digest != ev["content_digest"]:
                raise CronAuthorityError(
                    f"{label} content_digest mismatch",
                    code="snapshot_evidence_mismatch",
                )

    @classmethod
    def reverify_pinned_execution_objects(
        cls, fds: dict[str, int], snapshot: Any
    ) -> None:
        """Re-verify stat and perform full re-traversal from trusted parent descriptor pre-spawn."""
        if (
            "trusted_parent" not in fds
            or "root" not in fds
            or "cwd" not in fds
            or "exe" not in fds
        ):
            raise CronAuthorityError(
                "Missing pinned descriptors for pre-spawn reverification",
                code="prespawn_replacement_detected",
            )

        for key in ("trusted_parent", "root", "cwd", "exe"):
            try:
                st = os.fstat(fds[key])
                if st.st_nlink == 0:
                    raise CronAuthorityError(
                        f"Pinned descriptor for {key} unlinked on disk (st_nlink=0)",
                        code="prespawn_replacement_detected",
                    )
                if (st.st_mode & 0o022) != 0:
                    raise CronAuthorityError(
                        f"Pinned descriptor for {key} became group/world writable",
                        code="insecure_permissions",
                    )
            except OSError as exc:
                raise CronAuthorityError(
                    f"Pinned descriptor for {key} invalidated",
                    code="prespawn_replacement_detected",
                ) from exc

        trusted_parent = cls.get_trusted_release_parent()
        rel_root = getattr(snapshot, "release_root", "")
        tp_str = str(trusted_parent)
        pattern = re.compile(r"^" + re.escape(tp_str) + r"/([0-9a-f]{40})$")
        m = pattern.fullmatch(rel_root)
        if not m:
            raise CronAuthorityError(
                "release_root formatting invalid pre-spawn",
                code="prespawn_replacement_detected",
            )

        hex40_comp = m.group(1)
        temp_fds: list[int] = []
        try:
            try:
                st_tp_current = os.stat(str(trusted_parent))
                st_tp_pinned = os.fstat(fds["trusted_parent"])
                if (st_tp_current.st_dev, st_tp_current.st_ino) != (
                    st_tp_pinned.st_dev,
                    st_tp_pinned.st_ino,
                ):
                    raise CronAuthorityError(
                        "Trusted parent directory replaced on disk",
                        code="prespawn_replacement_detected",
                    )
            except OSError as exc:
                raise CronAuthorityError(
                    "Trusted parent directory inaccessible pre-spawn",
                    code="prespawn_replacement_detected",
                ) from exc

            try:
                fd_root_new = os.open(
                    hex40_comp,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                    dir_fd=fds["trusted_parent"],
                )
                temp_fds.append(fd_root_new)
            except OSError as exc:
                raise CronAuthorityError(
                    f"release_root missing or unopenable pre-spawn: {rel_root}",
                    code="prespawn_replacement_detected",
                ) from exc

            st_root_new = os.fstat(fd_root_new)
            st_root_pinned = os.fstat(fds["root"])
            if (
                st_root_new.st_nlink == 0
                or st_root_new.st_dev != st_root_pinned.st_dev
                or st_root_new.st_ino != st_root_pinned.st_ino
                or st_root_new.st_mode != st_root_pinned.st_mode
                or st_root_new.st_uid != st_root_pinned.st_uid
            ):
                raise CronAuthorityError(
                    "release_root replaced or unlinked pre-spawn",
                    code="prespawn_replacement_detected",
                )

            cwd = snapshot.cwd
            rel_cwd_sub = os.path.relpath(cwd, rel_root)
            if rel_cwd_sub == ".":
                fd_cwd_new = os.open(
                    ".",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                    dir_fd=fd_root_new,
                )
            else:
                curr_fd = fd_root_new
                cwd_comps = rel_cwd_sub.split("/")
                for comp in cwd_comps:
                    next_fd = os.open(
                        comp,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY,
                        dir_fd=curr_fd,
                    )
                    if curr_fd != fd_root_new:
                        os.close(curr_fd)
                    curr_fd = next_fd
                fd_cwd_new = curr_fd
            temp_fds.append(fd_cwd_new)

            st_cwd_new = os.fstat(fd_cwd_new)
            st_cwd_pinned = os.fstat(fds["cwd"])
            if (
                st_cwd_new.st_nlink == 0
                or st_cwd_new.st_dev != st_cwd_pinned.st_dev
                or st_cwd_new.st_ino != st_cwd_pinned.st_ino
                or st_cwd_new.st_mode != st_cwd_pinned.st_mode
                or st_cwd_new.st_uid != st_cwd_pinned.st_uid
            ):
                raise CronAuthorityError(
                    "cwd replaced or unlinked pre-spawn",
                    code="prespawn_replacement_detected",
                )

            argv0 = snapshot.argv[0]
            exe_path = (
                argv0
                if argv0.startswith("/")
                else os.path.normpath(os.path.join(cwd, argv0))
            )
            rel_exe_sub = os.path.relpath(exe_path, rel_root)
            curr_fd = fd_root_new
            exe_comps = rel_exe_sub.split("/")
            for i, comp in enumerate(exe_comps):
                is_last = i == len(exe_comps) - 1
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                if not is_last:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(comp, flags, dir_fd=curr_fd)
                if curr_fd not in (fd_root_new, fd_cwd_new):
                    os.close(curr_fd)
                curr_fd = next_fd
            fd_exe_new = curr_fd
            temp_fds.append(fd_exe_new)

            st_exe_new = os.fstat(fd_exe_new)
            st_exe_pinned = os.fstat(fds["exe"])
            if (
                st_exe_new.st_nlink == 0
                or st_exe_new.st_dev != st_exe_pinned.st_dev
                or st_exe_new.st_ino != st_exe_pinned.st_ino
                or st_exe_new.st_mode != st_exe_pinned.st_mode
                or st_exe_new.st_uid != st_exe_pinned.st_uid
            ):
                raise CronAuthorityError(
                    "executable replaced or unlinked pre-spawn",
                    code="prespawn_replacement_detected",
                )

            content_new = os.pread(fd_exe_new, 1048576, 0)
            digest_new = hashlib.sha256(content_new).hexdigest()
            ev_exe = getattr(snapshot, "executable_evidence", {})
            if "content_digest" in ev_exe and ev_exe["content_digest"] != digest_new:
                raise CronAuthorityError(
                    "executable content drift pre-spawn",
                    code="prespawn_replacement_detected",
                )
        finally:
            for fd in temp_fds:
                with contextlib.suppress(OSError):
                    os.close(fd)

    @classmethod
    def close_pinned_fds(cls, fds: dict[str, int]) -> None:
        for fd in fds.values():
            with contextlib.suppress(OSError):
                os.close(fd)


@dataclass(frozen=True)
class CronRegistrySnapshotRow:
    source_id: str
    schema_id: str
    schema_version: int
    registry_generation: int
    snapshot_digest: str
    canonical_bytes: bytes
    created_at: str
    schedule: str | None
    schedule_timezone: str | None
    schedule_available: bool
    schedule_unavailable_reason: str | None


@dataclass(frozen=True)
class CronTriggerDeliveryRow:
    trigger_event_id: str
    trigger_digest: str
    canonical_bytes: bytes
    trigger_kind: str
    transport_kind: str
    cron_id: str
    registry_generation: int
    schedule_bucket: str
    command_digest: str
    release_digest: str
    execution_id: str | None
    disposition: str
    reason_code: str
    submitted_at: str
    created_at: str


@dataclass(frozen=True)
class CronExecutionAggregateRow:
    execution_id: str
    cron_id: str
    registry_generation: int
    schedule_bucket: str
    command_digest: str
    release_digest: str
    created_at: str


@dataclass(frozen=True)
class CronExecutionAttemptRow:
    execution_id: str
    attempt: int
    state: str
    runner_id: str | None
    fence_token: int | None
    lease_expires_at: str | None
    source_id: str | None
    schema_id: str | None
    schema_version: int | None
    registry_generation: int | None
    snapshot_digest: str | None
    canonical_snapshot_bytes: bytes | None
    trusted_runner_identity: str | None
    command_digest: str | None
    release_digest: str | None
    dependency_digest: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CronReceiptRow:
    receipt_id: str
    execution_id: str
    attempt: int
    cron_id: str
    outcome: str
    runner_id: str
    runner_release_digest: str
    started_at: str
    finished_at: str
    error_classification: str | None
    evidence_digest: str | None
    signing_key_id: str
    signature: str
    schema_version: int
    created_at: str


@dataclass(frozen=True)
class CronSweepCursorRow:
    scope_key: str
    cron_id: str
    registry_generation: int
    command_digest: str
    catch_up_policy_version: int
    cursor_value: int
    updated_at: str


@dataclass(frozen=True)
class CronProjectionSource:
    registry_snapshots: tuple[CronRegistrySnapshotRow, ...]
    trigger_deliveries: tuple[CronTriggerDeliveryRow, ...]
    execution_aggregates: tuple[CronExecutionAggregateRow, ...]
    execution_attempts: tuple[CronExecutionAttemptRow, ...]
    terminal_receipts: tuple[CronReceiptRow, ...]
    sweep_cursors: tuple[CronSweepCursorRow, ...]


def read_cron_projection_source(db_path: str | Path) -> CronProjectionSource:
    """Read a stable, read-only operational snapshot spanning all authority row families."""
    if hasattr(db_path, "cursor") and hasattr(db_path, "execute"):
        raise CronAuthorityError(
            "Connection objects rejected; caller must supply filesystem db_path",
            code="connection_rejected",
        )

    target_str = str(db_path)
    if target_str != ":memory:" and not target_str.startswith("file:"):
        path_obj = Path(target_str).resolve()
        if not path_obj.exists() or not path_obj.is_file():
            raise CronAuthorityError(
                f"Database path does not exist or is not a file: {target_str!r}",
                code="nonexistent_database",
            )
        db_uri = f"file:{path_obj}?mode=ro"
    else:
        db_uri = target_str

    try:
        conn = sqlite3.connect(
            db_uri, uri=isinstance(db_uri, str) and db_uri.startswith("file:")
        )
    except sqlite3.Error as exc:
        raise CronAuthorityError(
            f"Failed to open read-only connection: {exc}",
            code="database_connect_failure",
        ) from exc

    try:
        conn.execute("PRAGMA query_only = ON;")
        cursor = conn.cursor()
        _reject_temp_schema_collisions(cursor)

        cursor.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name='cron_authority_schema_version';"
        )
        if cursor.fetchone() is None:
            raise CronAuthorityError(
                "Missing cron authority schema version table",
                code="unsupported_schema_version",
            )

        row_ver = cursor.execute(
            "SELECT schema_version FROM main.cron_authority_schema_version WHERE authority_id = 1;"
        ).fetchone()
        if row_ver is None or row_ver[0] not in (1, 2, 3):
            raise CronAuthorityError(
                "Unsupported cron authority schema version",
                code="unsupported_schema_version",
            )

        snapshots: list[CronRegistrySnapshotRow] = []

        has_v1 = (
            cursor.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name='cron_registry_snapshots_v1';"
            ).fetchone()
            is not None
        )
        if has_v1:
            v1_rows = cursor.execute(
                """
                SELECT source_id, schema_id, schema_version, registry_generation, snapshot_digest, canonical_bytes, created_at
                FROM main.cron_registry_snapshots_v1
                ORDER BY registry_generation ASC, source_id ASC;
                """
            ).fetchall()
            for r in v1_rows:
                snapshots.append(
                    CronRegistrySnapshotRow(
                        source_id=r[0],
                        schema_id=r[1],
                        schema_version=r[2],
                        registry_generation=r[3],
                        snapshot_digest=r[4],
                        canonical_bytes=bytes(r[5]),
                        created_at=r[6],
                        schedule=None,
                        schedule_timezone=None,
                        schedule_available=False,
                        schedule_unavailable_reason="legacy_v1_schedule_unavailable",
                    )
                )

        has_v2 = (
            cursor.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name='cron_registry_snapshots_v2';"
            ).fetchone()
            is not None
        )
        if has_v2:
            v2_rows = cursor.execute(
                """
                SELECT source_id, schema_id, schema_version, registry_generation, snapshot_digest, canonical_bytes, created_at
                FROM main.cron_registry_snapshots_v2
                ORDER BY registry_generation ASC, source_id ASC;
                """
            ).fetchall()
            for r in v2_rows:
                c_bytes = bytes(r[5])
                try:
                    parsed = json.loads(c_bytes.decode("utf-8"))
                    sched = parsed.get("schedule")
                    sched_tz = parsed.get("schedule_timezone")
                except Exception as exc:
                    raise CronAuthorityError(
                        f"Corrupt v2 snapshot bytes: {exc}",
                        code="corrupt_snapshot_bytes",
                    ) from exc
                snapshots.append(
                    CronRegistrySnapshotRow(
                        source_id=r[0],
                        schema_id=r[1],
                        schema_version=r[2],
                        registry_generation=r[3],
                        snapshot_digest=r[4],
                        canonical_bytes=c_bytes,
                        created_at=r[6],
                        schedule=sched,
                        schedule_timezone=sched_tz,
                        schedule_available=True,
                        schedule_unavailable_reason=None,
                    )
                )

        snapshots.sort(key=lambda s: (s.registry_generation, s.source_id))

        has_del = (
            cursor.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name='cron_trigger_deliveries';"
            ).fetchone()
            is not None
        )
        trigger_deliveries: list[CronTriggerDeliveryRow] = []
        if has_del:
            del_rows = cursor.execute(
                """
                SELECT trigger_event_id, trigger_digest, canonical_bytes, trigger_kind, transport_kind,
                       cron_id, registry_generation, schedule_bucket, command_digest, release_digest,
                       execution_id, disposition, reason_code, submitted_at, created_at
                FROM main.cron_trigger_deliveries
                ORDER BY submitted_at ASC, trigger_event_id ASC;
                """
            ).fetchall()
            for r in del_rows:
                trigger_deliveries.append(
                    CronTriggerDeliveryRow(
                        trigger_event_id=r[0],
                        trigger_digest=r[1],
                        canonical_bytes=bytes(r[2]),
                        trigger_kind=r[3],
                        transport_kind=r[4],
                        cron_id=r[5],
                        registry_generation=r[6],
                        schedule_bucket=r[7],
                        command_digest=r[8],
                        release_digest=r[9],
                        execution_id=r[10],
                        disposition=r[11],
                        reason_code=r[12],
                        submitted_at=r[13],
                        created_at=r[14],
                    )
                )

        agg_rows = cursor.execute(
            """
            SELECT execution_id, cron_id, registry_generation, schedule_bucket, command_digest, release_digest, created_at
            FROM main.cron_execution_aggregates
            ORDER BY created_at ASC, execution_id ASC;
            """
        ).fetchall()
        execution_aggregates = [
            CronExecutionAggregateRow(
                execution_id=r[0],
                cron_id=r[1],
                registry_generation=r[2],
                schedule_bucket=r[3],
                command_digest=r[4],
                release_digest=r[5],
                created_at=r[6],
            )
            for r in agg_rows
        ]

        att_rows = cursor.execute(
            """
            SELECT execution_id, attempt, state, runner_id, fence_token, lease_expires_at,
                   source_id, schema_id, schema_version, registry_generation, snapshot_digest,
                   canonical_snapshot_bytes, trusted_runner_identity, command_digest, release_digest,
                   dependency_digest, created_at, updated_at
            FROM main.cron_execution_attempts
            ORDER BY created_at ASC, execution_id ASC, attempt ASC;
            """
        ).fetchall()
        execution_attempts = [
            CronExecutionAttemptRow(
                execution_id=r[0],
                attempt=r[1],
                state=r[2],
                runner_id=r[3],
                fence_token=r[4],
                lease_expires_at=r[5],
                source_id=r[6],
                schema_id=r[7],
                schema_version=r[8],
                registry_generation=r[9],
                snapshot_digest=r[10],
                canonical_snapshot_bytes=bytes(r[11]) if r[11] is not None else None,
                trusted_runner_identity=r[12],
                command_digest=r[13],
                release_digest=r[14],
                dependency_digest=r[15],
                created_at=r[16],
                updated_at=r[17],
            )
            for r in att_rows
        ]

        rcpt_rows = cursor.execute(
            """
            SELECT receipt_id, execution_id, attempt, cron_id, outcome, runner_id, runner_release_digest,
                   started_at, finished_at, error_classification, evidence_digest, signing_key_id, signature,
                   schema_version, created_at
            FROM main.cron_receipts
            ORDER BY created_at ASC, receipt_id ASC;
            """
        ).fetchall()
        terminal_receipts = [
            CronReceiptRow(
                receipt_id=r[0],
                execution_id=r[1],
                attempt=r[2],
                cron_id=r[3],
                outcome=r[4],
                runner_id=r[5],
                runner_release_digest=r[6],
                started_at=r[7],
                finished_at=r[8],
                error_classification=r[9],
                evidence_digest=r[10],
                signing_key_id=r[11],
                signature=r[12],
                schema_version=r[13],
                created_at=r[14],
            )
            for r in rcpt_rows
        ]

        cur_rows = cursor.execute(
            """
            SELECT scope_key, cron_id, registry_generation, command_digest, catch_up_policy_version, cursor_value, updated_at
            FROM main.cron_sweep_cursors
            ORDER BY updated_at ASC, scope_key ASC;
            """
        ).fetchall()
        sweep_cursors = [
            CronSweepCursorRow(
                scope_key=r[0],
                cron_id=r[1],
                registry_generation=r[2],
                command_digest=r[3],
                catch_up_policy_version=r[4],
                cursor_value=r[5],
                updated_at=r[6],
            )
            for r in cur_rows
        ]

        return CronProjectionSource(
            registry_snapshots=tuple(snapshots),
            trigger_deliveries=tuple(trigger_deliveries),
            execution_aggregates=tuple(execution_aggregates),
            execution_attempts=tuple(execution_attempts),
            terminal_receipts=tuple(terminal_receipts),
            sweep_cursors=tuple(sweep_cursors),
        )
    finally:
        conn.close()
