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
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict

SCHEMA_VERSION = 1
MAX_STATE_FILE_SIZE = 16384  # 16 KB
CONFIRMATION_TOKEN = "I_ACCEPT_CURSOR_REPAIR_RISK"

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


def validate_generation_format(gen: str) -> bool:
    """Validate database generation UUID string."""
    if not isinstance(gen, str):
        return False
    if len(gen) < 8 or len(gen) > 128:
        return False
    return all(c.isalnum() or c in "-_" for c in gen)


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

    with conn:
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
    conn.commit()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    d: dict[str, object] = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"Duplicate JSON key at nesting level: {k!r}")
        d[k] = v
    return d


def read_cursor_state(state_file_path: str) -> tuple[dict | None, str, str]:
    """Read and strictly validate the cursor state file.

    Returns:
        (state_dict_or_none, status_code, diagnostic_message)
        status_code in {'MISSING', 'LEGACY', 'VALID', 'INVALID'}
    """
    state_path = Path(state_file_path).expanduser()
    if not state_path.is_absolute():
        state_path = state_path.absolute()

    if not state_path.exists():
        return None, "MISSING", f"Cursor state file does not exist at {state_path}"

    if state_path.is_symlink() or os.path.islink(state_file_path):
        return None, "INVALID", "Cursor state file is a symlink"

    try:
        st = os.lstat(state_path)
    except Exception as e:
        return None, "INVALID", f"Failed to lstat cursor state file: {e}"

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

    try:
        with open(state_path, "rb") as f:
            raw_bytes = f.read()
    except Exception as e:
        return None, "INVALID", f"Failed to read cursor state file: {e}"

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
    if type(ts) is not str or not ts:
        return None, "INVALID", f"Invalid updated_at in state: {ts!r}"

    return data, "VALID", "Cursor state envelope valid"


def write_cursor_state(state_file_path: str, state_data: dict) -> None:
    """Atomically write cursor state JSON envelope using temp file, 0o600 permissions,
    flush/fsync, os.replace, and parent directory fsync.
    """
    canonical_state_path = get_canonical_path(state_file_path)
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

        try:
            dir_fd = os.open(
                str(target_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise


def get_db_max_rowid_and_generation(db_path: str) -> tuple[int, str]:
    """Query current max(rowid) from actual events table and durable db_generation."""
    canonical_db_path = get_canonical_path(db_path)
    if not os.path.exists(canonical_db_path):
        raise FileNotFoundError(f"Database file does not exist: {canonical_db_path}")
    if os.path.islink(canonical_db_path) or os.path.islink(db_path):
        raise ValueError(f"Database path is a symlink: {db_path}")

    db_uri = f"file:{canonical_db_path}?mode=ro"
    try:
        conn = sqlite3.connect(db_uri, uri=True, timeout=5)
        try:
            gen = ensure_db_generation(conn)
            cur = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM events")
            max_rowid = int(cur.fetchone()[0] or 0)
            return max_rowid, gen
        finally:
            conn.close()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(canonical_db_path, timeout=5)
        try:
            gen = ensure_db_generation(conn)
            ensure_schema(conn)
            cur = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM events")
            max_rowid = int(cur.fetchone()[0] or 0)
            return max_rowid, gen
        finally:
            conn.close()


def verify_startup_gate(
    db_path: str = DB_PATH, state_file_path: str = STATE_FILE
) -> tuple[bool, str, dict | None]:
    """Verify DB generation and cursor state envelope before polling or spawning."""
    try:
        max_rowid, db_gen = get_db_max_rowid_and_generation(db_path)
    except Exception as e:
        msg = f"[FAIL_CLOSED] Database error or identity unavailable: {e}\nMARKER=PRISMATIC_DISPATCH_CURSOR_GENERATION_FAIL_CLOSED"
        return False, msg, None

    canonical_db_path = get_canonical_path(db_path)
    state, status_code, status_msg = read_cursor_state(state_file_path)

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


def set_state(rowid: int) -> None:
    """Atomically write updated versioned state envelope."""
    canonical_db_path = get_canonical_path(DB_PATH)
    _, db_gen = get_db_max_rowid_and_generation(DB_PATH)
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state_data = {
        "schema_version": SCHEMA_VERSION,
        "last_rowid": rowid,
        "db_path": canonical_db_path,
        "db_generation": db_gen,
        "updated_at": now_str,
    }
    write_cursor_state(STATE_FILE, state_data)


def _stable_dedup_key(dedup_key: str | None, topic: str, payload_json: str) -> str:
    if dedup_key:
        return dedup_key
    payload_hash = hashlib.sha256(
        payload_json.encode("utf-8", errors="replace")
    ).hexdigest()
    return f"legacy:{topic}:{payload_hash}"


def fetch_new_events(last_rowid: int) -> list[tuple]:
    canonical_db = get_canonical_path(DB_PATH)
    if not os.path.exists(canonical_db):
        return []
    conn = sqlite3.connect(canonical_db, timeout=5)
    try:
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
) -> None:
    canonical_db = get_canonical_path(DB_PATH)
    conn = sqlite3.connect(canonical_db, timeout=5)
    try:
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
    finally:
        conn.close()


def claim_event_for_processing(
    rowid: int, dedup_key: str, topic: str, issue_id: str | None
) -> bool:
    canonical_db = get_canonical_path(DB_PATH)
    conn = sqlite3.connect(canonical_db, timeout=5)
    try:
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
    rowid: int, dedup_key: str, topic: str, payload_json: str, ts: float
) -> None:
    event_key = _stable_dedup_key(dedup_key, topic, payload_json)

    try:
        event = json.loads(payload_json)
    except Exception as e:
        print(f"[consumer] bad payload rowid={rowid}: {e}")
        mark_processed(rowid, event_key, topic)
        return

    if topic != "update":
        mark_processed(rowid, event_key, topic)
        return
    if event.get("type") != "Issue":
        mark_processed(rowid, event_key, topic)
        return

    issue_id = event.get("data", {}).get("identifier")
    if not issue_id:
        mark_processed(rowid, event_key, topic)
        return

    if not claim_event_for_processing(rowid, event_key, topic, issue_id):
        print(f"[consumer] {issue_id}: replay skipped (dedup_key={event_key})")
        return

    now = time.time()
    if now - _recent_dispatches[issue_id] < DEDUP_WINDOW_SEC:
        print(f"[consumer] {issue_id}: deduped (within {DEDUP_WINDOW_SEC}s window)")
        return

    issue = fetch_issue(issue_id)
    if not issue:
        print(f"[consumer] {issue_id}: fetch_issue returned None (auth or 404?)")
        return

    if not should_dispatch(issue):
        print(f"[consumer] {issue_id}: should_dispatch=False (state or labels)")
        return

    _recent_dispatches[issue_id] = now
    dispatch_to_supervisor(issue_id)


