# Dispatch Cursor Generation Safety

This document outlines the safety architecture, database generation identity, versioned state envelope, fail-closed gate semantics, cursor serialization, plan authentication, and repair primitives for the source-owned dispatch consumer (`prismatic/gateway/event_handlers/dispatch_consumer_v3.py`).

## Overview

The source-owned dispatch consumer watches the SQLite event bus and dispatches supervisor workflows. To guarantee safety and prevent state corruption or replay storms across bus database replacements, the consumer enforces strict database identity binding, versioned cursor envelopes, fail-closed startup and polling validation, shared cursor locking, deterministic plan recomputation, atomic durability, and explicit repair primitives.

## 1. Database Generation Identity

- **Metadata Storage**: A durable, randomly generated database generation identifier (canonical UUID v4 string) is stored in SQLite metadata owned by the consumer schema in table `dispatch_consumer_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)` under key `'db_generation'`.
- **Transactional & Idempotent**: Created transactionally on schema setup (`INSERT OR IGNORE`). Concurrent connections resolve to the exact same generation UUID.
- **Strict Canonical UUID v4 Validation**: Generation strings are strictly validated as canonical lowercase UUID v4 text (36 characters, `8-4-4-4-12` hyphenated hex digits). Rejects nil UUID (`00000000-0000-0000-0000-000000000000`), UUID v1/v3/v5, uppercase hex, braces (`{...}`), whitespace, and non-canonical forms.
- **Bus Identity**: A SQLite database at the same filesystem path with a different generation UUID is treated as a distinct bus, preventing accidental cursor application across database replacements or recreations.

## 2. Versioned Cursor State Envelope

The cursor state file (`$PRISMATIC_HOME/bus/dispatch_consumer.rowid`) uses a strict versioned JSON envelope format:

```json
{
  "schema_version": 1,
  "last_rowid": 120,
  "db_path": "/home/ubuntu/.prismatic/bus/event_log.sqlite",
  "db_generation": "3f2a1b0c-4d5e-4f7a-8b9c-0d1e2f3a4b5c",
  "updated_at": "2026-07-22T20:00:00.000000Z"
}
```

### Strict Validation Rules

