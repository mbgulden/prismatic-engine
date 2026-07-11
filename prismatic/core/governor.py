"""Distributed compute allocation governor.

The governor stores active agent allocations in a shared JSON registry so
multiple dispatcher processes do not over-schedule the same scarce agent
backend. It is intentionally small and stdlib-only: the dispatcher can use it
without adding a service dependency.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DistributedComputeGovernor:
    """Cross-process agent allocation lifecycle manager.

    Allocations are persisted under ``$PRISMATIC_HOME/.antigravity`` by default.
    A sidecar ``.lock`` file created with ``O_EXCL`` serializes writers across
    dispatcher processes. Stale lock files are removed after ``lock_ttl_seconds``.
    """

    def __init__(
        self,
        status_path: str | os.PathLike[str] | None = None,
        *,
        lock_timeout_seconds: float = 10.0,
        lock_ttl_seconds: float = 15.0,
    ) -> None:
        if status_path is None:
            home = Path(os.environ.get("PRISMATIC_HOME", "~")).expanduser()
            status_path = home / ".antigravity" / "agent_status.json"
        self.status_path = Path(status_path)
        self.lock_path = self.status_path.with_suffix(self.status_path.suffix + ".lock")
        self.lock_timeout_seconds = lock_timeout_seconds
        self.lock_ttl_seconds = lock_ttl_seconds
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _lock_and_load(self) -> Generator[dict[str, Any], None, None]:
        """Acquire the registry lock, load state, then atomically persist it."""
        start = time.monotonic()
        locked = False
        while time.monotonic() - start < self.lock_timeout_seconds:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as lock_file:
                    lock_file.write(str(os.getpid()))
                locked = True
                break
            except FileExistsError:
                try:
                    if time.time() - self.lock_path.stat().st_mtime > self.lock_ttl_seconds:
                        self.lock_path.unlink()
                except FileNotFoundError:
                    pass
                time.sleep(0.05)

        if not locked:
            raise RuntimeError(f"Could not acquire compute governor lock: {self.lock_path}")

        try:
            status: dict[str, Any] = {"agents": {}}
            if self.status_path.exists():
                try:
                    loaded = json.loads(self.status_path.read_text())
                    if isinstance(loaded, dict):
                        status = loaded
                except (json.JSONDecodeError, OSError):
                    status = {"agents": {}}

            status.setdefault("agents", {})
            yield status

            tmp_path = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(status, indent=2, sort_keys=True))
            os.replace(tmp_path, self.status_path)
        finally:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def acquire(
        self,
        agent_name: str,
        task_id: str,
        instance_id: str,
        *,
        max_concurrent: int = 1,
        capability: str = "general",
        allow_existing: bool = False,
    ) -> bool:
        """Try to allocate one execution slot for ``agent_name``.

        Returns ``True`` only when a launcher may proceed. By default an
        existing allocation for the same ``task_id`` returns ``False`` so
        retry paths cannot spawn duplicate subprocesses while counting as one
        active run. Callers that need idempotent heartbeat refresh can pass
        ``allow_existing=True`` explicitly.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

        with self._lock_and_load() as status:
            agents = status.setdefault("agents", {})
            agent = agents.setdefault(
                agent_name,
                {"status": "idle", "max_concurrent": max_concurrent, "active_runs": []},
            )
            agent["max_concurrent"] = max_concurrent
            active_runs = agent.setdefault("active_runs", [])

            for run in active_runs:
                if run.get("task_id") == task_id:
                    run["heartbeat"] = _utc_now()
                    run["instance_id"] = instance_id
                    return allow_existing

            if len(active_runs) >= max_concurrent:
                return False

            active_runs.append(
                {
                    "task_id": task_id,
                    "instance_id": instance_id,
                    "capability": capability,
                    "pid": None,
                    "started_at": _utc_now(),
                    "heartbeat": _utc_now(),
                }
            )
            agent["status"] = "busy"
            return True

    def update_pid(self, agent_name: str, task_id: str, pid: int) -> None:
        with self._lock_and_load() as status:
            for run in status.get("agents", {}).get(agent_name, {}).get("active_runs", []):
                if run.get("task_id") == task_id:
                    run["pid"] = pid
                    run["heartbeat"] = _utc_now()
                    break

    def heartbeat(self, agent_name: str, task_id: str) -> None:
        with self._lock_and_load() as status:
            for run in status.get("agents", {}).get(agent_name, {}).get("active_runs", []):
                if run.get("task_id") == task_id:
                    run["heartbeat"] = _utc_now()
                    break

    def release(self, agent_name: str, task_id: str) -> None:
        with self._lock_and_load() as status:
            agent = status.get("agents", {}).get(agent_name)
            if not agent:
                return
            runs = agent.get("active_runs", [])
            agent["active_runs"] = [run for run in runs if run.get("task_id") != task_id]
            if not agent["active_runs"]:
                agent["status"] = "idle"

    def release_by_pid(self, pid: int) -> int:
        """Release allocations by process ID and return number removed."""
        removed = 0
        with self._lock_and_load() as status:
            for agent in status.get("agents", {}).values():
                runs = agent.get("active_runs", [])
                kept = [run for run in runs if run.get("pid") != pid]
                removed += len(runs) - len(kept)
                agent["active_runs"] = kept
                if not kept:
                    agent["status"] = "idle"
        return removed

    def prune_stale(self, *, ttl_seconds: int = 300) -> int:
        """Remove allocations whose heartbeat is older than ``ttl_seconds``."""
        now = datetime.now(timezone.utc)
        pruned = 0
        with self._lock_and_load() as status:
            for agent in status.get("agents", {}).values():
                kept = []
                for run in agent.get("active_runs", []):
                    timestamp = run.get("heartbeat") or run.get("started_at")
                    try:
                        heartbeat = datetime.fromisoformat(timestamp)
                    except (TypeError, ValueError):
                        pruned += 1
                        continue
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                    if (now - heartbeat).total_seconds() > ttl_seconds:
                        pruned += 1
                        continue
                    kept.append(run)
                agent["active_runs"] = kept
                if not kept:
                    agent["status"] = "idle"
        return pruned