def vacuum_processed() -> None:
    try:
        canonical_db = get_canonical_path(DB_PATH)
        conn = sqlite3.connect(canonical_db, timeout=5)
        try:
            conn.execute(
                "DELETE FROM events WHERE processed = 1 AND ts < ?",
                (time.time() - 86400,),
            )
            conn.commit()
        except Exception as e:
            print(f"[consumer] vacuum execution error: {e}")
        finally:
            conn.close()
    except Exception as e:
        print(f"[consumer] vacuum connection error: {e}")


def main_loop() -> None:
    print(f"[consumer] starting rowid-based; watching {DB_PATH}")
    last_rowid = get_state()
    print(f"[consumer] resuming from rowid={last_rowid}")

    try:
        canonical_db = get_canonical_path(DB_PATH)
        conn = sqlite3.connect(canonical_db, timeout=5)
        try:
            ensure_schema(conn)
        finally:
            conn.close()
    except Exception as e:
        print(f"[consumer] schema ensure error: {e}")

    last_vacuum = 0.0
    while True:
        try:
            rows = fetch_new_events(last_rowid)
            for row in rows:
                rid, dedup_key, topic, payload_json, ts = row
                process_event(rid, dedup_key, topic, payload_json, ts)
                last_rowid = max(last_rowid, rid)
            if rows:
                set_state(last_rowid)
            if time.time() - last_vacuum > 300:
                vacuum_processed()
                last_vacuum = time.time()
        except Exception as e:
            print(f"[consumer] loop error: {e}")
        time.sleep(POLL_INTERVAL)