State reading and parsing rejects and fails closed on:
- Missing, unknown, or duplicate JSON keys at any nesting level.
- Non-object JSON roots or syntax errors.
- Oversized state files (> 16 KB) or 0-byte empty files.
- Control characters or invalid UTF-8 bytes.
- Non-integer, negative, boolean, float, string, or oversized `last_rowid` values.
- Non-canonical, relative, or symlinked `db_path` values.
- Non-canonical UUID v4 strings or invalid timestamps.
- **Strict Canonical UTC Timestamp Envelope**: Timestamps must use the strict UTC format `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. Rejects space separators, missing timezones, non-UTC offsets (`+00:00`), offset-without-colon, excessive fraction digits (> 6 digits), week dates, and alternate ISO forms. All writer-generated timestamps use this exact canonical envelope format.
- Symlinks, directories, non-regular files, or group/world-accessible file permissions (`st_mode & 0o077 != 0`).
- Database generation mismatches or unresolvable DB identity.

## 3. Fail-Closed Startup & Polling Gate & Side-Effect Binding

Before reading events, spawning workers, or interacting with Linear:
1. The consumer reads the configured database path and cursor state file on startup.
2. It validates the cursor schema version and canonical generation UUID against the live database using pure read-only queries (`mode=ro`).
3. It queries current `MAX(rowid)` from the `events` table in read-only mode.
4. It verifies that `last_rowid <= MAX(rowid)` and that DB path and generation match.
5. **Generation Bound Across All Side Effects**: `expected_generation` is carried from startup through every poll, event-processing call, claim, mark-processed, and vacuum operation:
   - `fetch_new_events()` verifies generation in the same read connection that fetches event rows.
   - `claim_event_for_processing()` and `mark_processed()` verify generation inside their write transactions before mutating any processed ledger or event row.
   - `vacuum_processed()` accepts expected generation and validates it inside the write transaction before row deletion.
   - **Pre-Side-Effect Revalidation**: Immediately before calling the Linear API (`fetch_issue`) and immediately before spawning the supervisor subprocess (`dispatch_to_supervisor`), the consumer revalidates DB generation and canonical identity (`verify_db_generation_and_identity`). Any mismatch or database replacement raises fail-closed immediately with zero downstream side effects.
6. If any validation step fails, the consumer **fails closed immediately**, emits a compact diagnostic log, and logs the failure marker `PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED`.
7. No automatic state resets, replays, processed markers, Linear calls, or supervisor spawns occur when failed closed.
8. Only when all validation checks pass does the consumer emit `MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK` and proceed to poll events.

## 4. Cursor Serialization & Lock Primitive

To eliminate race conditions between consumer cursor advancement and repair apply:
- **Shared CursorLock Primitive**: Introduces a restrictive, no-follow shared primitive (`CursorLock`) using a lock file (`<state_file>.lock`) opened with `O_RDWR|O_CREAT|O_NOFOLLOW` and mode `0o600` (`fcntl.flock` exclusive lock).
- **Repair Lock Window**: Repair apply holds `CursorLock` from plan revalidation through backup creation and final cursor write.
- **SQLite Exclusion Continuity**: Repair apply holds the SQLite writer-exclusion transaction (`BEGIN EXCLUSIVE`) until the repaired cursor state file is durably written to disk.
- **Deadlock Avoidance**: Public wrapper `write_cursor_state()` acquires `CursorLock` and calls internal already-locked helper `_write_cursor_state_unlocked()`. Repair apply calls `_write_cursor_state_unlocked()` directly while holding `CursorLock`, avoiding recursive lock deadlocks.
- **Consumer Advancement Exclusion**: Consumer cursor advancement (`set_state`) cannot run while repair is holding `CursorLock`, preventing consumer cursor advancement from being overwritten or racing with repair apply.

## 5. Mandatory Durability & Cleanup Failure Propagation

All state updates and backup writes follow strict OS-safe atomic durability and error propagation rules:
- Validates all fields of the state envelope in memory before attempting file operations.
- Rejects writing if the target path is a symlink, directory, non-regular file, or group/world-accessible.
- Writes to a temporary file (`.dispatch_cursor_tmp_*`) created in the cursor file's directory with permissions restricted to `0o600` (`-rw-------`).
- Executes `flush()`, file descriptor `os.fsync()`, atomic `os.replace()`, and parent directory `os.fsync()`.
- **Directory Fsync Propagation**: `write_cursor_state()` propagates parent-directory open/fsync/close failures directly; it never reports success after a swallowed durability failure.
- **Cleanup Error Preservation**: Cleanup failures are never swallowed. If primary and cleanup failures coexist, both are reported using `ExceptionGroup` while preserving the primary exception.
- **Artifact Cleanup Boundary**: On failure during backup creation, newly created temporary/backup artifacts are safely removed with parent directory fsync. Pre-existing collision files are never deleted or modified.

## 6. Legacy Cursor Migration & Inspection

- **Legacy Detection**: Plain decimal integer text files (e.g. `"123\n"`) are explicitly recognized as legacy format.
- **No Auto-Migration**: Startup will not auto-migrate legacy files to JSON envelopes. Legacy state fails closed during startup until explicitly repaired.
- **Pure Read-Only Inspection**: `--inspect` provides a read-only report detailing database generation, max rowid, cursor format, readiness status, proposed bound state, and machine marker. On a database missing metadata or schema, inspect opens read-only (`mode=ro`) and byte-for-byte mutates nothing.

## 7. Repair Workflow: Dry-Run & Apply

### Repair Dry-Run (`--repair-dry-run`)
Computes a deterministic migration/repair plan:
- **Strict Target Validation**: Requires exact built-in integer `target_rowid` (`0 <= target <= max_rowid`), rejecting boolean, float, string, negative, or oversized targets.
- **Explicit Target Requirement**: Malformed, missing, generation-mismatch, or ahead cursors require an explicit target; dry-run will not silently infer max or replay. Legacy cursors at or below max may propose preserving their exact rowid.
- **Deterministic Plan ID & Destinations**: Generates a content-bound plan ID derived from source DB hash, cursor hash, generation, path, and explicit target. Backup paths are bound deterministically (`<db_path>.backup.plan_<plan_id>`), guaranteeing that consecutive dry-run calls with identical inputs return identical output.
- **Zero Side Effects**: Byte-for-byte mutates nothing, including when metadata or schema is missing.

### Repair Apply (`--repair-apply`)
Performs state repair and migration:
- **Confirmation Token**: Requires `--confirm I_ACCEPT_CURSOR_REPAIR_RISK`. Refuses execution if missing or incorrect.
- **Supplied Plan Authentication & Recomputation**: Always recomputes the deterministic plan from current source state and requested target. If a caller passes `plan`, it requires exact deep equality agreement with the recomputed plan before using any field. Tampered plan IDs, backup paths, target rowids, or extra/missing fields are rejected before creating backups or mutating state. If source files changed since dry-run, source drift is reported.
- **Fail-Closed Coherent Lock Window**: Acquires `CursorLock` and an exclusive SQLite write lock (`BEGIN EXCLUSIVE`) to exclude concurrent writers for the full source-hash, backup-copy, and cursor-write window.
- **WAL-Mode Raw Backup Set**: Performs raw byte-for-byte copies of the main DB (`<db_path>`), the WAL file if present and non-empty (`<db_path>-wal`), and the cursor state file (`<state_file>`). Source descriptors are opened securely with `O_RDONLY|O_NOFOLLOW` and verified via `fstat` and inode/device binding against pre-open stat to eliminate TOCTOU risks. Backup files use restrictive `0600` permissions created with `O_CREAT|O_EXCL`, with mandatory fsync on both file and parent directory. Any backup failure safely cleans up newly created member backups with parent directory fsync while preserving original exceptions via `ExceptionGroup`.
- **SHM Regenerability Boundary**: The SQLite shared-memory file (`-shm`) is treated as regenerable index state and is not backed up; SQLite automatically regenerates `-shm` upon reopening restored database files.
- **Exact Byte Identity**: Proves exact byte-for-byte SHA-256 identity between original source files and backup files (`src_db_sha256 == db_backup_sha256`, `src_wal_sha256 == wal_backup_sha256`, `src_cursor_sha256 == cursor_backup_sha256`).
- **Atomic Destination Backups**: Uses the exact proposed backup destination paths from the plan. Refuses execution if destination files already exist (backup collisions).
- **Data Preservation**: Never deletes, rewrites, or mutates rows in `events` or `processed_event_keys`. Apply mutates only the cursor state envelope.
- **Hash-Bound Receipt**: Emits a JSON receipt containing plan ID, source hashes/sizes, generation, exact destination paths, backup hashes/sizes, updated state envelope, final file hash, apply timestamp, member array with exact byte-match booleans, and machine marker `PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK`.
- **Side-Effect Boundary**: Does NOT call Linear API, spawn AGY agents/supervisors, publish completion events, change concurrency, enable services, or alter production runtime. Credentials and event payloads are excluded from diagnostic output.

## 8. Verification

Verification tests in `tests/test_dispatch_consumer_cursor_generation.py` (34 isolated regression tests) validate all safety guarantees:
1. Real OS process-level contention test (`ProcessPoolExecutor`) ensuring concurrent schema initializations produce one identical canonical UUID generation.
2. WAL-mode raw backup and rollback regression test verifying exact byte-for-byte SHA-256 matches for main DB, WAL file, and cursor state file, followed by SHM deletion, restoration, SQLite reopening, and verification of uncorrupted generation, max-rowid, and event row integrity.
3. Adversarial regressions proving database replacement after fetch, claim, Linear, or spawn fails closed with zero side effects.
4. Process concurrency tests proving consumer cursor advancement cannot occur while repair holds `CursorLock`.
5. Supplied plan authentication tests proving tampered plans are rejected before backup creation or cursor mutation.
6. Durability tests verifying directory fsync and cleanup error propagation with `ExceptionGroup`.
7. Canonical UUID v4 and UTC ISO timestamp validation tests.
