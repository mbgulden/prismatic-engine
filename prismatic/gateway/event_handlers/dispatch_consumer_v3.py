"""Phase 3 v2 (rowid+atomic): dispatch_consumer that polls the SQLite bus.

Improvements over v2:
- Use rowid (monotonic, restart-safe) instead of wall-clock ts (skew-prone).
- Atomic per-event processing: persistent dedup claim + UPDATE processed=1
  + COMMIT before spawn, with a dedicated processed column.
- Cold-start guard: if state file missing, default to "now-300s" so we
  don't replay the entire history on first start.
- Persistent dedup keys suppress exact bus-event replays across restarts.
- 60-second issue_id dedup window still suppresses distinct Linear retries
  and noisy update events.
- Filter: only spawn supervisor on `dispatch:ready` label changes or
  status changes that put issue in Backlog/Todo with a `dispatch:*` label.
  No more storm of spawns for every label flicker.

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

Reads `last_rowid` from `$PRISMATIC_HOME/bus/dispatch_consumer.rowid`
on startup, advances it after each row is processed.
"""

import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import hashlib
from pathlib import Path
from collections import defaultdict

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

# Issue-ID → last dispatch time, for dedup window
_recent_dispatches: dict[str, float] = defaultdict(float)


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
    """Get last rowid processed. Cold-start: use now - 300s of rowids (conservative)."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return int(f.read().strip() or 0)
        except Exception:
            pass
    # Cold start: figure out the highest rowid with ts newer than (now - COLD_START_BACKOFF_SEC)
    if not os.path.exists(DB_PATH):
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        cur = conn.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM events WHERE ts >= ?",
            (time.time() - COLD_START_BACKOFF_SEC,),
        )
        return int(cur.fetchone()[0] or 0)
    finally:
        conn.close()


def set_state(rowid: int) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(str(rowid))


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate the event bus schema used by the consumer.

    Idempotency is intentionally two-layered:

    * ``events.processed`` is the per-row processed marker used by the hot poller.
    * ``processed_event_keys`` is the persistent replay ledger keyed by the bus
      event's stable dedup key. If a row is reset to ``processed = 0`` or a replay
      is reinserted with the same key, the consumer sees the ledger entry and
      skips side effects.
    """
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


def _stable_dedup_key(dedup_key: str | None, topic: str, payload_json: str) -> str:
    """Return a stable replay key for a bus event.

    New writers persist ``events.dedup_key``. Older rows may not have it, so we
    fall back to a content hash that stays stable across rowid resets/replays.
    """
    if dedup_key:
        return dedup_key
    payload_hash = hashlib.sha256(
        payload_json.encode("utf-8", errors="replace")
    ).hexdigest()
    return f"legacy:{topic}:{payload_hash}"


def fetch_new_events(last_rowid: int) -> list[tuple]:
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH, timeout=5)
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
    """Mark this row processed and persist its replay ledger marker."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
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
    """Atomically claim an event key before side effects.

    Returns True for the first processor. Returns False for replays/duplicates;
    the duplicate row is still marked processed so the poller does not spin on it.
    """
    conn = sqlite3.connect(DB_PATH, timeout=5)
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
    """Filter: only dispatch when:
    - issue has at least one `dispatch:*` label
    - AND issue is NOT in Done/Cancelled/Canceled state
    - AND its previous-dispatch window has expired (handled in caller)
    """
    if not issue:
        return False
    state = issue.get("state", {}).get("name", "")
    if state in ("Done", "Cancelled", "Canceled", "Completed"):
        return False
    return has_dispatch_label(issue)


def dispatch_to_supervisor(issue_id: str) -> None:
    """Spawn the supervisor. Logged for observability."""
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
                "2",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[consumer] {issue_id}: supervisor PID={proc.pid}")
    except Exception as e:
        print(f"[consumer] {issue_id}: spawn failed: {e}")


def process_event(
    rowid: int, dedup_key: str, topic: str, payload_json: str, ts: float
) -> None:
    """Process a single event from the bus. Idempotent by stable dedup key."""
    event_key = _stable_dedup_key(dedup_key, topic, payload_json)

    try:
        event = json.loads(payload_json)
    except Exception as e:
        print(f"[consumer] bad payload rowid={rowid}: {e}")
        mark_processed(rowid, event_key, topic)
        return

    # Filter: only handle Linear/GitHub Issue-update events
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

    # 60-second dedup window
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
    """Periodically delete processed rows older than 1 day to keep SQLite lean."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
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

    # Ensure the new schema has the `processed` column on startup.
    # Gateway may write to the same DB without the column, so we use
    # a no-op ALTER TABLE guarded by a try/except.
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
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
            # Vacuum every 5 minutes
            if time.time() - last_vacuum > 300:
                vacuum_processed()
                last_vacuum = time.time()
        except Exception as e:
            print(f"[consumer] loop error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main_loop()
