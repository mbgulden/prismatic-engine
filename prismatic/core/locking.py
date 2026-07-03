"""
SwarmLockManager — workspace concurrency mutexes.

Ensures that at most one agent writes to a given workspace at a time.
Acquires a file-backed (or Redis-backed) mutex before any file-mutating
operation and releases it on completion.

Wraps the primitives in ``prismatic/lock.py`` with workspace-aware
locking semantics.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prismatic.core.locking")

# Default values if not provided
PRISMATIC_HOME = os.environ.get("PRISMATIC_HOME", "/home/ubuntu")
DEFAULT_LOCK_FILE = Path(PRISMATIC_HOME) / ".antigravity" / "swarm_locks.json"
DEFAULT_STALE_TTL_MS = 300_000  # 5 minutes


class SwarmLockManager:
    """
    Workspace-scoped concurrency mutex.

    Manages locks for workspaces and individual files to ensure safe
    multi-agent collaboration.
    """

    def __init__(self, lock_file: Optional[str | Path] = None, stale_ttl_ms: int = DEFAULT_STALE_TTL_MS) -> None:
        self._lock_file_path = Path(lock_file) if lock_file else DEFAULT_LOCK_FILE
        self._lock_mutex_path = self._lock_file_path.with_suffix(".lock")
        self._stale_ttl_ms = stale_ttl_ms

        # Ensure parent directory exists
        self._lock_file_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock_registry(self):
        """Lock the mutex file for thread-safe access to the registry."""
        self._lock_mutex_path.touch(exist_ok=True)
        with open(self._lock_mutex_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _read_locks(self) -> List[Dict[str, Any]]:
        if not self._lock_file_path.exists():
            return []
        try:
            with open(self._lock_file_path, "r") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_locks(self, locks: List[Dict[str, Any]]) -> None:
        tmp = self._lock_file_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(locks, f, indent=2)
        os.replace(tmp, self._lock_file_path)

    def _prune_stale(self, locks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now_ms = int(time.time() * 1000)
        kept = []
        for lock in locks:
            last_hb = lock.get("lastHeartbeat", lock.get("timestamp", 0))
            if now_ms - last_hb <= self._stale_ttl_ms:
                kept.append(lock)
            else:
                logger.info(f"Pruned stale lock: {lock.get('filePath')} held by {lock.get('agentId')}")
        return kept

    def acquire(self, resource_id: str, agent_id: str, timeout_s: float = 30.0) -> bool:
        """
        Acquire a lock for a resource (workspace or file).

        Args:
            resource_id: Identifier for the resource (e.g. workspace_id or relative path).
            agent_id: Identifier for the agent requesting the lock.
            timeout_s: Maximum time to wait for the lock.

        Returns:
            True if acquired, False otherwise.
        """
        start_time = time.time()
        while True:
            with self._lock_registry():
                locks = self._read_locks()
                locks = self._prune_stale(locks)

                # Check if resource is already locked
                existing_lock = next((l for l in locks if l["filePath"] == resource_id), None)

                if existing_lock:
                    if existing_lock["agentId"] == agent_id:
                        # Refresh heartbeat
                        existing_lock["lastHeartbeat"] = int(time.time() * 1000)
                        self._write_locks(locks)
                        return True
                    else:
                        # Locked by someone else
                        if time.time() - start_time > timeout_s:
                            logger.warning(f"Timeout acquiring lock for {resource_id} (held by {existing_lock['agentId']})")
                            return False
                else:
                    # Resource is free
                    now_ms = int(time.time() * 1000)
                    locks.append({
                        "filePath": resource_id,
                        "agentId": agent_id,
                        "timestamp": now_ms,
                        "lastHeartbeat": now_ms
                    })
                    self._write_locks(locks)
                    logger.info(f"Acquired lock: {resource_id} -> {agent_id}")
                    return True

            time.sleep(1.0)

    def release(self, resource_id: str, agent_id: str) -> bool:
        """
        Release a lock.

        Returns:
            True if released, False if not held by this agent.
        """
        with self._lock_registry():
            locks = self._read_locks()
            locks = self._prune_stale(locks)

            new_locks = [l for l in locks if not (l["filePath"] == resource_id and l["agentId"] == agent_id)]

            if len(new_locks) < len(locks):
                self._write_locks(new_locks)
                logger.info(f"Released lock: {resource_id} (held by {agent_id})")
                return True
            else:
                logger.warning(f"Attempted to release lock not held by {agent_id}: {resource_id}")
                return False

    def heartbeat(self, resource_id: str, agent_id: str) -> bool:
        """Refresh heartbeat for an active lock."""
        with self._lock_registry():
            locks = self._read_locks()
            locks = self._prune_stale(locks)

            for lock in locks:
                if lock["filePath"] == resource_id and lock["agentId"] == agent_id:
                    lock["lastHeartbeat"] = int(time.time() * 1000)
                    self._write_locks(locks)
                    return True
            return False

    def get_status(self) -> List[Dict[str, Any]]:
        """Returns all active (non-stale) locks."""
        with self._lock_registry():
            locks = self._read_locks()
            return self._prune_stale(locks)
