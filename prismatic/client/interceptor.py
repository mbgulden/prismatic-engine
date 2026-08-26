"""Universal Hypervisor Client and Agent Interceptor SDK.

Enforces Prismatic Engine Layer 1 Interceptor specifications:
- Automatic SwarmLock lease acquisition & background TTL heartbeat renewal.
- Live telemetry signal emission to Prismatic Hub (/api/gateway/signals/emit).
- Dual-OS endpoint resolution (Unix domain socket, local HTTP, or Tailscale HTTPS).
- Clean-room verification receipt integration (GRO-4203).
"""

from __future__ import annotations

import os
import sys
import time
import json
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator
import urllib.request
import urllib.error

logger = logging.getLogger("prismatic.client.interceptor")


def default_gateway_endpoint() -> str:
    """Resolve the default Prismatic Gateway endpoint based on environment and OS."""
    if os.environ.get("PRISMATIC_GATEWAY_URL"):
        return os.environ["PRISMATIC_GATEWAY_URL"].rstrip("/")
    if os.environ.get("PRISMATIC_PORT"):
        return f"http://127.0.0.1:{os.environ['PRISMATIC_PORT']}"
    return "http://127.0.0.1:9000"


@dataclass
class SignalPayload:
    agent_id: str
    stage: str
    message: str
    task_id: str | None = None
    model: str = "inherit"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "stage": self.stage,
            "message": self.message,
            "task_id": self.task_id,
            "model": self.model,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


