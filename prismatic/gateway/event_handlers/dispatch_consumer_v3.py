"""Phase 3 v2 (rowid+atomic): dispatch_consumer that polls the SQLite bus.

Improvements over v2:
- Use rowid (monotonic, restart-safe) instead of wall-clock ts (skew-prone).
- Atomic per-event processing: persistent dedup claim + UPDATE processed=1
  + COMMIT before spawn, with a dedicated processed column.
- Cold-start guard & generation safety: require strict versioned cursor state envelope.
- Database generation identity: store durable UUID in SQLite metadata.
- Fail closed on generation mismatch, cursor ahead of max, legacy format, or malformed state.
- Inspect / repair dry-run / repair apply primitives with atomic timestamped backups.
- Persistent dedup keys suppress exact bus-event replays across restarts.
- 60-second issue_id dedup window still suppresses distinct Linear retries
  and noisy update events.
- Filter: only spawn supervisor on `dispatch:ready` label changes or
  status changes that put issue in Backlog/Todo with a `dispatch:*` label.

Schema:
    events(
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT UNIQUE,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        ts REAL NOT NULL,
        processed INTEGER DEFAULT 0
    )
    processed_event_keys(
        dedup_key TEXT PRIMARY KEY,
        first_rowid INTEGER NOT NULL,
        topic TEXT NOT NULL,
        issue_id TEXT,
        processed_at REAL NOT NULL
    )
    dispatch_consumer_meta(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )

Reads versioned cursor envelope from `$PRISMATIC_HOME/bus/dispatch_consumer.rowid`
on startup, advances it after each row is processed.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict

if sys.version_info < (3, 11):
    try:
        from exceptiongroup import ExceptionGroup  # type: ignore[import-not-found]
    except ImportError:

        class ExceptionGroup(Exception):  # type: ignore[no-redef]
            def __init__(self, message: str, exceptions: list[BaseException]):
                super().__init__(message, exceptions)
                self.exceptions = exceptions


SCHEMA_VERSION = 1
MAX_STATE_FILE_SIZE = 16384  # 16 KB
CONFIRMATION_TOKEN = "I_ACCEPT_CURSOR_REPAIR_RISK"

CANONICAL_UTC_TS_REGEX = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?Z$"
)

LINEAR_KEY_ENV = "LINEAR_" + "API_KEY"
LINEAR_KEY = os.environ.get(LINEAR_KEY_ENV, "")
PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME") or (Path.home() / ".prismatic"))
ORCHESTRATOR_PROFILE_HOME = Path(
    os.environ.get("HERMES_ORCHESTRATOR_PROFILE_HOME")
    or (Path.home() / ".hermes" / "profiles" / "orchestrator")
)
DB_PATH = os.environ.get("PRISMATIC_BUS_DB") or str(
    PRISMATIC_HOME / "bus" / "event_log.sqlite"
)
POLL_INTERVAL = 3  # seconds
STATE_FILE = os.environ.get("PRISMATIC_CONSUMER_STATE_FILE") or str(
    PRISMATIC_HOME / "bus" / "dispatch_consumer.rowid"
)
DEDUP_WINDOW_SEC = 60  # suppress duplicate spawns for same issue_id within 60s
COLD_START_BACKOFF_SEC = 300  # on first start, ignore events older than 5 min
SUPERVISOR_PATH = os.environ.get("PRISMATIC_SUPERVISOR_PATH") or str(
    ORCHESTRATOR_PROFILE_HOME / "scripts" / "agy_sandbox_event_supervisor.py"
)
AGY_CLI_HOME_DEFAULT = str(
    Path("/home") / "ubuntu" / ".hermes" / "profiles" / "kai" / "home"
)
AGY_CLI_HOME = os.environ.get("AGY_CLI_HOME", AGY_CLI_HOME_DEFAULT)

# Issue-ID -> last dispatch time, for dedup window
_recent_dispatches: dict[str, float] = defaultdict(float)


def get_canonical_path(path_str: str) -> str:
    """Return the absolute canonical path (resolving symlinks and normalization)."""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = p.absolute()
    return str(p.resolve())


def _validate_state_path_strict(raw_path: str) -> str:
    """Validate cursor state path before resolution or lock creation.

    Rejects:
    - Symlink final component or symlink in parent path hierarchy
    - Non-regular existing target (directory, socket, FIFO, device)
    - Noncanonical alias (abspath != realpath)
    - Unsafe permissions if existing
    """
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("Cursor state path must be a non-empty string")

    if os.path.islink(raw_path):
        raise ValueError(f"Cursor state path is a symlink: {raw_path}")

    p = Path(raw_path).expanduser()
    abs_p = p.absolute()
    abs_str = str(abs_p)

    if os.path.islink(abs_str):
        raise ValueError(f"Cursor state path is a symlink: {raw_path}")

    try:
        st = os.lstat(raw_path)
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"Cursor state path is a symlink: {raw_path}")
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"Cursor state path is not a regular file: {raw_path}")
        if st.st_mode & 0o077 != 0:
            raise ValueError(
                f"Refusing to write cursor state: unsafe file permissions {oct(st.st_mode)} (group/world accessible)"
            )
    except FileNotFoundError:
        pass

    for parent in abs_p.parents:
        if os.path.islink(str(parent)):
            raise ValueError(
                f"Cursor state path parent component is a symlink: {parent}"
            )

    norm_abs = os.path.normpath(os.path.abspath(raw_path))
    real_p = os.path.realpath(raw_path)
    if norm_abs != real_p:
        raise ValueError(
            f"Cursor state path is noncanonical or contains symlink aliases: {raw_path!r} vs {real_p!r}"
        )

    return norm_abs


def _validate_db_path_strict(raw_path: str) -> str:
    """Validate database path before resolution or file operations."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("Database path must be a non-empty string")

    if os.path.islink(raw_path):
        raise ValueError(f"Database path is a symlink: {raw_path}")

    p = Path(raw_path).expanduser()
    abs_p = p.absolute()
    abs_str = str(abs_p)

    if os.path.islink(abs_str):
        raise ValueError(f"Database path is a symlink: {raw_path}")

    try:
        st = os.lstat(raw_path)
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"Database path is a symlink: {raw_path}")
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"Database path is not a regular file: {raw_path}")
    except FileNotFoundError:
        pass

    for parent in abs_p.parents:
        if os.path.islink(str(parent)):
            raise ValueError(f"Database path parent component is a symlink: {parent}")

    norm_abs = os.path.normpath(os.path.abspath(raw_path))
    real_p = os.path.realpath(raw_path)
    if norm_abs != real_p:
        raise ValueError(
            f"Database path is noncanonical or contains symlink aliases: {raw_path!r} vs {real_p!r}"
        )

    return norm_abs


def validate_generation_format(gen: str) -> bool:
    """Validate database generation UUID string strictly as canonical lowercase UUID v4 text."""
    if not isinstance(gen, str) or len(gen) != 36:
        return False
    if gen != gen.lower() or " " in gen or "{" in gen or "}" in gen:
        return False
    try:
        u = uuid.UUID(gen)
        if u.version != 4 or u.int == 0:
            return False
        return str(u) == gen
    except Exception:
        return False


def validate_iso_timestamp(ts_str: str) -> bool:
    """Validate updated_at strictly as canonical UTC ISO string YYYY-MM-DDTHH:MM:SS[.ffffff]Z."""
    if not isinstance(ts_str, str) or not ts_str:
        return False
    if not CANONICAL_UTC_TS_REGEX.match(ts_str):
        return False
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.tzinfo is not None
    except Exception:
        return False


def get_canonical_utc_now() -> str:
    """Return current UTC time in strict canonical format YYYY-MM-DDTHH:MM:SS.ffffffZ."""
    dt = datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class CursorLock:
    """Restrictive, no-follow canonical cursor lock file shared primitive."""

    def __init__(self, state_file_path: str):
        _validate_state_path_strict(state_file_path)
        self.state_file_path = get_canonical_path(state_file_path)
        self.lock_file_path = self.state_file_path + ".lock"
        self._fd = None

    def acquire(self, *, blocking: bool = True) -> CursorLock:
        if self._fd is not None:
            return self
        if os.path.islink(self.lock_file_path):
            raise ValueError("Cursor lock file is a symlink")
        target_dir = Path(self.lock_file_path).parent
        target_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self._fd = os.open(self.lock_file_path, flags, 0o600)
        try:
            st = os.fstat(self._fd)
            if st.st_mode & 0o077 != 0:
                os.fchmod(self._fd, 0o600)
            lock_operation = fcntl.LOCK_EX
            if not blocking:
                lock_operation |= fcntl.LOCK_NB
            fcntl.flock(self._fd, lock_operation)
        except Exception:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
            raise
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        unlock_exc = None
        close_exc = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception as e:
            unlock_exc = e
        finally:
            try:
                os.close(fd)
            except Exception as e:
                close_exc = e

        if unlock_exc and close_exc:
            raise ExceptionGroup(
                "Cursor lock release failed on unlock and close",
                [unlock_exc, close_exc],
            )
        elif unlock_exc:
            raise unlock_exc
        elif close_exc:
            raise close_exc

    def __enter__(self) -> CursorLock:
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            if exc_type is not None:
                try:
                    self.release()
                except Exception as rel_exc:
                    raise ExceptionGroup(
                        "Primary operation failed and lock release encountered errors",
                        [exc_val, rel_exc],
                    ) from exc_val
            else:
                self.release()


