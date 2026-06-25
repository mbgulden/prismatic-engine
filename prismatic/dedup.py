"""
prismatic/dedup.py - SQLite-backed dedup database for event deduplication.

Thread-safe SQLite store with TTL-based expiry. Extracted from the Hermes
event_router_dedup pattern, made configurable with environment variable support.
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path


# Default TTLs (in seconds) by event type
DEFAULT_TTLS = {
    "linear": 3600,       # 1 hour
    "command": 86400,     # 24 hours
    "github": 86400,      # 24 hours
    "cron": 1800,         # 30 minutes
    "manual": 259200,     # 72 hours
}


def _default_db_path() -> str:
    """Resolve the default database directory from env or fallback."""
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    return os.path.join(state_dir, "dedup.db")


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

def linear_key(issue_id: str, agent_label: str) -> str:
    """Build a dedup key for a Linear dispatch event."""
    return f"linear:{issue_id}:{agent_label}"


def command_key(issue_id: str, comment_id: str) -> str:
    """Build a dedup key for a command/comment event."""
    return f"command:{issue_id}:{comment_id}"


def github_key(repo: str, pr_number: int | str, event: str) -> str:
    """Build a dedup key for a GitHub event."""
    return f"github:{repo}:{pr_number}:{event}"


def cron_key(queue: str, bucket: str, issue_id: str) -> str:
    """Build a dedup key for a cron / scheduled event."""
    return f"cron:{queue}:{bucket}:{issue_id}"


def manual_key(event_id: str) -> str:
    """Build a dedup key for a manually triggered event."""
    return f"manual:{event_id}"


# ---------------------------------------------------------------------------
# EventRouterDedup
# ---------------------------------------------------------------------------

class EventRouterDedup:
    """Thread-safe SQLite-backed deduplication database.

    Auto-creates the database directory and schema on first use.  Entries
    expire based on their TTL and are lazily/prudently cleaned up.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _default_db_path()
        self._lock = threading.Lock()

        # Ensure parent directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # Set busy timeout so concurrent webhook dispatches don't fail with
        # "database is locked". 5s is enough for typical write contention.
        # GRO-2400 follow-up: every webhook handler creates a fresh
        # EventRouterDedup, so multiple in-flight dispatches can race on the
        # SQLite WAL. Without a timeout, the loser fails immediately.
        try:
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode = WAL")
        except Exception:
            pass
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the schema if it doesn't already exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                dedup_key TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                ttl INTEGER NOT NULL,
                metadata TEXT,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_events_expires
            ON processed_events(expires_at)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_events_type
            ON processed_events(event_type)
        """)
        self._conn.commit()

    # -- Connection management -----------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the underlying connection for advanced use."""
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # -- Core operations -----------------------------------------------------

    def is_processed(self, dedup_key: str) -> bool:
        """Return True if *dedup_key* has been marked and is not expired."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM processed_events WHERE dedup_key = ? AND expires_at > ?",
                (dedup_key, time.time()),
            ).fetchone()
            return row is not None

    def mark_processed(
        self,
        dedup_key: str,
        event_type: str,
        ttl: int | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Mark an event as processed, optionally with a custom *ttl* (seconds).

        *ttl* defaults to ``DEFAULT_TTLS[event_type]`` or 3600 if unknown.
        """
        if ttl is None:
            ttl = DEFAULT_TTLS.get(event_type, 3600)

        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO processed_events
                   (dedup_key, event_type, ttl, metadata, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dedup_key, event_type, ttl, json.dumps(metadata or {}), now, now + ttl),
            )
            self._conn.commit()

    # -- Convenience helpers -------------------------------------------------

    def mark_issue_dispatched(self, issue_id: str, agent_label: str) -> None:
        """Convenience: mark a Linear issue as dispatched to an agent."""
        self.mark_processed(
            linear_key(issue_id, agent_label),
            event_type="linear",
        )

    def is_issue_dispatched_recently(self, issue_id: str, agent_label: str) -> bool:
        """Convenience: check if a Linear issue was recently dispatched."""
        return self.is_processed(linear_key(issue_id, agent_label))

    def mark_command_processed(self, issue_id: str, comment_id: str) -> None:
        """Convenience: mark a /command comment as processed."""
        self.mark_processed(
            command_key(issue_id, comment_id),
            event_type="command",
        )

    def is_command_processed(self, issue_id: str, comment_id: str) -> bool:
        """Convenience: check if a /command comment was processed."""
        return self.is_processed(command_key(issue_id, comment_id))

    # -- Cleanup -------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Remove all expired entries.  Returns the number of rows deleted."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM processed_events WHERE expires_at <= ?",
                (time.time(),),
            )
            self._conn.commit()
            return cursor.rowcount

    def force_cleanup(self) -> int:
        """Remove *all* entries regardless of TTL.  Returns rows deleted."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM processed_events")
            self._conn.commit()
            return cursor.rowcount

    # -- Stats ---------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return a dictionary of database statistics."""
        with self._lock:
            now = time.time()
            total = self._conn.execute(
                "SELECT COUNT(*) AS c FROM processed_events"
            ).fetchone()["c"]
            active = self._conn.execute(
                "SELECT COUNT(*) AS c FROM processed_events WHERE expires_at > ?",
                (now,),
            ).fetchone()["c"]
            expired = total - active

            by_type_rows = self._conn.execute(
                "SELECT event_type, COUNT(*) AS c FROM processed_events GROUP BY event_type"
            ).fetchall()
            by_type = {row["event_type"]: row["c"] for row in by_type_rows}

            db_size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0

        return {
            "total": total,
            "active": active,
            "expired": expired,
            "by_type": by_type,
            "db_size": db_size,
            "db_path": self._db_path,
        }


# ---------------------------------------------------------------------------
# Singleton helper
# ---------------------------------------------------------------------------

_global_dedup: EventRouterDedup | None = None
_global_dedup_lock = threading.Lock()


def get_dedup(db_path: str | None = None) -> EventRouterDedup:
    """Return a shared singleton ``EventRouterDedup`` instance."""
    global _global_dedup
    if _global_dedup is None:
        with _global_dedup_lock:
            if _global_dedup is None:
                _global_dedup = EventRouterDedup(db_path=db_path)
    return _global_dedup


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Prismatic Engine Dedup Database CLI",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the dedup SQLite database (default: env PRISMATIC_STATE_DIR or ./prismatic_state/)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("cleanup", help="Remove expired entries")
    subparsers.add_parser("stats", help="Show database statistics")

    check_parser = subparsers.add_parser("check", help="Check if a dedup key is processed")
    check_parser.add_argument("dedup_key", help="The dedup key to check")

    args = parser.parse_args()
    dedup = get_dedup(db_path=args.db_path)

    if args.command == "cleanup":
        count = dedup.cleanup_expired()
        print(f"Cleaned up {count} expired entries.")
    elif args.command == "stats":
        stats = dedup.get_stats()
        print(f"Total entries:    {stats['total']}")
        print(f"Active entries:   {stats['active']}")
        print(f"Expired entries:  {stats['expired']}")
        print(f"By type:          {stats['by_type']}")
        print(f"DB size (bytes):  {stats['db_size']}")
        print(f"DB path:          {stats['db_path']}")
    elif args.command == "check":
        result = dedup.is_processed(args.dedup_key)
        print(f"Key '{args.dedup_key}': {'PROCESSED' if result else 'NOT PROCESSED'}")


if __name__ == "__main__":
    _cli()