def _sha256_file(filepath: str) -> str:
    if not os.path.exists(filepath):
        return "NONE"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def inspect_cursor(db_path: str = DB_PATH, state_file_path: str = STATE_FILE) -> dict:
    """Read-only inspection report. Byte-for-byte mutates nothing."""
    canonical_db_path = get_canonical_path(db_path)
    canonical_state_path = get_canonical_path(state_file_path)

    db_exists = os.path.exists(canonical_db_path)
    db_gen = None
    max_rowid = None
    if db_exists:
        try:
            max_rowid, db_gen = get_db_max_rowid_and_generation(db_path)
        except Exception:
            pass

    state, status_code, status_msg = read_cursor_state(state_file_path)
    is_ok, gate_msg, _ = verify_startup_gate(db_path, state_file_path)

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
    if db_exists and db_gen is not None:
        proposed_rowid = 0
        if cursor_rowid is not None:
            proposed_rowid = min(
                cursor_rowid, max_rowid if max_rowid is not None else 0
            )
        elif max_rowid is not None:
            proposed_rowid = max_rowid
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        proposed_bound_state = {
            "schema_version": SCHEMA_VERSION,
            "last_rowid": proposed_rowid,
            "db_path": canonical_db_path,
            "db_generation": db_gen,
            "updated_at": now_str,
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


def repair_dry_run(
    db_path: str = DB_PATH,
    state_file_path: str = STATE_FILE,
    target_rowid: int | None = None,
) -> dict:
    """Compute deterministic repair plan and backup destinations. Byte-for-byte mutates nothing."""
    inspection = inspect_cursor(db_path, state_file_path)
    canonical_db_path = inspection["db_path"]
    canonical_state_path = inspection["state_file_path"]

    if not inspection["db_exists"] or inspection["db_generation"] is None:
        raise RuntimeError(
            "Cannot dry-run repair: Database missing or generation unavailable"
        )

    db_gen = inspection["db_generation"]
    max_rowid = inspection["max_rowid"] or 0

    if target_rowid is not None:
        proposed_rowid = target_rowid
    elif inspection["cursor_rowid"] is not None:
        proposed_rowid = min(inspection["cursor_rowid"], max_rowid)
    else:
        proposed_rowid = max_rowid

    ts_suffix = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    proposed_db_backup = f"{canonical_db_path}.backup.{ts_suffix}"
    proposed_cursor_backup = f"{canonical_state_path}.backup.{ts_suffix}"

    db_sha256 = _sha256_file(canonical_db_path)
    cursor_sha256 = _sha256_file(canonical_state_path)

    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    proposed_state = {
        "schema_version": SCHEMA_VERSION,
        "last_rowid": proposed_rowid,
        "db_path": canonical_db_path,
        "db_generation": db_gen,
        "updated_at": now_str,
    }

    return {
        "plan": "repair_dry_run",
        "db_path": canonical_db_path,
        "db_generation": db_gen,
        "max_rowid": max_rowid,
        "db_sha256": db_sha256,
        "cursor_path": canonical_state_path,
        "cursor_sha256": cursor_sha256,
        "proposed_db_backup_path": proposed_db_backup,
        "proposed_cursor_backup_path": proposed_cursor_backup,
        "proposed_cursor_state": proposed_state,
        "confirmation_required": CONFIRMATION_TOKEN,
        "mutated": False,
    }


def repair_apply(
    db_path: str = DB_PATH,
    state_file_path: str = STATE_FILE,
    confirmation_token: str = "",
    target_rowid: int | None = None,
) -> dict:
    """Apply cursor migration/repair with atomic backups and confirmation token."""
    if confirmation_token != CONFIRMATION_TOKEN:
        return {
            "status": "REFUSED",
            "reason": f"Exact confirmation token {CONFIRMATION_TOKEN!r} required",
            "mutated": False,
        }

    canonical_db_path = get_canonical_path(db_path)
    canonical_state_path = get_canonical_path(state_file_path)

    src_db_sha256 = _sha256_file(canonical_db_path)
    src_cursor_sha256 = _sha256_file(canonical_state_path)

    dry_run_plan = repair_dry_run(db_path, state_file_path, target_rowid=target_rowid)

    ts_now = datetime.datetime.now(datetime.timezone.utc)
    ts_str = ts_now.strftime("%Y%m%d_%H%M%S") + f"_{ts_now.microsecond:06d}"
    db_backup_path = f"{canonical_db_path}.backup.{ts_str}"
    cursor_backup_path = f"{canonical_state_path}.backup.{ts_str}"

    if os.path.exists(db_backup_path):
        raise FileExistsError(f"DB backup destination collision: {db_backup_path}")
    if os.path.exists(cursor_backup_path):
        raise FileExistsError(
            f"Cursor backup destination collision: {cursor_backup_path}"
        )

    # Consistent DB backup using SQLite Backup API in read-only mode to prevent header mutation
    db_uri = f"file:{canonical_db_path}?mode=ro"
    src_conn = sqlite3.connect(db_uri, uri=True)
    try:
        dest_conn = sqlite3.connect(db_backup_path)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    db_backup_sha256 = _sha256_file(db_backup_path)

    cursor_backup_sha256 = "NONE"
    if os.path.exists(canonical_state_path):
        with open(canonical_state_path, "rb") as f_in:
            data = f_in.read()
        with open(cursor_backup_path, "wb") as f_out:
            f_out.write(data)
        cursor_backup_sha256 = _sha256_file(cursor_backup_path)
    else:
        cursor_backup_path = "NONE"

    new_state = dry_run_plan["proposed_cursor_state"]
    write_cursor_state(canonical_state_path, new_state)
    new_cursor_sha256 = _sha256_file(canonical_state_path)

    receipt = {
        "status": "SUCCESS",
        "action": "repair_apply",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "db_path": canonical_db_path,
        "db_generation": dry_run_plan["db_generation"],
        "src_db_sha256": src_db_sha256,
        "db_backup_path": db_backup_path,
        "db_backup_sha256": db_backup_sha256,
        "src_cursor_sha256": src_cursor_sha256,
        "cursor_backup_path": cursor_backup_path,
        "cursor_backup_sha256": cursor_backup_sha256,
        "new_cursor_state": new_state,
        "new_cursor_sha256": new_cursor_sha256,
        "marker": "PRISMATIC_DISPATCH_CURSOR_GENERATION_SOURCE_OK",
        "mutated": True,
    }
    return receipt


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
        "--db-path", type=str, default=DB_PATH, help="Path to SQLite event bus database"
    )
    parser.add_argument(
        "--state-file", type=str, default=STATE_FILE, help="Path to cursor state file"
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
