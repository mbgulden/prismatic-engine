"""
prismatic/run_records.py - Agent run records for tracking the lifecycle of
agent runs.

Uses a simple JSON-file based store (upgradeable to SQLite later).  All
operations are idempotent and thread-safe via file-level locking.
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.execution_evidence import (
    ExecutionEvidence,
    FailureCategory,
    VerificationScope,
    VerificationStatus,
    done_gate,
    validate_evidence,
)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentRunRecord:
    """Snapshot of a single agent run."""

    run_id: str
    issue_id: str
    agent_name: str
    status: str = "pending"  # pending | running | completed | failed
    started_at: str = ""  # ISO-8601 string
    completed_at: str | None = None  # ISO-8601 string
    output_path: str | None = None
    error_message: str | None = None
    evidence: dict[str, Any] | None = None
    verification_status: str = VerificationStatus.SELF_REPORTED.value
    verification_scope: str = VerificationScope.NOT_RUN.value
    failure_category: str = FailureCategory.NONE.value
    cleanup_status: str = "not_reported"
    done_gate_result: str = "not_done"
    done_gate_errors: list[str] = field(default_factory=list)

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
    """Thread-safe JSON or SQLite backed store for agent run records.

    If store_path has a SQLite extension (e.g. .db), it delegates to SQLite.
    Otherwise, it defaults to JSON-file backing.
    """

    def __init__(self, store_path: str | None = None):
        self._store_path = store_path or _default_store_path()
        self._is_sqlite = self._store_path.endswith(".db") or "sqlite" in self._store_path

        # Ensure parent directory exists
        Path(self._store_path).parent.mkdir(parents=True, exist_ok=True)

        if self._is_sqlite:
            self._init_sqlite()
        else:
            self._records: dict[str, AgentRunRecord] = {}  # run_id -> record
            self._lock_file_path = self._store_path + ".lock"
            self._load_from_disk()

    # -- SQLite helpers ------------------------------------------------------

    def _init_sqlite(self) -> None:
        import sqlite3
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
                    error_message TEXT,
                    evidence TEXT,
                    verification_status TEXT,
                    verification_scope TEXT,
                    failure_category TEXT,
                    cleanup_status TEXT,
                    done_gate_result TEXT,
                    done_gate_errors TEXT
                )
                """
            )

    def _get_columns(self) -> list[str]:
        import sqlite3
        with sqlite3.connect(self._store_path) as conn:
            cursor = conn.execute("PRAGMA table_info(runs)")
            return [row[1] for row in cursor.fetchall()]

    def _row_to_record(self, row: tuple, columns: list[str]) -> AgentRunRecord:
        row_dict = dict(zip(columns, row))
        evidence = None
        if row_dict.get("evidence"):
            try:
                evidence = json.loads(row_dict["evidence"])
            except Exception:
                pass
        done_gate_errors = []
        if row_dict.get("done_gate_errors"):
            try:
                done_gate_errors = json.loads(row_dict["done_gate_errors"])
            except Exception:
                pass
        return AgentRunRecord(
            run_id=row_dict.get("run_id"),
            issue_id=row_dict.get("issue_id"),
            agent_name=row_dict.get("agent_name"),
            status=row_dict.get("status", "pending"),
            started_at=row_dict.get("started_at", ""),
            completed_at=row_dict.get("completed_at"),
            output_path=row_dict.get("output_path"),
            error_message=row_dict.get("error_message"),
            evidence=evidence,
            verification_status=row_dict.get("verification_status", "self_reported"),
            verification_scope=row_dict.get("verification_scope", "not_run"),
            failure_category=row_dict.get("failure_category", "none"),
            cleanup_status=row_dict.get("cleanup_status", "not_reported"),
            done_gate_result=row_dict.get("done_gate_result", "not_done"),
            done_gate_errors=done_gate_errors,
        )

    # -- Internal helpers (JSON only) ----------------------------------------

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

        if self._is_sqlite:
            import sqlite3
            columns = self._get_columns()
            val_map = {
                "run_id": run_id,
                "issue_id": issue_id,
                "agent_name": agent_name,
                "status": "pending",
                "started_at": now,
            }
            insert_cols = [c for c in columns if c in val_map]
            placeholders = ", ".join(["?"] * len(insert_cols))
            sql = f"INSERT INTO runs ({', '.join(insert_cols)}) VALUES ({placeholders})"
            params = [val_map[c] for c in insert_cols]
            with sqlite3.connect(self._store_path) as conn:
                conn.execute(sql, params)
                conn.commit()
            return run_id

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
        evidence: ExecutionEvidence | dict[str, Any] | None = None,
    ) -> bool:
        """Update the status of an existing run.

        Returns ``True`` if the run was found and updated, ``False`` otherwise.
        """
        if self._is_sqlite:
            import sqlite3
            record = self.get_run(run_id)
            if record is None:
                return False

            record.status = status
            if output_path is not None:
                record.output_path = output_path
            if error is not None:
                record.error_message = error
            if evidence is not None:
                self._apply_evidence(record, evidence)
            elif status in ("completed", "failed") and record.evidence is None:
                gate_result, gate_errors = done_gate(status, None)
                record.verification_status = VerificationStatus.SELF_REPORTED.value
                record.verification_scope = VerificationScope.NOT_RUN.value
                record.failure_category = FailureCategory.NONE.value
                record.cleanup_status = "not_reported"
                record.done_gate_result = gate_result
                record.done_gate_errors = gate_errors

            if status in ("completed", "failed"):
                record.completed_at = datetime.now(timezone.utc).isoformat()

            columns = self._get_columns()
            val_map = {
                "status": record.status,
                "output_path": record.output_path,
                "error_message": record.error_message,
                "completed_at": record.completed_at,
                "evidence": json.dumps(record.evidence) if record.evidence else None,
                "verification_status": record.verification_status,
                "verification_scope": record.verification_scope,
                "failure_category": record.failure_category,
                "cleanup_status": record.cleanup_status,
                "done_gate_result": record.done_gate_result,
                "done_gate_errors": json.dumps(record.done_gate_errors) if record.done_gate_errors else None,
            }
            update_pairs = []
            params = []
            for c in columns:
                if c in val_map and c != "run_id":
                    update_pairs.append(f"{c} = ?")
                    params.append(val_map[c])
            params.append(run_id)
            sql = f"UPDATE runs SET {', '.join(update_pairs)} WHERE run_id = ?"
            with sqlite3.connect(self._store_path) as conn:
                conn.execute(sql, params)
                conn.commit()
            return True

        record = self._records.get(run_id)
        if record is None:
            return False

        record.status = status
        if output_path is not None:
            record.output_path = output_path
        if error is not None:
            record.error_message = error
        if evidence is not None:
            self._apply_evidence(record, evidence)
        elif status in ("completed", "failed") and record.evidence is None:
            gate_result, gate_errors = done_gate(status, None)
            record.verification_status = VerificationStatus.SELF_REPORTED.value
            record.verification_scope = VerificationScope.NOT_RUN.value
            record.failure_category = FailureCategory.NONE.value
            record.cleanup_status = "not_reported"
            record.done_gate_result = gate_result
            record.done_gate_errors = gate_errors

        if status in ("completed", "failed"):
            record.completed_at = datetime.now(timezone.utc).isoformat()

        self._flush_to_disk()
        return True

    def attach_evidence(
        self, run_id: str, evidence: ExecutionEvidence | dict[str, Any]
    ) -> bool:
        """Attach canonical execution evidence to an existing run."""
        if self._is_sqlite:
            import sqlite3
            record = self.get_run(run_id)
            if record is None:
                return False
            self._apply_evidence(record, evidence)

            columns = self._get_columns()
            val_map = {
                "evidence": json.dumps(record.evidence) if record.evidence else None,
                "verification_status": record.verification_status,
                "verification_scope": record.verification_scope,
                "failure_category": record.failure_category,
                "cleanup_status": record.cleanup_status,
                "done_gate_result": record.done_gate_result,
                "done_gate_errors": json.dumps(record.done_gate_errors) if record.done_gate_errors else None,
            }
            update_pairs = []
            params = []
            for c in columns:
                if c in val_map and c != "run_id":
                    update_pairs.append(f"{c} = ?")
                    params.append(val_map[c])
            params.append(run_id)
            sql = f"UPDATE runs SET {', '.join(update_pairs)} WHERE run_id = ?"
            with sqlite3.connect(self._store_path) as conn:
                conn.execute(sql, params)
                conn.commit()
            return True

        record = self._records.get(run_id)
        if record is None:
            return False
        self._apply_evidence(record, evidence)
        self._flush_to_disk()
        return True

    def _apply_evidence(
        self,
        record: AgentRunRecord,
        evidence: ExecutionEvidence | dict[str, Any],
    ) -> None:
        parsed = (
            evidence
            if isinstance(evidence, ExecutionEvidence)
            else ExecutionEvidence.from_dict(evidence)
        )
        errors = validate_evidence(parsed)
        gate_result, gate_errors = done_gate(record.status, parsed)
        record.evidence = parsed.to_dict()
        record.verification_status = parsed.status.value
        record.verification_scope = parsed.scope.value
        record.failure_category = parsed.failure_category.value
        record.cleanup_status = parsed.cleanup_status
        record.done_gate_result = gate_result
        record.done_gate_errors = errors + [
            error for error in gate_errors if error not in errors
        ]

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        """Retrieve a single run record by its *run_id*."""
        if self._is_sqlite:
            import sqlite3
            columns = self._get_columns()
            sql = f"SELECT {', '.join(columns)} FROM runs WHERE run_id = ?"
            with sqlite3.connect(self._store_path) as conn:
                row = conn.execute(sql, (run_id,)).fetchone()
                if row:
                    return self._row_to_record(row, columns)
                return None

        return self._records.get(run_id)

    def get_runs_for_issue(self, issue_id: str) -> list[AgentRunRecord]:
        """Return all runs for a given *issue_id*, newest first."""
        if self._is_sqlite:
            import sqlite3
            columns = self._get_columns()
            sql = f"SELECT {', '.join(columns)} FROM runs WHERE issue_id = ? ORDER BY started_at DESC"
            with sqlite3.connect(self._store_path) as conn:
                rows = conn.execute(sql, (issue_id,)).fetchall()
                return [self._row_to_record(r, columns) for r in rows]

        matching = [r for r in self._records.values() if r.issue_id == issue_id]
        matching.sort(key=lambda r: r.started_at, reverse=True)
        return matching

    def get_recent_runs(self, limit: int = 10) -> list[AgentRunRecord]:
        """Return the most recent *limit* runs across all issues."""
        if self._is_sqlite:
            import sqlite3
            columns = self._get_columns()
            sql = f"SELECT {', '.join(columns)} FROM runs ORDER BY started_at DESC LIMIT ?"
            with sqlite3.connect(self._store_path) as conn:
                rows = conn.execute(sql, (limit,)).fetchall()
                return [self._row_to_record(r, columns) for r in rows]

        sorted_records = sorted(
            self._records.values(),
            key=lambda r: r.started_at,
            reverse=True,
        )
        return sorted_records[:limit]

    def reload(self) -> None:
        """Reload records from disk (useful after external writes)."""
        if self._is_sqlite:
            pass
        else:
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
            lines.append(f"- **Verification:** {r.verification_status}")
            lines.append(f"- **Verification scope:** {r.verification_scope}")
            lines.append(f"- **Failure category:** {r.failure_category}")
            lines.append(f"- **Cleanup:** {r.cleanup_status}")
            lines.append(f"- **Done gate:** {r.done_gate_result}")
            if r.done_gate_errors:
                joined_errors = "; ".join(r.done_gate_errors)
                lines.append(f"- **Done gate errors:** {joined_errors}")
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
        if self._is_sqlite:
            import sqlite3
            columns = self._get_columns()
            sql = f"SELECT {', '.join(columns)} FROM runs"
            with sqlite3.connect(self._store_path) as conn:
                rows = conn.execute(sql).fetchall()
                return [self._row_to_record(r, columns) for r in rows]

        return list(self._records.values())

    @property
    def record_count(self) -> int:
        """Return the total number of stored records."""
        if self._is_sqlite:
            import sqlite3
            with sqlite3.connect(self._store_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
                return row[0] if row else 0

        return len(self._records)
