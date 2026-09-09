"""Persistent SQLite queue manager for distributed jobs and worker registry."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from prismatic.worker.protocol import WorkerJob, WorkerNode

logger = logging.getLogger("prismatic.worker.queue")


class WorkerQueueManager:
    """Manages distributed job leases, worker registration, and task lifecycle."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            base_dir = Path.home() / ".prismatic"
            base_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = base_dir / "worker_queue.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    target TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    node_id TEXT,
                    lease_id TEXT,
                    fence_token INTEGER NOT NULL,
                    result TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    lease_expires_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workers (
                    node_id TEXT PRIMARY KEY,
                    hostname TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    status TEXT NOT NULL,
                    active_job_id TEXT,
                    version TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def enqueue_job(
        self,
        task_id: str,
        command: str,
        target: str = "default",
        tags: list[str] | None = None,
        timeout_seconds: int = 300,
    ) -> WorkerJob:
        """Enqueue a new distributed work contract."""
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now = time.time()
        job_tags = tags or ["general"]

        job = WorkerJob(
            id=job_id,
            task_id=task_id,
            command=command,
            target=target,
            tags=job_tags,
            timeout_seconds=timeout_seconds,
            status="queued",
            fence_token=0,
            result={},
            created_at=now,
            updated_at=now,
        )

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, task_id, command, target, tags, timeout_seconds,
                    status, node_id, lease_id, fence_token, result,
                    created_at, updated_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.task_id,
                    job.command,
                    job.target,
                    json.dumps(job.tags),
                    job.timeout_seconds,
                    job.status,
                    None,
                    None,
                    job.fence_token,
                    json.dumps(job.result),
                    job.created_at,
                    job.updated_at,
                    0.0,
                ),
            )
            conn.commit()

        logger.info("Enqueued worker job %s for task %s", job.id, task_id)
        return job

    def lease_next_job(
        self,
        node_id: str,
        tags: list[str] | None = None,
        ttl_seconds: int = 60,
    ) -> WorkerJob | None:
        """Atomically claim the next matching queued job for a worker node."""
        worker_tags = set(tags or ["general"])
        now = time.time()

        with self._get_connection() as conn:
            # 1. Recover expired leases
            conn.execute(
                """
                UPDATE jobs
                SET status = 'queued', node_id = NULL, lease_id = NULL, lease_expires_at = 0.0, updated_at = ?
                WHERE status IN ('leased', 'running') AND lease_expires_at > 0 AND lease_expires_at < ?
                """,
                (now, now),
            )

            # 2. Find eligible queued jobs
            cur = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC"
            )
            rows = cur.fetchall()

            matched_row = None
            for row in rows:
                try:
                    job_tags = set(json.loads(row["tags"]))
                except Exception:
                    job_tags = {"general"}

                # If job tags overlap with worker tags, or job has general
                if "general" in job_tags or bool(job_tags.intersection(worker_tags)):
                    matched_row = row
                    break

            if not matched_row:
                return None

            job_id = matched_row["id"]
            new_fence = matched_row["fence_token"] + 1
            lease_id = str(uuid.uuid4())
            lease_expires = now + ttl_seconds

            # 3. Atomically acquire lease
            update_cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'leased', node_id = ?, lease_id = ?, fence_token = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (node_id, lease_id, new_fence, lease_expires, now, job_id),
            )

            if update_cur.rowcount == 0:
                return None  # Race condition lost

            # Update worker record
            conn.execute(
                """
                UPDATE workers
                SET active_job_id = ?, last_heartbeat = ?, status = 'active'
                WHERE node_id = ?
                """,
                (job_id, now, node_id),
            )
            conn.commit()

            return WorkerJob(
                id=job_id,
                task_id=matched_row["task_id"],
                command=matched_row["command"],
                target=matched_row["target"],
                tags=json.loads(matched_row["tags"]),
                timeout_seconds=matched_row["timeout_seconds"],
                status="leased",
                node_id=node_id,
                lease_id=lease_id,
                fence_token=new_fence,
                result=json.loads(matched_row["result"] or "{}"),
                created_at=matched_row["created_at"],
                updated_at=now,
            )

    def heartbeat_job(
        self,
        job_id: str,
        node_id: str,
        fence_token: int,
        ttl_seconds: int = 60,
    ) -> bool:
        """Extend the lease TTL for an actively executing job."""
        now = time.time()
        lease_expires = now + ttl_seconds

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, status = 'running', updated_at = ?
                WHERE id = ? AND node_id = ? AND fence_token = ? AND status IN ('leased', 'running')
                """,
                (lease_expires, now, job_id, node_id, fence_token),
            )
            conn.commit()
            return cur.rowcount > 0

    def complete_job(
        self,
        job_id: str,
        node_id: str,
        fence_token: int,
        receipt: dict[str, Any],
    ) -> bool:
        """Mark a job as completed or failed based on worker receipt."""
        now = time.time()
        exit_code = receipt.get("exit_code", 0)
        final_status = "completed" if exit_code == 0 else "failed"

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = ?, result = ?, updated_at = ?, lease_expires_at = 0.0
                WHERE id = ? AND node_id = ? AND fence_token = ? AND status IN ('leased', 'running')
                """,
                (final_status, json.dumps(receipt), now, job_id, node_id, fence_token),
            )
            if cur.rowcount > 0:
                conn.execute(
                    "UPDATE workers SET active_job_id = NULL, last_heartbeat = ? WHERE node_id = ?",
                    (now, node_id),
                )
                conn.commit()
                return True
            return False

    def register_worker(
        self,
        node_id: str,
        hostname: str,
        ip: str = "127.0.0.1",
        tags: list[str] | None = None,
        version: str = "0.2.0",
    ) -> WorkerNode:
        """Register or update a compute worker node."""
        now = time.time()
        worker_tags = tags or ["general"]

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO workers (node_id, hostname, ip, tags, last_heartbeat, status, active_job_id, version)
                VALUES (?, ?, ?, ?, ?, 'active', NULL, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    hostname = excluded.hostname,
                    ip = excluded.ip,
                    tags = excluded.tags,
                    last_heartbeat = excluded.last_heartbeat,
                    status = 'active',
                    version = excluded.version
                """,
                (node_id, hostname, ip, json.dumps(worker_tags), now, version),
            )
            conn.commit()

        return WorkerNode(
            node_id=node_id,
            hostname=hostname,
            ip=ip,
            tags=worker_tags,
            last_heartbeat=now,
            status="active",
            version=version,
        )

    def heartbeat_worker(self, node_id: str) -> bool:
        """Record a heartbeat timestamp for an active worker node."""
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE workers SET last_heartbeat = ?, status = 'active' WHERE node_id = ?",
                (now, node_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_workers(self) -> list[WorkerNode]:
        """List all known worker nodes with live health status."""
        now = time.time()
        workers = []
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM workers ORDER BY last_heartbeat DESC")
            for row in cur.fetchall():
                delta = now - row["last_heartbeat"]
                status = row["status"]
                if delta > 300:
                    status = "offline"
                elif delta > 60:
                    status = "stale"

                workers.append(
                    WorkerNode(
                        node_id=row["node_id"],
                        hostname=row["hostname"],
                        ip=row["ip"],
                        tags=json.loads(row["tags"]),
                        last_heartbeat=row["last_heartbeat"],
                        status=status,
                        active_job_id=row["active_job_id"],
                        version=row["version"],
                    )
                )
        return workers

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[WorkerJob]:
        """List jobs optionally filtered by status."""
        jobs = []
        with self._get_connection() as conn:
            if status:
                cur = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )

            for row in cur.fetchall():
                jobs.append(
                    WorkerJob(
                        id=row["id"],
                        task_id=row["task_id"],
                        command=row["command"],
                        target=row["target"],
                        tags=json.loads(row["tags"]),
                        timeout_seconds=row["timeout_seconds"],
                        status=row["status"],
                        node_id=row["node_id"],
                        lease_id=row["lease_id"],
                        fence_token=row["fence_token"],
                        result=json.loads(row["result"] or "{}"),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
        return jobs

    def get_job(self, job_id: str) -> WorkerJob | None:
        """Fetch details for a specific job."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return None

            return WorkerJob(
                id=row["id"],
                task_id=row["task_id"],
                command=row["command"],
                target=row["target"],
                tags=json.loads(row["tags"]),
                timeout_seconds=row["timeout_seconds"],
                status=row["status"],
                node_id=row["node_id"],
                lease_id=row["lease_id"],
                fence_token=row["fence_token"],
                result=json.loads(row["result"] or "{}"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
