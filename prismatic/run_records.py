"""
prismatic/run_records.py - Agent run records for tracking the lifecycle of
agent runs.

Uses a simple JSON-file based store (upgradeable to SQLite later).  All
operations are idempotent and thread-safe via file-level locking.
"""

from __future__ import annotations

import json
import os
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
    """Resolve store directory from env or fallback."""
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    return os.path.join(state_dir, "run_records.json")


class AgentRunRecordStore:
    """Thread-safe JSON-file backed store for agent run records.

    The file is kept in memory and flushed on every mutation.  Reads are
    served from the in-memory cache.  File-level advisory locking protects
    against concurrent writer processes.
    """

    def __init__(self, store_path: str | None = None):
        self._store_path = store_path or _default_store_path()

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

    def _load_from_disk(self) -> None:
        """Load records from the JSON file on disk, falling back to empty."""
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
        return self._records.get(run_id)

    def get_runs_for_issue(self, issue_id: str) -> list[AgentRunRecord]:
        """Return all runs for a given *issue_id*, newest first."""
        matching = [
            r for r in self._records.values()
            if r.issue_id == issue_id
        ]
        matching.sort(key=lambda r: r.started_at, reverse=True)
        return matching

    def get_recent_runs(self, limit: int = 10) -> list[AgentRunRecord]:
        """Return the most recent *limit* runs across all issues."""
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
