"""
SwarmLockManager — workspace-scoped concurrency mutexes.

Wraps the file-backed lock primitives from ``prismatic.lock`` with
workspace-aware semantics: each workspace gets its own lock-file
namespace so agents operating in different sandboxes never contend.

Usage::

    from prismatic.core.locking import SwarmLockManager

    mgr = SwarmLockManager(state_dir="/var/lib/prismatic/locks")
    if mgr.acquire("sandbox-ned-GRO-1234", timeout_s=30.0):
        try:
            # ... mutate files ...
        finally:
            mgr.release("sandbox-ned-GRO-1234")
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from prismatic.lock import (
    _read_locks,
    _write_locks,
    _prune_stale,
    STALE_TTL_MS,
)

logger = logging.getLogger("prismatic.core.locking")


class SwarmLockManager:
    """
    Workspace-scoped concurrency mutex backed by ``prismatic.lock``.

    Each workspace gets a dedicated lock-file registry under
    ``<state_dir>/workspace_locks.json``.  This keeps sandbox locking
    isolated from the global file-locking namespace used by
    ``prismatic-lock`` CLI.

    Thread-safe (uses ``threading.RLock`` for re-entrant acquisition).
    """

    def __init__(self, state_dir: str) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._lock_file_path = self._state_dir / "workspace_locks.json"

    # ── Public API ─────────────────────────────────────────────────

    def acquire(self, workspace_id: str, timeout_s: float = 30.0) -> bool:
        """
        Acquire the mutex for *workspace_id*.

        Blocks up to *timeout_s* seconds, polling every 100ms, until
        the lock becomes available or the timeout expires.

        Returns True if the lock was acquired, False on timeout.
        """
        deadline = time.monotonic() + timeout_s

        while True:
            with self._lock:
                acquired = self._try_acquire(workspace_id)
                if acquired:
                    return True

            # Check timeout
            if time.monotonic() >= deadline:
                logger.warning(
                    "SwarmLockManager: timeout acquiring lock for %s after %.1fs",
                    workspace_id, timeout_s,
                )
                return False

            # Back off before retrying
            time.sleep(0.1)

    def release(self, workspace_id: str) -> None:
        """
        Release the mutex for *workspace_id*.

        No-op if the workspace is not currently locked.
        """
        with self._lock:
            locks = self._load_locks()
            locks, pruned = _prune_stale(locks)
            if pruned:
                logger.debug("Pruned %d stale workspace lock(s).", pruned)

            # Find and remove this workspace's lock
            kept = []
            released = False
            for lock in locks:
                if lock.get("workspaceId") == workspace_id:
                    logger.debug(
                        "Released lock for workspace %s (held for %.1fs).",
                        workspace_id,
                        (time.time() * 1000 - lock.get("timestamp", 0)) / 1000,
                    )
                    released = True
                else:
                    kept.append(lock)

            if not released:
                logger.debug(
                    "SwarmLockManager.release: workspace %s was not locked.",
                    workspace_id,
                )

            self._save_locks(kept)

    def heartbeat(self, workspace_id: str) -> bool:
        """
        Refresh the heartbeat on *workspace_id*'s lock.

        Returns True if the workspace was found and refreshed.
        """
        with self._lock:
            locks = self._load_locks()
            locks, _ = _prune_stale(locks)

            now_ms = int(time.time() * 1000)
            for lock in locks:
                if lock.get("workspaceId") == workspace_id:
                    lock["lastHeartbeat"] = now_ms
                    self._save_locks(locks)
                    return True

            logger.debug(
                "SwarmLockManager.heartbeat: workspace %s not found.",
                workspace_id,
            )
            return False

    def is_locked(self, workspace_id: str) -> bool:
        """Check if *workspace_id* currently holds a lock."""
        with self._lock:
            locks = self._load_locks()
            locks, _ = _prune_stale(locks)
            for lock in locks:
                if lock.get("workspaceId") == workspace_id:
                    return True
            return False

    def get_lock_holder(self, workspace_id: str) -> str | None:
        """
        Return the agent ID holding the lock on *workspace_id*,
        or None if it is unlocked.
        """
        with self._lock:
            locks = self._load_locks()
            locks, _ = _prune_stale(locks)
            for lock in locks:
                if lock.get("workspaceId") == workspace_id:
                    return lock.get("agentId")
            return None

    # ── Internal helpers ──────────────────────────────────────────

    def _try_acquire(self, workspace_id: str) -> bool:
        """
        Attempt to acquire the lock for *workspace_id*.

        Must be called under ``self._lock``.

        Returns True if acquired, False if already held by another agent.
        """
        locks = self._load_locks()
        locks, pruned = _prune_stale(locks)
        if pruned:
            logger.debug("Pruned %d stale workspace lock(s) on acquire.", pruned)

        # Check if workspace is already locked
        for lock in locks:
            if lock.get("workspaceId") == workspace_id:
                # Already held — heartbeat refresh counts as re-acquire
                now_ms = int(time.time() * 1000)
                lock["lastHeartbeat"] = now_ms
                self._save_locks(locks)
                return True

        # Acquire new lock
        now_ms = int(time.time() * 1000)
        locks.append({
            "workspaceId": workspace_id,
            "agentId": os.environ.get("PRISMATIC_AGENT", "unknown"),
            "timestamp": now_ms,
            "lastHeartbeat": now_ms,
        })
        self._save_locks(locks)
        logger.debug("Lock acquired for workspace %s.", workspace_id)
        return True

    def _load_locks(self) -> list[dict]:
        """Load locks from the workspace lock file."""
        if not self._lock_file_path.exists():
            return []
        try:
            with open(self._lock_file_path) as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                # Support legacy dict-wrapper format
                if isinstance(data, dict):
                    return data.get("locks", [])
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load workspace locks: %s", exc)
        return []

    def _save_locks(self, locks: list[dict]) -> None:
        """Atomically persist the lock list."""
        tmp = self._lock_file_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(locks, f, indent=2)
        os.replace(tmp, self._lock_file_path)
