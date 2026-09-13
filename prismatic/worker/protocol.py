"""Typed protocol models for distributed worker execution and job dispatch."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WorkerJob:
    """A unit of work dispatched across the distributed mesh."""

    id: str
    task_id: str
    command: str
    target: str = "default"
    tags: list[str] = field(default_factory=lambda: ["general"])
    timeout_seconds: int = 300
    status: str = "queued"  # queued, leased, running, completed, failed, timeout
    node_id: str | None = None
    lease_id: str | None = None
    fence_token: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerJob:
        return cls(
            id=data["id"],
            task_id=data.get("task_id", ""),
            command=data.get("command", ""),
            target=data.get("target", "default"),
            tags=data.get("tags") or ["general"],
            timeout_seconds=data.get("timeout_seconds", 300),
            status=data.get("status", "queued"),
            node_id=data.get("node_id"),
            lease_id=data.get("lease_id"),
            fence_token=data.get("fence_token", 0),
            metadata=data.get("metadata") or {},
            result=data.get("result") or {},
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class WorkerNode:
    """A compute node registered in the distributed fleet."""

    node_id: str
    hostname: str
    ip: str = "127.0.0.1"
    tags: list[str] = field(default_factory=lambda: ["general"])
    last_heartbeat: float = field(default_factory=time.time)
    status: str = "active"  # active, stale, offline
    active_job_id: str | None = None
    version: str = "0.2.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerNode:
        return cls(
            node_id=data["node_id"],
            hostname=data.get("hostname", "unknown"),
            ip=data.get("ip", "127.0.0.1"),
            tags=data.get("tags") or ["general"],
            last_heartbeat=data.get("last_heartbeat", time.time()),
            status=data.get("status", "active"),
            active_job_id=data.get("active_job_id"),
            version=data.get("version", "0.2.0"),
        )


@dataclass
class WorkerReceipt:
    """Machine-attested execution receipt returned by a worker upon job completion."""

    job_id: str
    node_id: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    artifacts: dict[str, Any] = field(default_factory=dict)
    dld_digest: str = ""  # Deterministic Log Digest SHA-256

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerReceipt:
        return cls(
            job_id=data["job_id"],
            node_id=data["node_id"],
            exit_code=data.get("exit_code", 0),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            artifacts=data.get("artifacts") or {},
            dld_digest=data.get("dld_digest", ""),
        )
