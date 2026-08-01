"""Replay-safe dead-letter storage for failed Prismatic events.

The dead-letter store is intentionally small and boring: failed payloads are
persisted in SQLite with explicit retry/dead-letter state, bounded retry
metadata, and a replay claim flow.  It is safe to use from cron/drainer scripts
because events are not deleted on replay; rows move through states so an
interrupted replay can be retried without archaeology.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MAX_ATTEMPTS = 3
RETRYABLE_STATUSES = {"retry", "pending_retry"}
DEAD_LETTER_STATUS = "dead_letter"
REPLAY_STATUSES = {DEAD_LETTER_STATUS, *RETRYABLE_STATUSES}


@dataclass(frozen=True)
class DeadLetterEvent:
    """A failed event retained by the dead-letter store."""

    id: int
    event_id: str
    source: str
    event_type: str
    payload: dict[str, Any]
    error: str
    status: str
    attempts: int
    max_attempts: int
    created_at: float
    updated_at: float
    next_retry_at: float | None
    replayed_at: float | None
    replay_note: str


def default_db_path() -> Path:
    """Return the canonical dead-letter SQLite path.

    Respects ``PRISMATIC_DEAD_LETTER_DB`` for tests/operators, otherwise uses
    ``PRISMATIC_STATE_DIR/dead_letter_events.db``.
    """

    override = os.environ.get("PRISMATIC_DEAD_LETTER_DB")
    if override:
        return Path(override)
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))
    return state_dir / "dead_letter_events.db"


class DeadLetterStore:
    """SQLite-backed failed-event store with explicit retry/replay semantics."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letter_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    next_retry_at REAL,
                    replayed_at REAL,
                    replay_note TEXT NOT NULL DEFAULT '',
                    UNIQUE(event_id, source)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dead_letter_replay
                ON dead_letter_events(status, next_retry_at, created_at)
                """
            )

    def record_failure(
        self,
        *,
        event_id: str,
        source: str,
        event_type: str,
        payload: dict[str, Any],
        error: str,
        attempts: int = 0,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_after_seconds: float | None = None,
        now: float | None = None,
    ) -> DeadLetterEvent:
        """Persist a failed event and choose retry vs dead-letter explicitly.

        ``attempts`` is the count after the failed attempt.  Rows below
        ``max_attempts`` are marked ``retry`` with ``next_retry_at``; rows at or
        above the cap are marked ``dead_letter``. Re-recording the same
        ``event_id``/``source`` updates the retained row instead of duplicating
        it, preserving the recoverable payload.
        """

        if not event_id:
            raise ValueError("event_id is required for replay-safe retention")
        if not source:
            raise ValueError("source is required for replay-safe retention")
        if not event_type:
            raise ValueError("event_type is required for replay-safe retention")
        if attempts < 0:
            raise ValueError("attempts must be >= 0")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        now_ts = time.time() if now is None else float(now)
        retryable = attempts < max_attempts
        status = "retry" if retryable else DEAD_LETTER_STATUS
        next_retry_at = None
        if retryable:
            delay = 0 if retry_after_seconds is None else float(retry_after_seconds)
            next_retry_at = now_ts + max(0.0, delay)
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dead_letter_events (
                    event_id, source, event_type, payload_json, error, status,
                    attempts, max_attempts, created_at, updated_at, next_retry_at,
                    replayed_at, replay_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '')
                ON CONFLICT(event_id, source) DO UPDATE SET
                    event_type=excluded.event_type,
                    payload_json=excluded.payload_json,
                    error=excluded.error,
                    status=excluded.status,
                    attempts=excluded.attempts,
                    max_attempts=excluded.max_attempts,
                    updated_at=excluded.updated_at,
                    next_retry_at=excluded.next_retry_at,
                    replayed_at=NULL,
                    replay_note=''
                """,
                (
                    event_id,
                    source,
                    event_type,
                    payload_json,
                    error,
                    status,
                    attempts,
                    max_attempts,
                    now_ts,
                    now_ts,
                    next_retry_at,
                ),
            )
        return self.get(event_id=event_id, source=source)

    def get(self, *, event_id: str, source: str) -> DeadLetterEvent:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dead_letter_events WHERE event_id = ? AND source = ?",
                (event_id, source),
            ).fetchone()
        if row is None:
            raise KeyError(f"dead-letter event not found: {source}:{event_id}")
        return _row_to_event(row)

    def list_events(
        self,
        *,
        statuses: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[DeadLetterEvent]:
        status_list = list(statuses or REPLAY_STATUSES)
        if not status_list:
            return []
        placeholders = ",".join("?" for _ in status_list)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM dead_letter_events
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (*status_list, limit),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def due_for_replay(
        self, *, limit: int = 50, now: float | None = None
    ) -> list[DeadLetterEvent]:
        """Return retryable/dead-letter rows that can be replayed now."""

        now_ts = time.time() if now is None else float(now)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dead_letter_events
                WHERE status IN ('retry', 'pending_retry', 'dead_letter')
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (now_ts, limit),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def mark_replayed(self, *, row_id: int, note: str = "") -> None:
        """Mark a retained failed event as successfully replayed.

        The payload remains in the DB for auditability; it is not deleted.
        """

        now_ts = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE dead_letter_events
                SET status = 'replayed', updated_at = ?, replayed_at = ?, replay_note = ?
                WHERE id = ?
                """,
                (now_ts, now_ts, note, row_id),
            )

    def mark_replay_failed(
        self,
        *,
        row_id: int,
        error: str,
        retry_after_seconds: float | None = None,
    ) -> DeadLetterEvent:
        """Record a replay failure and either reschedule or dead-letter it."""

        now_ts = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dead_letter_events WHERE id = ?",
                (row_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"dead-letter row not found: {row_id}")
            attempts = int(row["attempts"]) + 1
            max_attempts = int(row["max_attempts"])
            status = "retry" if attempts < max_attempts else DEAD_LETTER_STATUS
            delay = 0 if retry_after_seconds is None else float(retry_after_seconds)
            next_retry_at = now_ts + max(0.0, delay) if status == "retry" else None
            conn.execute(
                """
                UPDATE dead_letter_events
                SET status = ?, attempts = ?, error = ?, updated_at = ?, next_retry_at = ?
                WHERE id = ?
                """,
                (status, attempts, error, now_ts, next_retry_at, row_id),
            )
            updated = conn.execute(
                "SELECT * FROM dead_letter_events WHERE id = ?",
                (row_id,),
            ).fetchone()
        return _row_to_event(updated)


def replay(
    store: DeadLetterStore, handler, *, limit: int = 50, now: float | None = None
) -> dict[str, int]:
    """Replay due events through ``handler(event)``.

    ``handler`` receives a :class:`DeadLetterEvent` and should return truthy on
    success.  Exceptions and falsey returns are retained via
    :meth:`mark_replay_failed`, so replay never drops a failed item silently.
    """

    stats = {"selected": 0, "replayed": 0, "failed": 0}
    for event in store.due_for_replay(limit=limit, now=now):
        stats["selected"] += 1
        try:
            ok = bool(handler(event))
        except Exception as exc:  # pragma: no cover - exact handler is caller-owned
            ok = False
            error = str(exc)
        else:
            error = "replay handler returned false"
        if ok:
            store.mark_replayed(row_id=event.id, note="replay succeeded")
            stats["replayed"] += 1
        else:
            store.mark_replay_failed(row_id=event.id, error=error)
            stats["failed"] += 1
    return stats


def _row_to_event(row: sqlite3.Row) -> DeadLetterEvent:
    payload_raw = row["payload_json"] or "{}"
    try:
        payload = json.loads(payload_raw)
    except Exception:
        payload = {"_unparseable_payload_json": payload_raw}
    return DeadLetterEvent(
        id=int(row["id"]),
        event_id=str(row["event_id"]),
        source=str(row["source"]),
        event_type=str(row["event_type"]),
        payload=payload,
        error=str(row["error"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        next_retry_at=row["next_retry_at"],
        replayed_at=row["replayed_at"],
        replay_note=str(row["replay_note"] or ""),
    )