def get_db_max_rowid_and_generation_readonly(
    db_path: str | None = None,
) -> tuple[int | None, str | None]:
    """Pure read-only query of MAX(rowid) and db_generation. Byte-for-byte mutates nothing.

    Returns (max_rowid, db_generation). If DB does not exist, events table is missing,
    or metadata/generation is missing/invalid, returns None for the missing component(s).
    """
    effective_db = DB_PATH if db_path is None else db_path
    canonical_db_path = get_canonical_path(effective_db)
    if not os.path.exists(canonical_db_path):
        return None, None
    if os.path.islink(canonical_db_path) or os.path.islink(effective_db):
        return None, None

    db_uri = f"file:{canonical_db_path}?mode=ro"
    try:
        conn = sqlite3.connect(db_uri, uri=True, timeout=5)
        try:
            db_gen = None
            try:
                cur = conn.execute(
                    "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    gen_str = str(row[0])
                    if validate_generation_format(gen_str):
                        db_gen = gen_str
            except sqlite3.OperationalError:
                pass

            max_rowid = None
            try:
                cur = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM events")
                row = cur.fetchone()
                if row is not None:
                    max_rowid = int(row[0] or 0)
            except sqlite3.OperationalError:
                pass

            return max_rowid, db_gen
        finally:
            conn.close()
    except sqlite3.Error:
        return None, None


def ensure_db_generation(conn: sqlite3.Connection) -> str:
    """Store a durable randomly generated database generation identifier in SQLite metadata.

    Creation is transactional and idempotent under multiple connections.
    """
    try:
        cur = conn.execute(
            "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
        )
        row = cur.fetchone()
        if row and row[0]:
            gen = str(row[0])
            if validate_generation_format(gen):
                return gen
    except sqlite3.OperationalError:
        pass

    in_tx = conn.in_transaction
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dispatch_consumer_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cur = conn.execute(
        "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
    )
    row = cur.fetchone()
    if row and row[0]:
        gen = str(row[0])
    else:
        gen = str(uuid.uuid4())
        conn.execute(
            "INSERT OR IGNORE INTO dispatch_consumer_meta (key, value) VALUES ('db_generation', ?)",
            (gen,),
        )
        cur = conn.execute(
            "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
        )
        gen = str(cur.fetchone()[0])

    if not in_tx:
        conn.commit()

    if not validate_generation_format(gen):
        raise ValueError(f"Database generation format invalid: {gen!r}")
    return gen


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate the event bus schema used by the consumer."""
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('events', 'processed_event_keys', 'dispatch_consumer_meta')"
        )
        tables = {row[0] for row in cur.fetchall()}
        if len(tables) == 3:
            ensure_db_generation(conn)
            return
    except sqlite3.OperationalError:
        pass

    in_tx = conn.in_transaction
    ensure_db_generation(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT UNIQUE,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            ts REAL NOT NULL,
            processed INTEGER DEFAULT 0
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "processed" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN processed INTEGER DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_event_keys (
            dedup_key TEXT PRIMARY KEY,
            first_rowid INTEGER NOT NULL,
            topic TEXT NOT NULL,
            issue_id TEXT,
            processed_at REAL NOT NULL
        )
        """
    )
    if not in_tx:
        conn.commit()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    d: dict[str, object] = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"Duplicate JSON key at nesting level: {k!r}")
        d[k] = v
    return d