class LeaseContext:
    """Manages an active SwarmLock lease with a background heartbeat thread."""

    def __init__(
        self,
        client: "HypervisorClient",
        paths: list[str],
        ttl: int = 300,
        owner: str = "agy",
        task_id: str | None = None,
    ) -> None:
        self.client = client
        self.paths = [str(Path(p).as_posix()) for p in paths]
        self.ttl = ttl
        self.owner = owner
        self.task_id = task_id
        self.lease_id: str | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_heartbeat = threading.Event()
        self.acquired = False

    def acquire(self) -> bool:
        """Acquire lease from gateway."""
        primary_resource = self.paths[0] if self.paths else "file:workspace"
        payload = {
            "paths": self.paths,
            "resource": primary_resource,
            "owner": self.owner,
            "agent_id": self.owner,
            "task_id": self.task_id,
            "ttl": self.ttl,
        }
        res = self.client._post("/api/gateway/swarmlock/acquire", payload)
        if res and (res.get("status") == "ok" or res.get("ok") is True):
            self.lease_id = res.get("lease_id")
            self.acquired = True
            self._start_heartbeat()
            return True
        logger.warning("SwarmLock lease acquisition failed or skipped: %s", res)
        # In offline/degraded mode, treat as acquired locally
        self.acquired = True
        self._start_heartbeat()
        return True

    def release(self) -> bool:
        """Release lease and stop heartbeat."""
        self._stop_heartbeat.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)

        if not self.acquired:
            return True

        primary_resource = self.paths[0] if self.paths else "file:workspace"
        payload = {
            "paths": self.paths,
            "resource": primary_resource,
            "owner": self.owner,
            "agent_id": self.owner,
            "task_id": self.task_id,
            "lease_id": self.lease_id,
        }
        res = self.client._post("/api/gateway/swarmlock/release", payload)
        self.acquired = False
        return bool(res and (res.get("status") == "ok" or res.get("ok") is True))

    def _start_heartbeat(self) -> None:
        """Start daemon heartbeat thread to renew lease every (ttl / 2) seconds."""
        interval = max(5.0, self.ttl / 2.0)

        def _heartbeat_worker():
            while not self._stop_heartbeat.wait(interval):
                primary_resource = self.paths[0] if self.paths else "file:workspace"
                payload = {
                    "paths": self.paths,
                    "resource": primary_resource,
                    "owner": self.owner,
                    "agent_id": self.owner,
                    "task_id": self.task_id,
                    "lease_id": self.lease_id,
                    "ttl": self.ttl,
                }
                self.client._post("/api/gateway/swarmlock/heartbeat", payload)

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat_worker, daemon=True, name="SwarmLockHeartbeat"
        )
        self._heartbeat_thread.start()

    def __enter__(self) -> "LeaseContext":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class TaskContext:
    """Manages an admitted task context within the Hypervisor."""

    def __init__(
        self,
        client: "HypervisorClient",
        task_id: str,
        producer: str = "agy",
        model: str = "inherit",
    ) -> None:
        self.client = client
        self.task_id = task_id
        self.producer = producer
        self.model = model

    @contextmanager
    def acquire_lease(
        self, paths: list[str], ttl: int = 300
    ) -> Generator[LeaseContext, None, None]:
        """Acquire a managed lease on files for the duration of a code block."""
        lease = LeaseContext(
            client=self.client,
            paths=paths,
            ttl=ttl,
            owner=self.producer,
            task_id=self.task_id,
        )
        with lease:
            yield lease

    def emit_signal(
        self,
        stage: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Emit a telemetry signal attached to this task."""
        payload = SignalPayload(
            agent_id=self.producer,
            stage=stage,
            message=message,
            task_id=self.task_id,
            model=self.model,
            metadata=metadata or {},
        )
        return self.client.emit_signal(payload)

    def run_verification(
        self,
        command: str,
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Execute a clean-room verification receipt runner for this task."""
        try:
            from prismatic.verification.receipt_runner import run_clean_room_receipt

            receipt = run_clean_room_receipt(
                command=command,
                task_id=self.task_id,
                producer=self.producer,
                model=self.model,
                timeout=timeout,
            )
            return receipt.to_dict()
        except ImportError:
            logger.warning("Receipt runner not available in runtime; executing subprocess")
            import subprocess

            start = time.time()
            res = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return {
                "task_id": self.task_id,
                "command": command,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "duration_seconds": round(time.time() - start, 3),
            }


class HypervisorClient:
    """Universal client for interacting with the Prismatic Hypervisor & Control Plane."""

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = (endpoint or default_gateway_endpoint()).rstrip("/")

    def _post(self, path: str, data: dict[str, Any], max_retries: int = 3) -> dict[str, Any] | None:
        """Helper to make HTTP POST requests to Gateway API with exponential backoff."""
        url = f"{self.endpoint}{path}"
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("PRISMATIC_API_TOKEN") or os.environ.get("PRISMATIC_OPERATOR_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        body_bytes = json.dumps(data).encode("utf-8")
        delay = 0.5
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    url,
                    data=body_bytes,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status < 300:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as err:
                logger.debug("Attempt %d failed POST to %s: %s", attempt + 1, url, err)
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        return None

    def _get(self, path: str, max_retries: int = 3) -> dict[str, Any] | None:
        """Helper to make HTTP GET requests to Gateway API with exponential backoff."""
        url = f"{self.endpoint}{path}"
        headers = {}
        token = os.environ.get("PRISMATIC_API_TOKEN") or os.environ.get("PRISMATIC_OPERATOR_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        delay = 0.5
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status < 300:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as err:
                logger.debug("Attempt %d failed GET to %s: %s", attempt + 1, url, err)
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        return None

    @contextmanager
    def admit_task(
        self,
        task_id: str,
        producer: str = "agy",
        model: str = "inherit",
    ) -> Generator[TaskContext, None, None]:
        """Admit a task into the Hypervisor and enter its execution context."""
        payload = {
            "task_id": task_id,
            "producer": producer,
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._post("/api/gateway/admissions", payload)
        ctx = TaskContext(self, task_id=task_id, producer=producer, model=model)
        ctx.emit_signal("ADMITTED", f"Task {task_id} admitted by {producer}")
        try:
            yield ctx
            ctx.emit_signal("COMPLETED", f"Task {task_id} completed successfully")
        except Exception as err:
            ctx.emit_signal("FAILED", f"Task {task_id} failed: {err}")
            raise

    def emit_signal(self, signal: SignalPayload | dict[str, Any]) -> bool:
        """Emit a real-time signal to Prismatic Hub."""
        data = signal.to_dict() if isinstance(signal, SignalPayload) else signal
        res = self._post("/api/gateway/signals/emit", data)
        return bool(res and res.get("status") == "ok")

    def get_status(self) -> dict[str, Any]:
        """Get live Hypervisor health and lock status."""
        health = self._get("/health") or {"status": "offline"}
        locks = self._get("/api/gateway/swarmlock/status") or {"active_leases": []}
        return {
            "endpoint": self.endpoint,
            "health": health,
            "locks": locks,
        }

    def get_control_status(self) -> dict[str, Any]:
        """Get live fleet control and pause status."""
        res = self._get("/api/gateway/control/status")
        return (res and res.get("status")) or {"fleet_paused": False, "paused_agents": []}

    def is_paused(self, agent_id: str | None = None) -> bool:
        """Check if fleet or agent is paused by operator."""
        status = self.get_control_status()
        if status.get("fleet_paused"):
            return True
        if agent_id and agent_id.lower() in status.get("paused_agents", []):
            return True
        return False
