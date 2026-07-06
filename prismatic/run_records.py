"""
prismatic/run_records.py - Agent run records for tracking the lifecycle of
agent runs.

Uses the shared SQLite runs database by default, with compatibility for the
legacy JSON-file store. All operations are idempotent and thread-safe via
file-level locking.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import fcntl


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentRunRecord:
    """Snapshot of a single agent run."""

    run_id: str
    issue_id: str
    agent_name: str
    status: str = "pending"           # pending | running | completed | failed
    started_at: str = ""              # ISO-8601 string
    completed_at: str | None = None   # ISO-8601 string
    output_path: str | None = None
    error_message: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRunRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# JSON-file backed store
# ---------------------------------------------------------------------------

def _default_store_path() -> str:
    """Resolve the canonical run-history store path.

    Production writes and the gateway ``/runs`` endpoint share the SQLite
    database at ``~/.prismatic/runs.db``.  ``PRISMATIC_RUN_RECORDS_PATH`` is
    retained for tests or legacy JSON-file users; ``PRISMATIC_RUNS_DB`` can
    override the SQLite location without changing the state directory.
    """
    if os.environ.get("PRISMATIC_RUN_RECORDS_PATH"):
        return os.environ["PRISMATIC_RUN_RECORDS_PATH"]
    return os.environ.get(
        "PRISMATIC_RUNS_DB",
        os.path.expanduser("~/.prismatic/runs.db"),
    )


class AgentRunRecordStore:
    """Thread-safe store for agent run records.

    SQLite (``*.db``) is the production format used by the Runs panel.  The
    older JSON list format remains supported for callers that pass an explicit
    non-DB path.  SQLite reads reload from disk so the gateway sees supervisor
    writes made by sibling processes after startup.
    """

    def __init__(self, store_path: str | None = None):
        self._store_path = store_path or _default_store_path()
        self._sqlite = self._store_path.endswith(".db")

        # Ensure parent directory exists
        Path(self._store_path).parent.mkdir(parents=True, exist_ok=True)

        self._records: dict[str, AgentRunRecord] = {}  # run_id -> record
        self._lock_file_path = self._store_path + ".lock"

        self._load_from_disk()

    # -- Internal helpers ----------------------------------------------------

    def _acquire_lock(self) -> int:
        """Acquire an exclusive advisory lock on the lock file.

        Returns the fd so the caller can release it later.
        """
        fd = os.open(self._lock_file_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release_lock(self, fd: int) -> None:
        """Release the advisory lock."""
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _ensure_sqlite_schema(self) -> None:
        """Create the production SQLite schema if it is missing."""
        with sqlite3.connect(self._store_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    issue_id TEXT,
                    agent_name TEXT,
                    status TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    output_path TEXT,
                    error_message TEXT
                )
                """
            )
            conn.commit()

    def _load_from_disk(self) -> None:
        """Load records from disk, falling back to empty on corrupt JSON."""
        if self._sqlite:
            self._ensure_sqlite_schema()
            with sqlite3.connect(self._store_path) as conn:
                rows = conn.execute(
                    """
                    SELECT run_id, issue_id, agent_name, status, started_at,
                           completed_at, output_path, error_message
                    FROM runs
                    """
                ).fetchall()
            self._records = {
                row[0]: AgentRunRecord(
                    run_id=row[0],
                    issue_id=row[1] or "",
                    agent_name=row[2] or "",
                    status=row[3] or "pending",
                    started_at=row[4] or "",
                    completed_at=row[5],
                    output_path=row[6],
                    error_message=row[7],
                )
                for row in rows
            }
            return

        if not os.path.exists(self._store_path):
            self._records = {}
            return

        fd = self._acquire_lock()
        try:
            try:
                with open(self._store_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = []

            if isinstance(data, list):
                self._records = {r["run_id"]: AgentRunRecord.from_dict(r) for r in data}
            else:
                self._records = {}
        finally:
            self._release_lock(fd)

    def _flush_to_disk(self) -> None:
        """Write the in-memory records to disk under a lock."""
        fd = self._acquire_lock()
        try:
            if self._sqlite:
                self._ensure_sqlite_schema()
                with sqlite3.connect(self._store_path) as conn:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO runs (
                            run_id, issue_id, agent_name, status, started_at,
                            completed_at, output_path, error_message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                r.run_id,
                                r.issue_id,
                                r.agent_name,
                                r.status,
                                r.started_at,
                                r.completed_at,
                                r.output_path,
                                r.error_message,
                            )
                            for r in self._records.values()
                        ],
                    )
                    conn.commit()
                return

            serialised = [asdict(r) for r in self._records.values()]
            with open(self._store_path, "w") as f:
                json.dump(serialised, f, indent=2, default=str)
        finally:
            self._release_lock(fd)

    # -- CRUD operations -----------------------------------------------------

    def create_run(self, issue_id: str, agent_name: str) -> str:
        """Create a new run record and persist it.

        Returns the newly generated ``run_id`` (UUID4 string).
        """
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        record = AgentRunRecord(
            run_id=run_id,
            issue_id=issue_id,
            agent_name=agent_name,
            status="pending",
            started_at=now,
        )
        self._records[run_id] = record
        self._flush_to_disk()
        return run_id

    def update_run(
        self,
        run_id: str,
        status: str,
        output_path: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update the status of an existing run.

        Returns ``True`` if the run was found and updated, ``False`` otherwise.
        """
        record = self._records.get(run_id)
        if record is None:
            return False

        record.status = status
        if output_path is not None:
            record.output_path = output_path
        if error is not None:
            record.error_message = error

        if status in ("completed", "failed"):
            record.completed_at = datetime.now(timezone.utc).isoformat()

        self._flush_to_disk()
        return True

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        """Retrieve a single run record by its *run_id*."""
        if self._sqlite:
            self._load_from_disk()
        return self._records.get(run_id)

    def get_runs_for_issue(self, issue_id: str) -> list[AgentRunRecord]:
        """Return all runs for a given *issue_id*, newest first."""
        if self._sqlite:
            self._load_from_disk()
        matching = [
            r for r in self._records.values()
            if r.issue_id == issue_id
        ]
        matching.sort(key=lambda r: r.started_at, reverse=True)
        return matching

    def get_recent_runs(self, limit: int = 10) -> list[AgentRunRecord]:
        """Return the most recent *limit* runs across all issues."""
        if self._sqlite:
            self._load_from_disk()
        sorted_records = sorted(
            self._records.values(),
            key=lambda r: r.started_at,
            reverse=True,
        )
        return sorted_records[:limit]

    def reload(self) -> None:
        """Reload records from disk (useful after external writes)."""
        self._load_from_disk()

    # -- Reporting -----------------------------------------------------------

    def generate_report(self, issue_id: str) -> str:
        """Generate a Markdown summary of all runs for a given issue."""
        runs = self.get_runs_for_issue(issue_id)
        if not runs:
            return f"*No run records found for issue `{issue_id}`.*\n"

        lines = [f"# Run Report for Issue `{issue_id}`\n"]
        for r in runs:
            status_emoji = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
            }.get(r.status, "❓")

            lines.append(f"## {status_emoji} Run `{r.run_id}`")
            lines.append(f"- **Agent:** {r.agent_name}")
            lines.append(f"- **Status:** {r.status}")
            lines.append(f"- **Started:** {r.started_at}")
            if r.completed_at:
                lines.append(f"- **Completed:** {r.completed_at}")
            if r.output_path:
                lines.append(f"- **Output:** `{r.output_path}`")
            if r.error_message:
                lines.append(f"- **Error:** {r.error_message}")
            lines.append("")

        return "\n".join(lines)

    # -- Convenience ---------------------------------------------------------

    @property
    def all_records(self) -> list[AgentRunRecord]:
        """Return all stored records."""
        return list(self._records.values())

    @property
    def record_count(self) -> int:
        """Return the total number of stored records."""
        return len(self._records)
