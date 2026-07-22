# Dispatch Cursor Generation Safety

This document outlines the safety architecture, database generation identity, versioned state envelope, fail-closed gate semantics, and repair primitives for the source-owned dispatch consumer (`prismatic/gateway/event_handlers/dispatch_consumer_v3.py`).

## Overview

The source-owned dispatch consumer watches the SQLite event bus and dispatches supervisor workflows. To guarantee safety and prevent state corruption or replay storms across bus database replacements, the consumer enforces strict database identity binding, versioned cursor envelopes, fail-closed startup validation, atomic durability, and explicit repair primitives.

## 1. Database Generation Identity

- **Metadata Storage**: A durable, randomly generated database generation identifier (UUID v4) is stored in SQLite metadata owned by the consumer schema in table `dispatch_consumer_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)` under key `'db_generation'`.
- **Transactional & Idempotent**: Created transactionally on schema setup (`INSERT OR IGNORE`). Concurrent connections resolve to the exact same generation UUID.
- **Strict Validation**: Generation strings are validated for non-empty alphanumeric/hyphen formatting (length 8–128).
- **Bus Identity**: A SQLite database at the same filesystem path with a different generation UUID is treated as a distinct bus, preventing accidental cursor application across database replacements or recreations.

## 2. Versioned Cursor State Envelope

The cursor state file (`$PRISMATIC_HOME/bus/dispatch_consumer.rowid`) uses a strict versioned JSON envelope format:

```json
{
  "schema_version": 1,
  "last_rowid": 120,
  "db_path": "/home/ubuntu/.prismatic/bus/event_log.sqlite",
  "db_generation": "3f2a1b0c-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
  "updated_at": "2026-07-22T20:00:00.000000+00:00"
}
```

### Strict Validation Rules

State reading rejects and fails closed on:
- Missing, unknown, or duplicate JSON keys at any nesting level.
- Non-object JSON roots or syntax errors.
- Oversized state files (> 16 KB) or 0-byte empty files.
- Control characters or invalid UTF-8 bytes.
- Non-integer, negative, boolean, float, string, or oversized `last_rowid` values.
- Non-canonical, relative, or symlinked `db_path` values.
- Symlinks, directories, non-regular files, or group/world-accessible file permissions (`st_mode & 0o077 != 0`).
- Database generation mismatches or unresolvable DB identity.

## 3. Fail-Closed Startup & Polling Gate

Before reading events, spawning workers, or interacting with Linear:
1. The consumer reads the configured database path and cursor state file.
2. It validates the cursor schema version and generation UUID against the live database.
3. It queries the current `MAX(rowid)` from the `events` table.
4. It verifies that `last_rowid <= MAX(rowid)`.
5. If any validation step fails (cursor ahead of max, generation mismatch, legacy format, malformed state, missing file), the consumer **fails closed immediately**, emits a compact diagnostic log, and logs the failure marker `PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED`.
6. No automatic state resets, replays, processed markers, Linear calls, or supervisor spawns occur when failed closed.
7. Only when all validation checks pass does the consumer emit `MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK` and proceed to poll events.

## 4. Atomic State Writes

All state updates use an OS-safe atomic write procedure:
- Writes to a temporary file (`.dispatch_cursor_tmp_*`) created in the cursor file's directory.
- Restricts permissions to `0o600` (`-rw-------`).
- Executes `flush()`, file descriptor `os.fsync()`, atomic `os.replace()`, and parent directory `os.fsync()`.
- On any failure prior to `os.replace()`, the temporary file is removed and the original state file remains untouched.

## 5. Legacy Cursor Migration & Inspection

- **Legacy Detection**: Plain decimal integer text files (e.g. `"123\n"`) are explicitly recognized as legacy format.
- **No Auto-Migration**: Startup will not auto-migrate legacy files to JSON envelopes. Legacy state fails closed during startup until explicitly repaired.
- **Inspect Primitive**: `--inspect` provides a read-only report detailing database generation, max rowid, cursor format, readiness status, proposed bound state, and machine marker without byte-for-byte mutating any file.

## 6. Repair Workflow: Dry-Run & Apply

### Repair Dry-Run (`--repair-dry-run`)
Computes a deterministic migration/repair plan:
- Resolves target `last_rowid` conservatively (e.g., `min(cursor_rowid, max_rowid)`).
- Constructs the proposed JSON envelope bound to current DB generation and canonical path.
- Computes proposed timestamped backup paths and SHA-256 hashes of database and cursor files.
- Mutates byte-for-byte NOTHING.

### Repair Apply (`--repair-apply`)
Performs state repair and migration:
- **Confirmation Token**: Requires `--confirm I_ACCEPT_CURSOR_REPAIR_RISK`. Refuses execution if missing or incorrect.
- **Atomic Backups**: Creates timestamped backups (`<db_path>.backup.<timestamp>` and `<cursor_path>.backup.<timestamp>`) using SQLite's consistent Backup API for the database and direct byte copying for the cursor file. Refuses path collisions.
- **Data Preservation**: Never deletes, rewrites, or mutates rows in `events` or `processed_event_keys`.
- **Hash-Bound Receipt**: Emits a JSON receipt containing source hashes, backup paths, backup hashes, updated state envelope, and new file hash bound to marker `PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK`.
- **Side-Effect Boundary**: Does NOT call Linear API, spawn AGY agents/supervisors, publish completion events, change concurrency, enable services, or alter production runtime.

## 7. Verification

Verification tests in `tests/test_dispatch_consumer_cursor_generation.py` validate all 18 core safety guarantees, including concurrency, fail-closed bounds, atomic write durability, backup collision prevention, hash restoration on rollback, and side-effect isolation.
