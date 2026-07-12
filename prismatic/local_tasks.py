"""
Local task queue for bare-metal Prismatic Engine usage.

This module intentionally has no Linear or harness dependency. It gives a
fresh install a useful spine: create a task locally, persist it in the same
SQLite state surface as the rest of the engine, and let the dispatcher pick
it up before polling external task providers.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")) / "event_router.db"

COMPLETED_STATUSES = {"complete", "completed", "done", "success", "succeeded"}
PROOF_METADATA_KEYS = (
    "artifact_path",
    "result_artifact",
    "result_path",
    "completion_marker",
    "completion_proof",
)


def has_completion_proof(metadata: dict[str, Any] | None) -> bool:
    """Return True when *metadata* carries a durable completion proof.

    A finished local run must leave either a result artifact path or an
    explicit completion marker. Truthy process returns are only dispatch
    acknowledgements; completion is not inferred from silence.
    """
    if not metadata:
        return False
    return any(bool(metadata.get(key)) for key in PROOF_METADATA_KEYS)


def require_completion_proof(status: str, metadata: dict[str, Any] | None) -> None:
    """Reject successful terminal status updates that do not carry proof."""
    if status.strip().lower() in COMPLETED_STATUSES and not has_completion_proof(metadata):
        raise ValueError(
            "completed local tasks require artifact_path, result_path, "
            "completion_marker, or completion_proof metadata"
        )


@dataclass(frozen=True)
class LocalTask:
    id: str
    title: str
    agent: str
    workspace: str
    status: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


class LocalTaskQueue:
    """SQLite-backed queue for tasks that do not require Linear."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        from prismatic.admin import cmd_db_upgrade
        cmd_db_upgrade(str(self.db_path))

    def create(
        self,
        *,
        title: str,
        agent: str,
        workspace: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> LocalTask:
        title = title.strip()
        agent = agent.strip().lower()
        if not title:
            raise ValueError("local task title is required")
        if not agent:
            raise ValueError("local task agent is required")

        now = datetime.now(timezone.utc).isoformat()
        task_id = f"local-{uuid.uuid4().hex[:12]}"
        workspace_path = str(Path(workspace).expanduser().resolve())
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO local_tasks (
                    id, agent, title, workspace, status, created_at,
                    updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (task_id, agent, title, workspace_path, now, now, metadata_json),
            )
            conn.commit()
        return self.get(task_id)

    def get(self, task_id: str) -> LocalTask:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM local_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._row_to_task(row)

    def list_queued(self, *, agent: str | None = None, limit: int = 25) -> list[LocalTask]:
        params: list[Any] = ["queued"]
        sql = "SELECT * FROM local_tasks WHERE status = ?"
        if agent:
            sql += " AND agent = ?"
            params.append(agent.strip().lower())
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_status(self, task_id: str, status: str, *, metadata_patch: dict[str, Any] | None = None) -> LocalTask:
        current = self.get(task_id)
        metadata = dict(current.metadata)
        if metadata_patch:
            metadata.update(metadata_patch)
        normalized_status = status.strip().lower()
        require_completion_proof(normalized_status, metadata)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE local_tasks
                SET status = ?, updated_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (normalized_status, now, json.dumps(metadata, sort_keys=True), task_id),
            )
            conn.commit()
        return self.get(task_id)

    def complete(
        self,
        task_id: str,
        *,
        artifact_path: str | Path | None = None,
        completion_marker: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> LocalTask:
        """Mark a local task completed only when durable proof is supplied."""
        patch = dict(metadata_patch or {})
        if artifact_path is not None:
            patch["artifact_path"] = str(Path(artifact_path).expanduser())
        if completion_marker:
            patch["completion_marker"] = completion_marker
        return self.update_status(task_id, "completed", metadata_patch=patch)

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> LocalTask:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return LocalTask(
            id=row["id"],
            title=row["title"],
            agent=row["agent"],
            workspace=row["workspace"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=metadata,
        )


def get_default_queue() -> LocalTaskQueue:
    return LocalTaskQueue()