def read_cursor_state(
    state_file_path: str | None = None,
) -> tuple[dict | None, str, str]:
    """Read and strictly validate the cursor state file using no-follow/fstat.

    Returns:
        (state_dict_or_none, status_code, diagnostic_message)
        status_code in {'MISSING', 'LEGACY', 'VALID', 'INVALID'}
    """
    effective_state = STATE_FILE if state_file_path is None else state_file_path
    state_path_obj = Path(effective_state).expanduser()
    if not state_path_obj.is_absolute():
        state_path_obj = state_path_obj.absolute()

    if not state_path_obj.exists():
        return None, "MISSING", f"Cursor state file does not exist at {state_path_obj}"

    if state_path_obj.is_symlink() or os.path.islink(effective_state):
        return None, "INVALID", "Cursor state file is a symlink"

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(state_path_obj), flags)
    except Exception as e:
        return None, "INVALID", f"Failed to open cursor state file: {e}"

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return (
                None,
                "INVALID",
                "Cursor state file is not a regular file (e.g. directory or FIFO)",
            )
        if st.st_mode & 0o077 != 0:
            return (
                None,
                "INVALID",
                f"Unsafe cursor state file permissions: {oct(st.st_mode)} (group/world accessible)",
            )
        if st.st_size == 0:
            return None, "INVALID", "Cursor state file is empty (0 bytes)"
        if st.st_size > MAX_STATE_FILE_SIZE:
            return (
                None,
                "INVALID",
                f"Cursor state file size ({st.st_size} bytes) exceeds limit ({MAX_STATE_FILE_SIZE} bytes)",
            )

        raw_bytes = os.read(fd, MAX_STATE_FILE_SIZE + 1)
    finally:
        os.close(fd)

    try:
        content_str = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, "INVALID", "Cursor state file is not valid UTF-8 text"

    for char in content_str:
        code = ord(char)
        if (code < 32 and code not in (9, 10, 13)) or code == 127:
            return (
                None,
                "INVALID",
                f"Cursor state file contains invalid control character ASCII {code}",
            )

    stripped = content_str.strip()
    if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
        try:
            val = int(stripped)
            if val < 0 or val > 2**63 - 1:
                return None, "INVALID", f"Legacy cursor integer out of range: {val}"
            return (
                {"last_rowid": val, "legacy": True},
                "LEGACY",
                f"Legacy decimal integer cursor: {val}",
            )
        except Exception:
            return (
                None,
                "INVALID",
                f"Malformed legacy numeric cursor string: {stripped!r}",
            )

    try:
        data = json.loads(content_str, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError:
        return (
            None,
            "INVALID",
            "Cursor state file is neither valid JSON nor a legacy decimal integer",
        )
    except ValueError as ve:
        return None, "INVALID", str(ve)

    if not isinstance(data, dict):
        return None, "INVALID", "Cursor state JSON envelope must be an object"

    expected_keys = {
        "schema_version",
        "last_rowid",
        "db_path",
        "db_generation",
        "updated_at",
    }
    actual_keys = set(data.keys())
    if actual_keys != expected_keys:
        return (
            None,
            "INVALID",
            f"Cursor state envelope key mismatch: expected {expected_keys}, got {actual_keys}",
        )

    sv = data["schema_version"]
    if type(sv) is not int or isinstance(sv, bool) or sv != SCHEMA_VERSION:
        return (
            None,
            "INVALID",
            f"Invalid schema_version: {sv!r} (expected int {SCHEMA_VERSION})",
        )

    rid = data["last_rowid"]
    if type(rid) is not int or isinstance(rid, bool) or rid < 0 or rid > 2**63 - 1:
        return (
            None,
            "INVALID",
            f"Invalid last_rowid: {rid!r} (expected non-negative int)",
        )

    dbp = data["db_path"]
    if type(dbp) is not str or not os.path.isabs(dbp) or os.path.realpath(dbp) != dbp:
        return (
            None,
            "INVALID",
            f"Invalid db_path in state: {dbp!r} (must be absolute canonical path)",
        )

    gen = data["db_generation"]
    if type(gen) is not str or not validate_generation_format(gen):
        return None, "INVALID", f"Invalid db_generation in state: {gen!r}"

    ts = data["updated_at"]
    if type(ts) is not str or not validate_iso_timestamp(ts):
        return (
            None,
            "INVALID",
            f"Invalid updated_at in state: {ts!r} (must be strict canonical UTC ISO string)",
        )

    return data, "VALID", "Cursor state envelope valid"


def validate_cursor_state_dict(state_data: dict) -> None:
    """Strictly validate in-memory state dictionary prior to writing."""
    if not isinstance(state_data, dict):
        raise ValueError("Cursor state data must be a dict")
    expected_keys = {
        "schema_version",
        "last_rowid",
        "db_path",
        "db_generation",
        "updated_at",
    }
    if set(state_data.keys()) != expected_keys:
        raise ValueError(
            f"Cursor state dict keys mismatch: expected {expected_keys}, got {set(state_data.keys())}"
        )
    sv = state_data["schema_version"]
    if type(sv) is not int or isinstance(sv, bool) or sv != SCHEMA_VERSION:
        raise ValueError(f"Invalid schema_version: {sv!r}")
    rid = state_data["last_rowid"]
    if type(rid) is not int or isinstance(rid, bool) or rid < 0 or rid > 2**63 - 1:
        raise ValueError(f"Invalid last_rowid: {rid!r}")
    dbp = state_data["db_path"]
    if type(dbp) is not str or not os.path.isabs(dbp) or os.path.realpath(dbp) != dbp:
        raise ValueError(f"Invalid db_path: {dbp!r}")
    gen = state_data["db_generation"]
    if type(gen) is not str or not validate_generation_format(gen):
        raise ValueError(f"Invalid db_generation: {gen!r}")
    ts = state_data["updated_at"]
    if type(ts) is not str or not validate_iso_timestamp(ts):
        raise ValueError(f"Invalid updated_at: {ts!r}")


def _write_cursor_state_unlocked(state_file_path: str, state_data: dict) -> None:
    """Internal atomic write helper assuming cursor lock is held."""
    validate_cursor_state_dict(state_data)
    canonical_state_path = get_canonical_path(state_file_path)

    if os.path.islink(state_file_path) or os.path.islink(canonical_state_path):
        raise ValueError(
            f"Refusing to write cursor state: target path is a symlink ({state_file_path})"
        )
    if os.path.exists(canonical_state_path):
        st = os.lstat(canonical_state_path)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(
                "Refusing to write cursor state: target exists and is not a regular file"
            )
        if st.st_mode & 0o077 != 0:
            raise ValueError(
                f"Refusing to write cursor state: unsafe file permissions {oct(st.st_mode)}"
            )

    target_dir = Path(canonical_state_path).parent
    target_dir.mkdir(parents=True, exist_ok=True)

    content = json.dumps(state_data, indent=2) + "\n"

    fd, temp_path = tempfile.mkstemp(
        dir=str(target_dir), prefix=".dispatch_cursor_tmp_"
    )
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, canonical_state_path)

        # Mandatory parent directory fsync - errors must propagate!
        dir_fd = os.open(str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception as primary_exc:
        cleanup_exc = None
        if os.path.exists(temp_path) or os.path.islink(temp_path):
            try:
                os.remove(temp_path)
                dir_fd = os.open(
                    str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception as ce:
                cleanup_exc = ce
        if cleanup_exc is not None:
            raise ExceptionGroup(
                "Failed to write cursor state and cleanup encountered an error",
                [primary_exc, cleanup_exc],
            )
        raise primary_exc


def _snapshot_cursor_file(canonical_state_path: str) -> tuple[bool, bytes | None]:
    """Snapshot prior cursor state strictly.

    Returns (prior_existed, prior_bytes).
    If file exists (even if 0 bytes), prior_existed is True and prior_bytes contains file content.
    If file does not exist, prior_existed is False and prior_bytes is None.
    If stat/open/read fails for any reason other than FileNotFoundError, raises the exception.
    """
    if os.path.islink(canonical_state_path):
        raise ValueError(f"Cursor state path is a symlink: {canonical_state_path}")
    try:
        st = os.lstat(canonical_state_path)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(
                f"Cursor state path is not a regular file: {canonical_state_path}"
            )
        with open(canonical_state_path, "rb") as f:
            prior_bytes = f.read()
        return True, prior_bytes
    except FileNotFoundError:
        return False, None


def _safe_rollback_cursor(
    canonical_state_path: str,
    prior_existed: bool,
    prior_bytes: bytes | None,
    written_bytes: bytes | None,
) -> bool:
    """Reacquire CursorLock and attempt safe reserialized rollback.

    Returns True only if prior state was safely restored and the rollback lock
    was released cleanly. Returns False if a contender wrote a new state, lock
    ownership remains uncertain, or comparison/restoration/release failed.
    """
    try:
        rollback_lock = CursorLock(canonical_state_path)
        # Lock ownership is uncertain after a release error. Never block here:
        # a close-before-effect failure can leave the original lock held, and a
        # blocking reacquire would deadlock the recovery path indefinitely.
        rollback_lock.acquire(blocking=False)
    except Exception:
        return False

    rollback_result = False
    release_failed = False
    try:
        curr_existed = os.path.exists(canonical_state_path) and not os.path.islink(
            canonical_state_path
        )
        curr_bytes = None
        if curr_existed:
            try:
                st = os.lstat(canonical_state_path)
                if stat.S_ISREG(st.st_mode):
                    with open(canonical_state_path, "rb") as f:
                        curr_bytes = f.read()
            except Exception:
                return False

        if written_bytes is not None and curr_existed and curr_bytes == written_bytes:
            try:
                _restore_or_remove_cursor(
                    canonical_state_path, prior_existed, prior_bytes
                )
                rollback_result = True
            except Exception:
                rollback_result = False
        else:
            rollback_result = False
    finally:
        try:
            rollback_lock.release()
        except Exception:
            release_failed = True

    return rollback_result and not release_failed


def write_cursor_state(state_file_path: str, state_data: dict) -> None:
    """Public wrapper acquiring CursorLock before writing cursor state."""
    _validate_state_path_strict(state_file_path)
    canonical_state_path = get_canonical_path(state_file_path)

    lock = CursorLock(canonical_state_path)
    lock.acquire()

    written_bytes = None
    try:
        prior_existed, prior_bytes = _snapshot_cursor_file(canonical_state_path)
        validate_cursor_state_dict(state_data)
        content_str = json.dumps(state_data, indent=2) + "\n"
        written_bytes = content_str.encode("utf-8")

        _write_cursor_state_unlocked(canonical_state_path, state_data)
    except Exception as body_exc:
        rel_exc = None
        try:
            lock.release()
        except Exception as re:
            rel_exc = re
        if rel_exc is not None:
            raise ExceptionGroup(
                "write_cursor_state failed and lock release encountered errors",
                [body_exc, rel_exc],
            ) from body_exc
        raise body_exc

    try:
        lock.release()
    except Exception as rel_exc:
        restored = _safe_rollback_cursor(
            canonical_state_path, prior_existed, prior_bytes, written_bytes
        )
        if restored:
            raise RuntimeError(
                f"[FAIL_CLOSED] Lock release failed after cursor write: {rel_exc}. Prior exact cursor restored.\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            ) from rel_exc
        else:
            raise RuntimeError(
                f"[FAIL_CLOSED] Lock release failed after cursor write: {rel_exc}. Later contender update preserved on disk.\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            ) from rel_exc


def verify_startup_gate(
    db_path: str | None = None, state_file_path: str | None = None
) -> tuple[bool, str, dict | None]:
    """Verify DB generation and cursor state envelope before polling or spawning."""
    effective_db = DB_PATH if db_path is None else db_path
    effective_state = STATE_FILE if state_file_path is None else state_file_path
    canonical_db_path = get_canonical_path(effective_db)
    max_rowid, db_gen = get_db_max_rowid_and_generation_readonly(effective_db)

    if max_rowid is None or db_gen is None:
        msg = f"[FAIL_CLOSED] Database error or identity unavailable: db_path={canonical_db_path!r}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    state, status_code, status_msg = read_cursor_state(effective_state)

    if status_code == "MISSING":
        msg = f"[FAIL_CLOSED] Cursor state file missing: {status_msg}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    if status_code == "INVALID":
        msg = f"[FAIL_CLOSED] Cursor state invalid: {status_msg}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    if status_code == "LEGACY":
        legacy_rowid = state["last_rowid"]
        if legacy_rowid > max_rowid:
            msg = f"[FAIL_CLOSED] Legacy cursor ahead of max: cursor={legacy_rowid} > max={max_rowid}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        else:
            msg = f"[FAIL_CLOSED] Legacy cursor format detected ({legacy_rowid}); explicit repair apply required\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    cursor_rowid = state["last_rowid"]
    cursor_db_path = state["db_path"]
    cursor_db_gen = state["db_generation"]

    if cursor_db_path != canonical_db_path:
        msg = f"[FAIL_CLOSED] Database path mismatch: state={cursor_db_path!r} vs configured={canonical_db_path!r}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    if cursor_db_gen != db_gen:
        msg = f"[FAIL_CLOSED] Database generation mismatch: state={cursor_db_gen!r} vs db={db_gen!r}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    if cursor_rowid > max_rowid:
        msg = f"[FAIL_CLOSED] Cursor ahead of max rowid: cursor={cursor_rowid} > max={max_rowid}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    msg = f"[OK] Cursor valid and bound: max_rowid={max_rowid}, cursor_rowid={cursor_rowid}, gen={db_gen}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK"
    return True, msg, state


def get_linear_api_key() -> str:
    if LINEAR_KEY:
        return LINEAR_KEY
    env_file = ORCHESTRATOR_PROFILE_HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if (
                LINEAR_KEY_ENV in line
                and "=" in line
                and not line.strip().startswith("#")
            ):
                v = line.split("=", 1)[1].strip().strip("'\"")
                if v:
                    return v
    return ""


def get_state() -> int:
    """Get last rowid processed after verifying startup gate. Fails closed if gate check fails."""
    is_ok, msg, state = verify_startup_gate(DB_PATH, STATE_FILE)
    print(msg)
    if not is_ok or not state:
        raise RuntimeError(f"Dispatch consumer failed closed on startup: {msg}")
    return state["last_rowid"]


def _read_db_identity_under_lock(
    canonical_db_path: str,
) -> tuple[str | None, int | None, os.stat_result]:
    if os.path.islink(canonical_db_path):
        raise ValueError(f"Database path is a symlink: {canonical_db_path}")
    st_pre = os.lstat(canonical_db_path)
    if not stat.S_ISREG(st_pre.st_mode):
        raise ValueError(f"Database path is not a regular file: {canonical_db_path}")

    db_uri = f"file:{canonical_db_path}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        db_gen = None
        try:
            cur = conn.execute(
                "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
            )
            row = cur.fetchone()
            if row and row[0]:
                gen_str = str(row[0])
                if validate_generation_format(gen_str):
                    db_gen = gen_str
        except sqlite3.OperationalError:
            pass

        max_rowid = None
        try:
            cur = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM events")
            row = cur.fetchone()
            if row is not None:
                max_rowid = int(row[0] or 0)
        except sqlite3.OperationalError:
            pass

        st_post = os.lstat(canonical_db_path)
        if st_pre.st_dev != st_post.st_dev or st_pre.st_ino != st_post.st_ino:
            return None, None, st_post

        return db_gen, max_rowid, st_post
    finally:
        conn.close()


def _restore_or_remove_cursor(
    canonical_state_path: str, prior_existed: bool, prior_bytes: bytes | None
) -> None:
    target_dir = Path(canonical_state_path).parent
    if prior_existed and prior_bytes is not None:
        fd, temp_path = tempfile.mkstemp(
            dir=str(target_dir), prefix=".dispatch_cursor_restore_tmp_"
        )
        temp_cleanup_err = None
        try:
            os.chmod(temp_path, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(prior_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, canonical_state_path)
            dir_fd = os.open(
                str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception as primary_exc:
            if os.path.exists(temp_path) or os.path.islink(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as ce:
                    raise ExceptionGroup(
                        "Cursor restoration failed and temp file cleanup failed",
                        [primary_exc, ce],
                    ) from primary_exc
            raise primary_exc
        finally:
            if os.path.exists(temp_path) or os.path.islink(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as ce:
                    temp_cleanup_err = ce
        if temp_cleanup_err is not None:
            raise temp_cleanup_err
    else:
        if os.path.exists(canonical_state_path) or os.path.islink(canonical_state_path):
            os.remove(canonical_state_path)
            dir_fd = os.open(
                str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


def set_state(
    rowid: int,
    expected_generation: str | None = None,
    db_path: str | None = None,
    state_file_path: str | None = None,
) -> None:
    """Atomically write updated versioned state envelope after verifying generation within one critical section."""
    effective_db = DB_PATH if db_path is None else db_path
    effective_state = STATE_FILE if state_file_path is None else state_file_path

    _validate_db_path_strict(effective_db)
    _validate_state_path_strict(effective_state)

    canonical_db_path = get_canonical_path(effective_db)
    canonical_state_path = get_canonical_path(effective_state)

    lock = CursorLock(canonical_state_path)
    lock.acquire()

    written_bytes = None
    try:
        prior_existed, prior_bytes = _snapshot_cursor_file(canonical_state_path)

        db_gen, max_rowid, pre_stat = _read_db_identity_under_lock(canonical_db_path)

        if db_gen is None:
            raise RuntimeError(
                "[FAIL_CLOSED] Database generation unavailable when updating state\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            )
        if expected_generation is not None and db_gen != expected_generation:
            raise RuntimeError(
                f"[FAIL_CLOSED] Database generation changed before state write: expected {expected_generation!r} vs db {db_gen!r}\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            )

        post_read_stat = os.lstat(canonical_db_path)
        if (
            post_read_stat.st_dev != pre_stat.st_dev
            or post_read_stat.st_ino != pre_stat.st_ino
        ):
            raise RuntimeError(
                "[FAIL_CLOSED] Database file replaced after identity read\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            )

        now_str = get_canonical_utc_now()
        state_data = {
            "schema_version": SCHEMA_VERSION,
            "last_rowid": rowid,
            "db_path": canonical_db_path,
            "db_generation": db_gen,
            "updated_at": now_str,
        }
        content_str = json.dumps(state_data, indent=2) + "\n"
        written_bytes = content_str.encode("utf-8")

        _write_cursor_state_unlocked(canonical_state_path, state_data)

        # Revalidate path identity & generation immediately AFTER write
        try:
            post_write_stat = os.lstat(canonical_db_path)
            if (
                post_write_stat.st_dev != pre_stat.st_dev
                or post_write_stat.st_ino != pre_stat.st_ino
            ):
                raise RuntimeError("Database file replaced immediately after write")

            post_db_gen, _, _ = _read_db_identity_under_lock(canonical_db_path)
            if post_db_gen != db_gen or (
                expected_generation is not None and post_db_gen != expected_generation
            ):
                raise RuntimeError(
                    "Database generation changed immediately after write"
                )
        except Exception as post_err:
            _restore_or_remove_cursor(canonical_state_path, prior_existed, prior_bytes)
            raise RuntimeError(
                f"[FAIL_CLOSED] Post-write validation detected replacement: {post_err}\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            ) from post_err

    except Exception as body_exc:
        rel_exc = None
        try:
            lock.release()
        except Exception as re:
            rel_exc = re
        if rel_exc is not None:
            raise ExceptionGroup(
                "set_state failed and lock release encountered errors",
                [body_exc, rel_exc],
            ) from body_exc
        raise body_exc

    try:
        lock.release()
    except Exception as rel_exc:
        restored = _safe_rollback_cursor(
            canonical_state_path, prior_existed, prior_bytes, written_bytes
        )
        if restored:
            raise RuntimeError(
                f"[FAIL_CLOSED] Lock release failed after set_state: {rel_exc}. Prior exact cursor restored.\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            ) from rel_exc
        else:
            raise RuntimeError(
                f"[FAIL_CLOSED] Lock release failed after set_state: {rel_exc}. Later contender update preserved on disk.\n"
                "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
            ) from rel_exc


def _stable_dedup_key(dedup_key: str | None, topic: str, payload_json: str) -> str:
    if dedup_key:
        return dedup_key
    payload_hash = hashlib.sha256(
        payload_json.encode("utf-8", errors="replace")
    ).hexdigest()
    return f"legacy:{topic}:{payload_hash}"


def verify_db_generation_and_identity(
    db_path: str | None = None, expected_generation: str = ""
) -> None:
    """Verify that database at db_path exists, is not a symlink, and has exact expected_generation."""
    effective_db = DB_PATH if db_path is None else db_path
    canonical_db = get_canonical_path(effective_db)
    if not os.path.exists(canonical_db) or os.path.islink(canonical_db):
        raise RuntimeError(
            f"[FAIL_CLOSED] DB missing or symlink: db_path={canonical_db!r}\n"
            "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        )
    if not validate_generation_format(expected_generation):
        raise RuntimeError(
            f"[FAIL_CLOSED] Invalid expected generation format: {expected_generation!r}\n"
            "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        )
    db_uri = f"file:{canonical_db}?mode=ro"
    try:
        conn = sqlite3.connect(db_uri, uri=True, timeout=5)
        try:
            cur = conn.execute(
                "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
            )
            row = cur.fetchone()
            db_gen = str(row[0]) if row and row[0] else None
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise RuntimeError(
            f"[FAIL_CLOSED] DB query error when verifying generation: {e}\n"
            "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        )

    if not db_gen or db_gen != expected_generation:
        raise RuntimeError(
            f"[FAIL_CLOSED] Database generation mismatch: expected {expected_generation!r} vs db {db_gen!r}\n"
            "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        )


def fetch_new_events(
    last_rowid: int,
    expected_generation: str | None = None,
    db_path: str | None = None,
) -> list[tuple]:
    effective_db = DB_PATH if db_path is None else db_path
    canonical_db = get_canonical_path(effective_db)
    if not os.path.exists(canonical_db):
        return []

    db_uri = f"file:{canonical_db}?mode=ro"
    try:
        conn = sqlite3.connect(db_uri, uri=True, timeout=5)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(canonical_db, timeout=5)

    try:
        if expected_generation is not None:
            db_gen = None
            try:
                cur = conn.execute(
                    "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    db_gen = str(row[0])
            except sqlite3.OperationalError:
                pass

            if (
                not db_gen
                or db_gen != expected_generation
                or not validate_generation_format(db_gen)
            ):
                print(
                    f"[FAIL_CLOSED] Database generation mismatch during fetch: expected {expected_generation!r} vs db {db_gen!r}\n"
                    "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
                )
                raise RuntimeError("Database generation mismatch during event fetch")

        cur = conn.execute(
            "SELECT rowid, dedup_key, topic, payload_json, ts FROM events "
            "WHERE rowid > ? AND processed = 0 ORDER BY rowid ASC LIMIT 100",
            (last_rowid,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def mark_processed(
    rowid: int,
    dedup_key: str | None = None,
    topic: str = "",
    issue_id: str | None = None,
    expected_generation: str | None = None,
    db_path: str | None = None,
) -> None:
    effective_db = DB_PATH if db_path is None else db_path
    canonical_db = get_canonical_path(effective_db)
    conn = sqlite3.connect(canonical_db, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if expected_generation is not None:
            db_gen = None
            try:
                cur = conn.execute(
                    "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    db_gen = str(row[0])
            except sqlite3.OperationalError:
                pass
            if (
                not db_gen
                or db_gen != expected_generation
                or not validate_generation_format(db_gen)
            ):
                conn.rollback()
                raise RuntimeError(
                    f"[FAIL_CLOSED] Database generation mismatch during mark_processed: expected {expected_generation!r} vs db {db_gen!r}\n"
                    "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
                )
        ensure_schema(conn)
        if dedup_key is None:
            row = conn.execute(
                "SELECT dedup_key, topic, payload_json FROM events WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if row:
                dedup_key = _stable_dedup_key(row[0], row[1], row[2])
                topic = topic or row[1]
        if dedup_key:
            conn.execute(
                """
                INSERT OR IGNORE INTO processed_event_keys
                (dedup_key, first_rowid, topic, issue_id, processed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (dedup_key, rowid, topic, issue_id, time.time()),
            )
        conn.execute("UPDATE events SET processed = 1 WHERE rowid = ?", (rowid,))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def claim_event_for_processing(
    rowid: int,
    dedup_key: str,
    topic: str,
    issue_id: str | None,
    expected_generation: str | None = None,
    db_path: str | None = None,
) -> bool:
    effective_db = DB_PATH if db_path is None else db_path
    canonical_db = get_canonical_path(effective_db)
    conn = sqlite3.connect(canonical_db, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if expected_generation is not None:
            db_gen = None
            try:
                cur = conn.execute(
                    "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    db_gen = str(row[0])
            except sqlite3.OperationalError:
                pass
            if (
                not db_gen
                or db_gen != expected_generation
                or not validate_generation_format(db_gen)
            ):
                conn.rollback()
                raise RuntimeError(
                    f"[FAIL_CLOSED] Database generation mismatch during claim: expected {expected_generation!r} vs db {db_gen!r}\n"
                    "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
                )
        ensure_schema(conn)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO processed_event_keys
            (dedup_key, first_rowid, topic, issue_id, processed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (dedup_key, rowid, topic, issue_id, time.time()),
        )
        conn.execute("UPDATE events SET processed = 1 WHERE rowid = ?", (rowid,))
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def fetch_issue(issue_id: str) -> dict | None:
    api_key = get_linear_api_key()
    if not api_key:
        return None
    query = (
        f'{{ issue(id: "{issue_id}") {{ identifier title priority '
        f"state {{name}} labels {{nodes {{name}}}} description }} }}"
    )
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        return d.get("data", {}).get("issue")
    except Exception as e:
        print(f"[consumer] fetch_issue error: {e}")
        return None


def has_dispatch_label(issue: dict) -> bool:
    labels = [
        label.get("name", "") for label in issue.get("labels", {}).get("nodes", [])
    ]
    return any(label_name.startswith("dispatch:") for label_name in labels)


def should_dispatch(issue: dict) -> bool:
    if not issue:
        return False
    state = issue.get("state", {}).get("name", "")
    if state in ("Done", "Cancelled", "Canceled", "Completed"):
        return False
    return has_dispatch_label(issue)


def dispatch_to_supervisor(issue_id: str) -> None:
    print(f"[consumer] {issue_id}: spawning supervisor")
    try:
        proc = subprocess.Popen(
            [
                "python3",
                SUPERVISOR_PATH,
                "--issue",
                issue_id,
                "--from-linear",
                "--lane-mode",
                "auto",
                "--active-project",
                "pwp",
                "--backlog-age-days",
                "30",
                "--jitter",
                "5-10",
                "--backoff",
                "3-8",
                "--max-concurrent",
                "3",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "AGY_CLI_HOME": AGY_CLI_HOME},
        )
        print(f"[consumer] {issue_id}: supervisor PID={proc.pid}")
    except Exception as e:
        print(f"[consumer] {issue_id}: spawn failed: {e}")


def process_event(
    rowid: int,
    dedup_key: str,
    topic: str,
    payload_json: str,
    ts: float,
    expected_generation: str | None = None,
    db_path: str | None = None,
) -> None:
    effective_db = DB_PATH if db_path is None else db_path
    event_key = _stable_dedup_key(dedup_key, topic, payload_json)

    try:
        event = json.loads(payload_json)
    except Exception as e:
        print(f"[consumer] bad payload rowid={rowid}: {e}")
        mark_processed(
            rowid,
            event_key,
            topic,
            expected_generation=expected_generation,
            db_path=effective_db,
        )
        return

    if topic != "update":
        mark_processed(
            rowid,
            event_key,
            topic,
            expected_generation=expected_generation,
            db_path=effective_db,
        )
        return
    if event.get("type") != "Issue":
        mark_processed(
            rowid,
            event_key,
            topic,
            expected_generation=expected_generation,
            db_path=effective_db,
        )
        return

    issue_id = event.get("data", {}).get("identifier")
    if not issue_id:
        mark_processed(
            rowid,
            event_key,
            topic,
            expected_generation=expected_generation,
            db_path=effective_db,
        )
        return

    if not claim_event_for_processing(
        rowid,
        event_key,
        topic,
        issue_id,
        expected_generation=expected_generation,
        db_path=effective_db,
    ):
        print(f"[consumer] {issue_id}: replay skipped (dedup_key={event_key})")
        return

    now = time.time()
    if now - _recent_dispatches[issue_id] < DEDUP_WINDOW_SEC:
        print(f"[consumer] {issue_id}: deduped (within {DEDUP_WINDOW_SEC}s window)")
        return

    # Revalidate expected generation and DB identity immediately before Linear access!
    if expected_generation is not None:
        verify_db_generation_and_identity(effective_db, expected_generation)

    issue = fetch_issue(issue_id)
    if not issue:
        print(f"[consumer] {issue_id}: fetch_issue returned None (auth or 404?)")
        return

    if not should_dispatch(issue):
        print(f"[consumer] {issue_id}: should_dispatch=False (state or labels)")
        return

    _recent_dispatches[issue_id] = now

    # Revalidate expected generation and DB identity immediately before supervisor spawn!
    if expected_generation is not None:
        verify_db_generation_and_identity(effective_db, expected_generation)

    dispatch_to_supervisor(issue_id)


def vacuum_processed(
    expected_generation: str | None = None, db_path: str | None = None
) -> None:
    effective_db = DB_PATH if db_path is None else db_path
    canonical_db = get_canonical_path(effective_db)
    conn = sqlite3.connect(canonical_db, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if expected_generation is not None:
            db_gen = None
            try:
                cur = conn.execute(
                    "SELECT value FROM dispatch_consumer_meta WHERE key = 'db_generation'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    db_gen = str(row[0])
            except sqlite3.OperationalError:
                pass
            if (
                not db_gen
                or db_gen != expected_generation
                or not validate_generation_format(db_gen)
            ):
                conn.rollback()
                raise RuntimeError(
                    f"[FAIL_CLOSED] Database generation mismatch during vacuum: expected {expected_generation!r} vs db {db_gen!r}\n"
                    "MARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
                )
        conn.execute(
            "DELETE FROM events WHERE processed = 1 AND ts < ?",
            (time.time() - 86400,),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def main_loop() -> None:
    print(f"[consumer] starting rowid-based; watching {DB_PATH}")

    last_vacuum = 0.0
    while True:
        try:
            is_ok, msg, state = verify_startup_gate(DB_PATH, STATE_FILE)
            if not is_ok or not state:
                print(msg)
                raise RuntimeError(f"Dispatch consumer failed closed: {msg}")

            last_rowid = state["last_rowid"]
            expected_gen = state["db_generation"]

            rows = fetch_new_events(
                last_rowid, expected_generation=expected_gen, db_path=DB_PATH
            )
            for row in rows:
                rid, dedup_key, topic, payload_json, ts = row
                process_event(
                    rid,
                    dedup_key,
                    topic,
                    payload_json,
                    ts,
                    expected_generation=expected_gen,
                    db_path=DB_PATH,
                )
                last_rowid = max(last_rowid, rid)
            if rows:
                set_state(last_rowid, expected_generation=expected_gen)
            if time.time() - last_vacuum > 300:
                vacuum_processed(expected_generation=expected_gen, db_path=DB_PATH)
                last_vacuum = time.time()
        except Exception as e:
            print(f"[consumer] loop error: {e}")
            if "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED" in str(
                e
            ) or isinstance(e, RuntimeError):
                raise
        time.sleep(POLL_INTERVAL)


def _sha256_file(filepath: str) -> str:
    if not os.path.exists(filepath):
        return "NONE"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def inspect_cursor(
    db_path: str | None = None, state_file_path: str | None = None
) -> dict:
    """Read-only inspection report. Byte-for-byte mutates nothing."""
    effective_db = DB_PATH if db_path is None else db_path
    effective_state = STATE_FILE if state_file_path is None else state_file_path
    canonical_db_path = get_canonical_path(effective_db)
    canonical_state_path = get_canonical_path(effective_state)

    db_exists = os.path.exists(canonical_db_path)
    max_rowid, db_gen = get_db_max_rowid_and_generation_readonly(effective_db)

    state, status_code, status_msg = read_cursor_state(effective_state)
    is_ok, gate_msg, _ = verify_startup_gate(effective_db, effective_state)

    cursor_format = "unknown"
    cursor_rowid = None
    cursor_db_gen = None
    cursor_db_path = None

    if status_code == "VALID" and state:
        cursor_format = "versioned_v1"
        cursor_rowid = state["last_rowid"]
        cursor_db_gen = state["db_generation"]
        cursor_db_path = state["db_path"]
    elif status_code == "LEGACY" and state:
        cursor_format = "legacy"
        cursor_rowid = state["last_rowid"]
    elif status_code == "MISSING":
        cursor_format = "missing"
    elif status_code == "INVALID":
        cursor_format = "invalid"

    proposed_bound_state = None
    if db_exists and db_gen is not None and max_rowid is not None:
        proposed_rowid = None
        if (
            status_code == "LEGACY"
            and cursor_rowid is not None
            and cursor_rowid <= max_rowid
        ):
            proposed_rowid = cursor_rowid
        elif (
            status_code == "VALID"
            and cursor_rowid is not None
            and cursor_rowid <= max_rowid
            and cursor_db_gen == db_gen
            and cursor_db_path == canonical_db_path
        ):
            proposed_rowid = cursor_rowid

        if proposed_rowid is not None:
            proposed_bound_state = {
                "schema_version": SCHEMA_VERSION,
                "last_rowid": proposed_rowid,
                "db_path": canonical_db_path,
                "db_generation": db_gen,
                "updated_at": "1970-01-01T00:00:00Z",
            }

    marker = (
        "PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK"
        if is_ok
        else "PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
    )

    return {
        "db_path": canonical_db_path,
        "db_exists": db_exists,
        "db_generation": db_gen,
        "max_rowid": max_rowid,
        "state_file_path": canonical_state_path,
        "state_file_exists": os.path.exists(canonical_state_path),
        "cursor_format": cursor_format,
        "cursor_rowid": cursor_rowid,
        "cursor_db_generation": cursor_db_gen,
        "cursor_db_path": cursor_db_path,
        "status_code": status_code,
        "status_msg": status_msg,
        "gate_ready": is_ok,
        "gate_msg": gate_msg,
        "proposed_bound_state": proposed_bound_state,
        "marker": marker,
    }


def validate_target_rowid(target_rowid: object, max_rowid: int) -> int:
    """Validate target_rowid to strictly require integer, 0 <= target_rowid <= max_rowid."""
    if type(target_rowid) is not int or isinstance(target_rowid, bool):
        raise TypeError(
            f"Invalid target_rowid type {type(target_rowid).__name__!r}: must be integer"
        )
    if target_rowid < 0 or target_rowid > max_rowid:
        raise ValueError(
            f"Invalid target_rowid {target_rowid}: must satisfy 0 <= target_rowid <= max_rowid={max_rowid}"
        )
    return target_rowid


def _cleanup_partial_destination(dst_path: str, target_dir: Path) -> None:
    """Safely remove partial destination file and fsync parent directory."""
    if os.path.exists(dst_path) or os.path.islink(dst_path):
        os.remove(dst_path)
        dir_fd = os.open(str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _copy_file_raw_atomic(
    src_path: str,
    dst_backup_path: str,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> tuple[str, int]:
    """Copy src_path raw byte-for-byte to dst_backup_path with exclusive creation,
    0o600 permissions, fsync on file and parent directory.

    Returns (dst_sha256, dst_size).
    """
    canonical_src = get_canonical_path(src_path)
    canonical_dst = get_canonical_path(dst_backup_path)

    if (
        os.path.islink(src_path)
        or os.path.islink(dst_backup_path)
        or os.path.islink(canonical_src)
        or os.path.islink(canonical_dst)
    ):
        raise ValueError(
            f"Refusing to copy: symlink detected ({src_path} -> {dst_backup_path})"
        )

    if os.path.exists(canonical_dst):
        raise FileExistsError(f"Backup destination collision: {dst_backup_path}")

    st_pre = os.lstat(canonical_src)
    if not stat.S_ISREG(st_pre.st_mode):
        raise ValueError(
            f"Refusing to copy: source path is not a regular file ({src_path})"
        )

    src_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    src_fd = os.open(canonical_src, src_flags)
    dst_fd = None
    dst_created = False
    try:
        st_src = os.fstat(src_fd)
        if not stat.S_ISREG(st_src.st_mode):
            raise ValueError(
                f"Refusing to copy: source descriptor is not a regular file ({src_path})"
            )
        if st_src.st_ino != st_pre.st_ino or st_src.st_dev != st_pre.st_dev:
            raise ValueError(
                f"Refusing to copy: source file modified/swapped between stat and open ({src_path})"
            )

        target_dir = Path(canonical_dst).parent
        target_dir.mkdir(parents=True, exist_ok=True)

        dst_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        dst_fd = os.open(canonical_dst, dst_flags, 0o600)
        dst_created = True

        try:
            while True:
                chunk = os.read(src_fd, 65536)
                if not chunk:
                    break
                offset = 0
                while offset < len(chunk):
                    written = os.write(dst_fd, chunk[offset:])
                    if written == 0:
                        raise OSError("os.write returned 0 bytes written")
                    offset += written

            os.fsync(dst_fd)
            os.close(dst_fd)
            dst_fd = None

            dir_fd = os.open(
                str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

            dst_sha = _sha256_file(canonical_dst)
            dst_sz = os.path.getsize(canonical_dst)

            if expected_sha256 is not None and dst_sha != expected_sha256:
                raise RuntimeError(
                    f"Backup copy verification failed: sha256 mismatch (expected {expected_sha256}, got {dst_sha})"
                )
            if expected_size is not None and dst_sz != expected_size:
                raise RuntimeError(
                    f"Backup copy verification failed: size mismatch (expected {expected_size}, got {dst_sz})"
                )

            return dst_sha, dst_sz

        except Exception as primary_exc:
            if dst_fd is not None:
                try:
                    os.close(dst_fd)
                except Exception:
                    pass
            cleanup_exc = None
            if dst_created:
                try:
                    _cleanup_partial_destination(canonical_dst, target_dir)
                except Exception as ce:
                    cleanup_exc = ce
            if cleanup_exc is not None:
                raise ExceptionGroup(
                    "Backup copy failed and partial artifact cleanup failed",
                    [primary_exc, cleanup_exc],
                )
            raise primary_exc

    finally:
        os.close(src_fd)


def _copy_file_raw_atomic_overwrite(src_path: str, dst_path: str) -> tuple[str, int]:
    """Copy src_path raw byte-for-byte to dst_path, overwriting atomic target."""
    canonical_src = get_canonical_path(src_path)
    canonical_dst = get_canonical_path(dst_path)

    target_dir = Path(canonical_dst).parent
    target_dir.mkdir(parents=True, exist_ok=True)

    with open(canonical_src, "rb") as f_in:
        raw_bytes = f_in.read()

    fd, temp_path = tempfile.mkstemp(
        dir=str(target_dir), prefix=".dispatch_cursor_restore_tmp_"
    )
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "wb") as f_out:
            f_out.write(raw_bytes)
            f_out.flush()
            os.fsync(f_out.fileno())

        os.replace(temp_path, canonical_dst)

        dir_fd = os.open(str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

        return _sha256_file(canonical_dst), os.path.getsize(canonical_dst)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def repair_dry_run(
    db_path: str | None = None,
    state_file_path: str | None = None,
    target_rowid: int | None = None,
) -> dict:
    """Compute deterministic repair plan and backup destinations. Byte-for-byte mutates nothing."""
    effective_db = DB_PATH if db_path is None else db_path
    effective_state = STATE_FILE if state_file_path is None else state_file_path

    _validate_db_path_strict(effective_db)
    _validate_state_path_strict(effective_state)

    inspection = inspect_cursor(effective_db, effective_state)
    canonical_db_path = inspection["db_path"]
    canonical_state_path = inspection["state_file_path"]

    if (
        not inspection["db_exists"]
        or inspection["db_generation"] is None
        or inspection["max_rowid"] is None
    ):
        raise RuntimeError(
            "Cannot dry-run repair: Database missing or generation unavailable"
        )

    db_gen = inspection["db_generation"]
    max_rowid = inspection["max_rowid"]

    if target_rowid is not None:
        proposed_rowid = validate_target_rowid(target_rowid, max_rowid)
    else:
        proposed_rowid = None
        status_code = inspection["status_code"]
        c_rowid = inspection["cursor_rowid"]
        c_gen = inspection["cursor_db_generation"]
        c_path = inspection["cursor_db_path"]

        if status_code == "LEGACY" and c_rowid is not None and c_rowid <= max_rowid:
            proposed_rowid = c_rowid
        elif (
            status_code == "VALID"
            and c_rowid is not None
            and c_rowid <= max_rowid
            and c_gen == db_gen
            and c_path == canonical_db_path
        ):
            proposed_rowid = c_rowid

        if proposed_rowid is None:
            raise ValueError(
                f"Explicit target_rowid required for repair when cursor status is {status_code!r} "
                f"or cursor is invalid/ahead/mismatched (0 <= target <= max_rowid={max_rowid})"
            )

    db_sha256 = _sha256_file(canonical_db_path)
    db_size = (
        os.path.getsize(canonical_db_path) if os.path.exists(canonical_db_path) else 0
    )

    wal_path = canonical_db_path + "-wal"
    wal_exists = os.path.exists(wal_path) and os.path.getsize(wal_path) > 0
    wal_sha256 = _sha256_file(wal_path) if wal_exists else "NONE"
    wal_size = os.path.getsize(wal_path) if wal_exists else 0

    cursor_exists, cursor_bytes = _snapshot_cursor_file(canonical_state_path)
    cursor_sha256 = (
        hashlib.sha256(cursor_bytes).hexdigest() if cursor_bytes is not None else "NONE"
    )
    cursor_size = len(cursor_bytes) if cursor_bytes is not None else 0

    plan_payload = (
        f"{canonical_db_path}:{db_sha256}:{db_size}:"
        f"{wal_path}:{wal_sha256}:{wal_size}:"
        f"{canonical_state_path}:{cursor_sha256}:{cursor_size}:"
        f"{db_gen}:{proposed_rowid}"
    )
    plan_id = hashlib.sha256(plan_payload.encode("utf-8")).hexdigest()[:16]

    proposed_db_backup = f"{canonical_db_path}.backup.plan_{plan_id}"
    proposed_wal_backup = (
        f"{canonical_db_path}-wal.backup.plan_{plan_id}" if wal_exists else "NONE"
    )
    proposed_cursor_backup = (
        f"{canonical_state_path}.backup.plan_{plan_id}" if cursor_exists else "NONE"
    )

    deterministic_updated_at = "1970-01-01T00:00:00Z"
    proposed_state = {
        "schema_version": SCHEMA_VERSION,
        "last_rowid": proposed_rowid,
        "db_path": canonical_db_path,
        "db_generation": db_gen,
        "updated_at": deterministic_updated_at,
    }
    validate_cursor_state_dict(proposed_state)

    members = [
        {
            "name": "main_db",
            "src_path": canonical_db_path,
            "src_sha256": db_sha256,
            "src_size": db_size,
            "proposed_backup_path": proposed_db_backup,
        }
    ]
    if wal_exists:
        members.append(
            {
                "name": "wal",
                "src_path": wal_path,
                "src_sha256": wal_sha256,
                "src_size": wal_size,
                "proposed_backup_path": proposed_wal_backup,
            }
        )
    if cursor_exists:
        members.append(
            {
                "name": "cursor",
                "src_path": canonical_state_path,
                "src_sha256": cursor_sha256,
                "src_size": cursor_size,
                "proposed_backup_path": proposed_cursor_backup,
            }
        )

    return {
        "plan": "repair_dry_run",
        "plan_id": plan_id,
        "db_path": canonical_db_path,
        "db_generation": db_gen,
        "max_rowid": max_rowid,
        "db_sha256": db_sha256,
        "db_size": db_size,
        "wal_path": wal_path if wal_exists else "NONE",
        "wal_sha256": wal_sha256,
        "wal_size": wal_size,
        "cursor_path": canonical_state_path if cursor_exists else "NONE",
        "cursor_sha256": cursor_sha256,
        "cursor_size": cursor_size,
        "proposed_db_backup_path": proposed_db_backup,
        "proposed_wal_backup_path": proposed_wal_backup,
        "proposed_cursor_backup_path": proposed_cursor_backup,
        "members": members,
        "proposed_cursor_state": proposed_state,
        "confirmation_required": CONFIRMATION_TOKEN,
        "mutated": False,
    }


def repair_apply(
    db_path: str | None = None,
    state_file_path: str | None = None,
    confirmation_token: str = "",
    target_rowid: int | None = None,
    plan: dict | None = None,
) -> dict:
    """Apply cursor migration/repair with atomic raw backups and confirmation token."""
    if confirmation_token != CONFIRMATION_TOKEN:
        return {
            "status": "REFUSED",
            "reason": f"Exact confirmation token {CONFIRMATION_TOKEN!r} required",
            "mutated": False,
        }

    effective_db = DB_PATH if db_path is None else db_path
    effective_state = STATE_FILE if state_file_path is None else state_file_path

    _validate_db_path_strict(effective_db)
    _validate_state_path_strict(effective_state)

    canonical_db_path = get_canonical_path(effective_db)
    canonical_state_path = get_canonical_path(effective_state)

    created_artifacts: list[str] = []
    verified_backups: list[dict] = []
    cursor_write_started = False
    written_bytes: bytes | None = None

    lock = CursorLock(canonical_state_path)
    lock.acquire()

    try:
        prior_existed, prior_bytes = _snapshot_cursor_file(canonical_state_path)

        expected_plan = repair_dry_run(
            effective_db, effective_state, target_rowid=target_rowid
        )

        if plan is not None:
            if not isinstance(plan, dict):
                raise TypeError("Supplied plan must be a dictionary")
            for source_key in (
                "db_sha256",
                "db_size",
                "wal_sha256",
                "wal_size",
                "cursor_sha256",
                "cursor_size",
            ):
                if plan.get(source_key) != expected_plan.get(source_key):
                    raise RuntimeError(
                        "Source drift detected: database or cursor file changed since plan computation"
                    )
            if plan != expected_plan:
                raise ValueError(
                    "Caller-supplied plan does not match recomputed deterministic plan"
                )

        dry_run_plan = expected_plan

        db_backup_path = dry_run_plan["proposed_db_backup_path"]
        wal_backup_path = dry_run_plan["proposed_wal_backup_path"]
        cursor_backup_path = dry_run_plan["proposed_cursor_backup_path"]

        if os.path.exists(db_backup_path):
            raise FileExistsError(f"DB backup destination collision: {db_backup_path}")
        if wal_backup_path != "NONE" and os.path.exists(wal_backup_path):
            raise FileExistsError(
                f"WAL backup destination collision: {wal_backup_path}"
            )
        if cursor_backup_path != "NONE" and os.path.exists(cursor_backup_path):
            raise FileExistsError(
                f"Cursor backup destination collision: {cursor_backup_path}"
            )

        lock_conn = sqlite3.connect(canonical_db_path, timeout=10.0)
        try:
            lock_conn.execute("BEGIN EXCLUSIVE")

            curr_db_sha256 = _sha256_file(canonical_db_path)
            curr_db_size = (
                os.path.getsize(canonical_db_path)
                if os.path.exists(canonical_db_path)
                else 0
            )

            wal_path = canonical_db_path + "-wal"
            wal_exists = os.path.exists(wal_path) and os.path.getsize(wal_path) > 0
            curr_wal_sha256 = _sha256_file(wal_path) if wal_exists else "NONE"
            curr_wal_size = os.path.getsize(wal_path) if wal_exists else 0

            cursor_exists = prior_existed
            curr_cursor_sha256 = (
                hashlib.sha256(prior_bytes).hexdigest()
                if prior_bytes is not None
                else "NONE"
            )
            curr_cursor_size = len(prior_bytes) if prior_bytes is not None else 0

            if (
                curr_db_sha256 != dry_run_plan["db_sha256"]
                or curr_db_size != dry_run_plan["db_size"]
                or curr_wal_sha256 != dry_run_plan["wal_sha256"]
                or curr_wal_size != dry_run_plan["wal_size"]
                or curr_cursor_sha256 != dry_run_plan["cursor_sha256"]
                or curr_cursor_size != dry_run_plan["cursor_size"]
            ):
                raise RuntimeError(
                    "Source drift detected: database or cursor file changed since plan computation"
                )

            # Raw byte copy of main DB
            db_bak_sha, db_bak_sz = _copy_file_raw_atomic(
                canonical_db_path, db_backup_path
            )
            created_artifacts.append(db_backup_path)
            verified_backups.append(
                {"path": db_backup_path, "sha256": db_bak_sha, "size": db_bak_sz}
            )

            if db_bak_sha != curr_db_sha256 or db_bak_sz != curr_db_size:
                raise RuntimeError(
                    f"Main DB backup copy verification failed: expected sha={curr_db_sha256} size={curr_db_size}, got sha={db_bak_sha} size={db_bak_sz}"
                )

            # Raw byte copy of WAL if present and non-empty
            wal_bak_sha, wal_bak_sz = "NONE", 0
            if wal_exists:
                wal_bak_sha, wal_bak_sz = _copy_file_raw_atomic(
                    wal_path, wal_backup_path
                )
                created_artifacts.append(wal_backup_path)
                verified_backups.append(
                    {"path": wal_backup_path, "sha256": wal_bak_sha, "size": wal_bak_sz}
                )
                if wal_bak_sha != curr_wal_sha256 or wal_bak_sz != curr_wal_size:
                    raise RuntimeError(
                        f"WAL backup copy verification failed: expected sha={curr_wal_sha256} size={curr_wal_size}, got sha={wal_bak_sha} size={wal_bak_sz}"
                    )

            # Raw byte copy of cursor state file if present
            cursor_bak_sha, cursor_bak_sz = "NONE", 0
            if cursor_exists:
                cursor_bak_sha, cursor_bak_sz = _copy_file_raw_atomic(
                    canonical_state_path, cursor_backup_path
                )
                created_artifacts.append(cursor_backup_path)
                verified_backups.append(
                    {
                        "path": cursor_backup_path,
                        "sha256": cursor_bak_sha,
                        "size": cursor_bak_sz,
                    }
                )
                if (
                    cursor_bak_sha != curr_cursor_sha256
                    or cursor_bak_sz != curr_cursor_size
                ):
                    raise RuntimeError(
                        f"Cursor backup copy verification failed: expected sha={curr_cursor_sha256} size={curr_cursor_size}, got sha={cursor_bak_sha} size={cursor_bak_sz}"
                    )

            now_str = get_canonical_utc_now()
            new_state = dict(dry_run_plan["proposed_cursor_state"])
            new_state["updated_at"] = now_str

            member_receipts = [
                {
                    "name": "main_db",
                    "src_path": canonical_db_path,
                    "src_sha256": curr_db_sha256,
                    "src_size": curr_db_size,
                    "backup_path": db_backup_path,
                    "backup_sha256": db_bak_sha,
                    "backup_size": db_bak_sz,
                    "bytes_match": curr_db_sha256 == db_bak_sha
                    and curr_db_size == db_bak_sz,
                }
            ]
            if wal_exists:
                member_receipts.append(
                    {
                        "name": "wal",
                        "src_path": wal_path,
                        "src_sha256": curr_wal_sha256,
                        "src_size": curr_wal_size,
                        "backup_path": wal_backup_path,
                        "backup_sha256": wal_bak_sha,
                        "backup_size": wal_bak_sz,
                        "bytes_match": curr_wal_sha256 == wal_bak_sha
                        and curr_wal_size == wal_bak_sz,
                    }
                )
            if cursor_exists:
                member_receipts.append(
                    {
                        "name": "cursor",
                        "src_path": canonical_state_path,
                        "src_sha256": curr_cursor_sha256,
                        "src_size": curr_cursor_size,
                        "backup_path": cursor_backup_path,
                        "backup_sha256": cursor_bak_sha,
                        "backup_size": cursor_bak_sz,
                        "bytes_match": curr_cursor_sha256 == cursor_bak_sha
                        and curr_cursor_size == cursor_bak_sz,
                    }
                )

            # ATOMIC CURSOR REPLACEMENT STARTS HERE
            target_dir = Path(canonical_state_path).parent
            target_dir.mkdir(parents=True, exist_ok=True)
            content = json.dumps(new_state, indent=2) + "\n"
            written_bytes = content.encode("utf-8")

            fd, temp_path = tempfile.mkstemp(
                dir=str(target_dir), prefix=".dispatch_cursor_tmp_"
            )
            temp_cleanup_err = None
            try:
                os.chmod(temp_path, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())

                cursor_write_started = True
                os.replace(temp_path, canonical_state_path)

                dir_fd = os.open(
                    str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)

                new_cursor_sha256 = _sha256_file(canonical_state_path)
            finally:
                if os.path.exists(temp_path) or os.path.islink(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as ce:
                        temp_cleanup_err = ce

            if temp_cleanup_err is not None:
                raise temp_cleanup_err

            receipt = {
                "status": "SUCCESS",
                "action": "repair_apply",
                "plan_id": dry_run_plan["plan_id"],
                "timestamp": now_str,
                "db_path": canonical_db_path,
                "db_generation": dry_run_plan["db_generation"],
                "src_db_sha256": curr_db_sha256,
                "src_db_size": curr_db_size,
                "db_backup_path": db_backup_path,
                "db_backup_sha256": db_bak_sha,
                "db_backup_size": db_bak_sz,
                "wal_path": wal_path if wal_exists else "NONE",
                "src_wal_sha256": curr_wal_sha256,
                "src_wal_size": curr_wal_size,
                "wal_backup_path": wal_backup_path if wal_exists else "NONE",
                "wal_backup_sha256": wal_bak_sha,
                "wal_backup_size": wal_bak_sz,
                "cursor_path": canonical_state_path if cursor_exists else "NONE",
                "src_cursor_sha256": curr_cursor_sha256,
                "src_cursor_size": curr_cursor_size,
                "cursor_backup_path": cursor_backup_path if cursor_exists else "NONE",
                "cursor_backup_sha256": cursor_bak_sha,
                "cursor_backup_size": cursor_bak_sz,
                "members": member_receipts,
                "new_cursor_state": new_state,
                "new_cursor_sha256": new_cursor_sha256,
                "marker": "PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK",
                "mutated": True,
            }

        finally:
            lock_conn.rollback()
            lock_conn.close()

        lock.release()

        return receipt

    except Exception as primary_exc:
        lock_release_err = None
        if lock._fd is not None:
            try:
                lock.release()
            except Exception as lre:
                lock_release_err = lre

        if not cursor_write_started:
            cleanup_errors = []
            for path in reversed(created_artifacts):
                try:
                    _cleanup_partial_destination(path, Path(path).parent)
                except Exception as ce:
                    cleanup_errors.append(ce)
            if lock_release_err is not None:
                cleanup_errors.append(lock_release_err)
            if cleanup_errors:
                raise ExceptionGroup(
                    "repair_apply failed pre-mutation and artifact cleanup encountered errors",
                    [primary_exc] + cleanup_errors,
                )
            raise primary_exc
        else:
            restored = _safe_rollback_cursor(
                canonical_state_path, prior_existed, prior_bytes, written_bytes
            )

            if restored:
                msg = (
                    f"Post-cursor-write failure occurred during repair_apply ({primary_exc}). "
                    f"Durable rollback succeeded: exact original cursor state restored. "
                    f"Backups preserved at {created_artifacts}."
                )
                if lock_release_err is not None and lock_release_err is not primary_exc:
                    raise ExceptionGroup(
                        msg,
                        [primary_exc, lock_release_err],
                    ) from primary_exc
                raise RuntimeError(msg) from primary_exc
            else:
                backup_records = []
                for vb in verified_backups:
                    p = vb["path"]
                    h = "NONE"
                    try:
                        h = _sha256_file(p)
                    except Exception:
                        pass
                    if h == "NONE":
                        h = vb.get("sha256", "NONE")
                    backup_records.append({"path": p, "sha256": h})

                recovery_info = {
                    "status": "RECOVERY_REQUIRED",
                    "error": str(primary_exc),
                    "created_backups": backup_records,
                    "cursor_state_mutated": True,
                    "marker": "PRISMATIC_DISPATCH_CURSOR_GENERATION_RECOVERY_REQUIRED",
                }
                msg = (
                    f"Post-cursor-write failure occurred during repair_apply ({primary_exc}) "
                    f"and durable rollback could not be proven exact. "
                    f"RECOVERY REQUIRED. Retained backups: {json.dumps(recovery_info)}"
                )
                if lock_release_err is not None and lock_release_err is not primary_exc:
                    raise ExceptionGroup(
                        msg,
                        [primary_exc, lock_release_err],
                    ) from primary_exc
                raise RuntimeError(msg) from primary_exc


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Dispatch consumer v3 with cursor generation safety."
    )
    parser.add_argument("--inspect", action="store_true", help="Run inspect workflow")
    parser.add_argument(
        "--repair-dry-run", action="store_true", help="Run repair dry-run workflow"
    )
    parser.add_argument(
        "--repair-apply", action="store_true", help="Run repair apply workflow"
    )
    parser.add_argument(
        "--confirm", type=str, default="", help="Confirmation token for repair apply"
    )
    parser.add_argument(
        "--target-rowid",
        type=int,
        default=None,
        help="Explicit target rowid for repair",
    )
    parser.add_argument(
        "--db-path", type=str, default=None, help="Path to SQLite event bus database"
    )
    parser.add_argument(
        "--state-file", type=str, default=None, help="Path to cursor state file"
    )

    args = parser.parse_args()

    if args.inspect:
        res = inspect_cursor(args.db_path, args.state_file)
        print(json.dumps(res, indent=2))
    elif args.repair_dry_run:
        res = repair_dry_run(
            args.db_path, args.state_file, target_rowid=args.target_rowid
        )
        print(json.dumps(res, indent=2))
    elif args.repair_apply:
        res = repair_apply(
            args.db_path,
            args.state_file,
            confirmation_token=args.confirm,
            target_rowid=args.target_rowid,
        )
        print(json.dumps(res, indent=2))
    else:
        main_loop()


if __name__ == "__main__":
    main()
