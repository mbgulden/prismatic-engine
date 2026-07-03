"""
DistributedComputeGovernor — cross-process agent allocation management.

Ensures that agent capacity (max_concurrent) is respected across multiple
Prismatic Engine instances by maintaining a shared status registry at
$PRISMATIC_HOME/.antigravity/agent_status.json.
"""

from __future__ import annotations

import json
import os
import time
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Generator

class DistributedComputeGovernor:
    """
    Manages agent allocation lifecycle: acquire, heartbeat, release.
    """

    def __init__(self, status_path: str | None = None) -> None:
        if status_path is None:
            home = os.environ.get("PRISMATIC_HOME", os.path.expanduser("~"))
            status_path = os.path.join(home, ".antigravity", "agent_status.json")
        self.status_path = Path(status_path)
        self.lock_path = self.status_path.with_suffix(".lock")
        self._ensure_status_dir()

    def _ensure_status_dir(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _lock_and_load(self) -> Generator[Dict[str, Any], None, None]:
        """Atomic lock context manager for shared state registry."""
        start_time = time.time()
        locked = False
        while time.time() - start_time < 10.0:
            try:
                # O_EXCL ensures atomic creation
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(str(os.getpid()))
                locked = True
                break
            except FileExistsError:
                # Check if lock is stale (e.g. process died)
                try:
                    mtime = os.path.getmtime(self.lock_path)
                    if time.time() - mtime > 15.0:
                        os.remove(self.lock_path)
                except OSError:
                    pass
                time.sleep(0.1)

        if not locked:
            raise RuntimeError("Could not acquire lock for agent_status.json")

        try:
            status = {"agents": {}}
            if self.status_path.exists():
                try:
                    status = json.loads(self.status_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass

            yield status

            # Save back the status
            self.status_path.write_text(json.dumps(status, indent=2))

        finally:
            if locked:
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass

    def acquire(
        self,
        agent_name: str,
        task_id: str,
        instance_id: str,
        max_concurrent: int = 1,
        capability: str = "general",
    ) -> bool:
        """
        Attempt to acquire an allocation for *agent_name*.

        Returns True if successful, False if at capacity.
        """
        try:
            with self._lock_and_load() as status:
                agents = status.setdefault("agents", {})
                agent_data = agents.setdefault(agent_name, {
                    "status": "idle",
                    "max_concurrent": max_concurrent,
                    "active_runs": []
                })

                # Update max_concurrent if it changed in config
                agent_data["max_concurrent"] = max_concurrent
                active_runs = agent_data.setdefault("active_runs", [])

                # Check if this task already has an allocation
                for run in active_runs:
                    if run["task_id"] == task_id:
                        return True

                if len(active_runs) >= max_concurrent:
                    return False

                # Allocate
                new_run = {
                    "task_id": task_id,
                    "instance_id": instance_id,
                    "capability": capability,
                    "pid": None,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "heartbeat": datetime.now(timezone.utc).isoformat(),
                }
                active_runs.append(new_run)
                agent_data["status"] = "busy"
                return True
        except RuntimeError:
            return False

    def update_pid(self, agent_name: str, task_id: str, pid: int) -> None:
        """Update the PID for an active allocation."""
        with self._lock_and_load() as status:
            active_runs = status.get("agents", {}).get(agent_name, {}).get("active_runs", [])
            for run in active_runs:
                if run["task_id"] == task_id:
                    run["pid"] = pid
                    break

    def heartbeat(self, agent_name: str, task_id: str) -> None:
        """Update the heartbeat timestamp for an active allocation."""
        with self._lock_and_load() as status:
            active_runs = status.get("agents", {}).get(agent_name, {}).get("active_runs", [])
            for run in active_runs:
                if run["task_id"] == task_id:
                    run["heartbeat"] = datetime.now(timezone.utc).isoformat()
                    break

    def release(self, agent_name: str, task_id: str) -> None:
        """Release an allocation."""
        with self._lock_and_load() as status:
            agent_data = status.get("agents", {}).get(agent_name)
            if not agent_data:
                return

            active_runs = agent_data.get("active_runs", [])
            new_runs = [r for r in active_runs if r["task_id"] != task_id]

            if len(new_runs) != len(active_runs):
                agent_data["active_runs"] = new_runs
                if not new_runs:
                    agent_data["status"] = "idle"

    def release_by_pid(self, pid: int) -> None:
        """Release an allocation by PID."""
        with self._lock_and_load() as status:
            for agent_name, agent_data in status.get("agents", {}).items():
                active_runs = agent_data.get("active_runs", [])
                new_runs = [r for r in active_runs if r.get("pid") != pid]
                if len(new_runs) != len(active_runs):
                    agent_data["active_runs"] = new_runs
                    if not new_runs:
                        agent_data["status"] = "idle"

    def prune_stale(self, ttl_seconds: int = 300) -> int:
        """Remove allocations that have timed out."""
        count = 0
        now = datetime.now(timezone.utc)

        with self._lock_and_load() as status:
            for agent_name, agent_data in status.get("agents", {}).items():
                active_runs = agent_data.get("active_runs", [])
                new_runs = []
                for run in active_runs:
                    hb_str = run.get("heartbeat") or run.get("started_at")
                    try:
                        hb_time = datetime.fromisoformat(hb_str)
                        if (now - hb_time).total_seconds() > ttl_seconds:
                            count += 1
                            continue
                    except ValueError:
                        count += 1
                        continue
                    new_runs.append(run)

                if len(new_runs) != len(active_runs):
                    agent_data["active_runs"] = new_runs
                    if not new_runs:
                        agent_data["status"] = "idle"

        return count
