"""
SwarmLockManager — workspace concurrency mutexes backed by Swarmlock v0.2.0.

Ensures that at most one agent writes to a given workspace at a time.
Acquires a file-backed or distributed mutex before any file-mutating
operation and releases it on completion.

Wraps the swarmlock production primitive while maintaining exact backward
compatibility for all callers across the Prismatic Engine governance layer.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from swarmlock import (
    AcquireRequest,
    LockConflictError,
    ReleaseRequest,
    RenewRequest,
    SyncSwarmlock,
)

logger = logging.getLogger("prismatic.core.locking")

# Default values if not provided
PRISMATIC_HOME = os.environ.get("PRISMATIC_HOME", "/home/ubuntu")
DEFAULT_LOCK_FILE = Path(PRISMATIC_HOME) / ".antigravity" / "swarm_locks.json"
DEFAULT_STALE_TTL_MS = 300_000  # 5 minutes

try:
    from prismatic.gateway.ipc_bridge import send_event_via_socket
    _HAS_IPC = True
except ImportError:
    _HAS_IPC = False


def _emit_lock_event(event_type: str, filepath: str, agent_id: str, **extra: Any) -> None:
    """Emit a lock lifecycle event to the IPC bridge."""
    if not _HAS_IPC:
        return
    try:
        send_event_via_socket(
            event_type=event_type,
            source=f"lock:{agent_id}",
            payload={"file": filepath, "agent": agent_id, **extra},
        )
    except Exception:
        pass


class SwarmLockManager:
    """
    Workspace-scoped concurrency mutex backed by Swarmlock v0.2.0.

    Manages locks for workspaces and individual files to ensure safe
    multi-agent collaboration. Works cross-platform on Windows and Linux.
    """

    # In-memory tracking for contention/deflection metrics across the process
    _contentions_by_resource: Dict[str, List[Dict[str, Any]]] = {}
    _total_deflected_collisions: int = 0

    def __init__(
        self,
        lock_file: Optional[str | Path] = None,
        stale_ttl_ms: int = DEFAULT_STALE_TTL_MS,
    ) -> None:
        self._lock_file_path = Path(lock_file) if lock_file else DEFAULT_LOCK_FILE
        self._stale_ttl_ms = stale_ttl_ms
        self._lock_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Delegate storage and lock mechanics to Swarmlock FileBackend
        self._sw = SyncSwarmlock(backend="file", registry_file=str(self._lock_file_path))

    def acquire(
        self,
        resource_id: str,
        agent_id: str,
        timeout_s: float = 30.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Acquire a lock for a resource (workspace or file).

        Args:
            resource_id: Identifier for the resource (e.g. workspace_id or relative path).
            agent_id: Identifier for the agent requesting the lock.
            timeout_s: Maximum time to wait for the lock.
            metadata: Optional rich metadata (e.g. intention, model, task_id, task_provider).

        Returns:
            True if acquired, False otherwise.
        """
        start_time = time.time()
        ttl_seconds = self._stale_ttl_ms / 1000.0
        meta = metadata or {}
        req = AcquireRequest(
            resource=resource_id,
            holder=agent_id,
            ttl_seconds=ttl_seconds,
            metadata=meta,
        )

        while True:
            try:
                lease = self._sw.acquire(req)
                logger.info(f"Acquired lock: {resource_id} -> {agent_id} (Lease ID: {lease.lease_id})")
                _emit_lock_event("lock", resource_id, agent_id, lease_id=lease.lease_id, metadata=meta)
                return True
            except LockConflictError as err:
                SwarmLockManager._total_deflected_collisions += 1
                contention_record = {
                    "agent_id": agent_id,
                    "attempted_at": time.time(),
                    "holder": err.holder,
                    "metadata": meta,
                }
                SwarmLockManager._contentions_by_resource.setdefault(resource_id, []).append(contention_record)
                _emit_lock_event(
                    "contention",
                    resource_id,
                    agent_id,
                    holder=err.holder,
                    deflected_total=SwarmLockManager._total_deflected_collisions,
                )

                if time.time() - start_time >= timeout_s:
                    logger.warning(f"Timeout acquiring lock for {resource_id} (held by {err.holder})")
                    return False
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Error acquiring lock for {resource_id}: {e}")
                return False

    def release(self, resource_id: str, agent_id: str) -> bool:
        """
        Release a lock.

        Returns:
            True if released, False if not held by this agent.
        """
        try:
            lease = self._sw.get_lease(resource_id)
            if lease is None or lease.holder != agent_id:
                logger.warning(f"Attempted to release lock not held by {agent_id}: {resource_id}")
                return False

            rel_req = ReleaseRequest(lease_id=lease.lease_id, resource=resource_id, holder=agent_id)
            success = self._sw.release(rel_req)
            if success:
                logger.info(f"Released lock: {resource_id} (held by {agent_id})")
                _emit_lock_event("unlock", resource_id, agent_id, lease_id=lease.lease_id)
                SwarmLockManager._contentions_by_resource.pop(resource_id, None)
            return success
        except Exception as e:
            logger.error(f"Error releasing lock for {resource_id}: {e}")
            return False

    def heartbeat(self, resource_id: str, agent_id: str) -> bool:
        """Refresh heartbeat for an active lock."""
        try:
            lease = self._sw.get_lease(resource_id)
            if lease is None or lease.holder != agent_id:
                return False

            renew_req = RenewRequest(
                lease_id=lease.lease_id,
                resource=resource_id,
                holder=agent_id,
                extend_seconds=self._stale_ttl_ms / 1000.0,
            )
            self._sw.renew(renew_req)
            _emit_lock_event("heartbeat", resource_id, agent_id, lease_id=lease.lease_id)
            return True
        except Exception:
            return False

    def evict(self, resource_id: str, reason: str = "operator_eviction") -> bool:
        """Force-evict a lock (Operator Action)."""
        try:
            raw_data = self._sw.async_client.backend._read_data()
            if resource_id in raw_data:
                evicted_lease = raw_data.pop(resource_id)
                self._sw.async_client.backend._write_data(raw_data)
                _emit_lock_event("expire", resource_id, evicted_lease.get("holder", "unknown"), reason=reason)
                SwarmLockManager._contentions_by_resource.pop(resource_id, None)
                logger.info(f"Evicted lock on {resource_id}: {reason}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error evicting lock for {resource_id}: {e}")
            return False

    def get_status(self) -> List[Dict[str, Any]]:
        """Returns all active (non-stale) locks in JSON-compatible format."""
        status_list = []
        try:
            raw_data = self._sw.async_client.backend._read_data()
            raw_data = self._sw.async_client.backend._prune(raw_data)
            now = time.time()
            for res, entry in raw_data.items():
                if now < entry.get("expires_at", 0):
                    status_list.append({
                        "filePath": res,
                        "agentId": entry.get("holder", "unknown"),
                        "timestamp": int(entry.get("created_at", now) * 1000),
                        "lastHeartbeat": int((entry.get("expires_at", now) - entry.get("ttl_seconds", 300)) * 1000),
                    })
        except Exception as e:
            logger.error(f"Error reading lock status: {e}")
        return status_list

    def get_enriched_status(self) -> Dict[str, Any]:
        """Returns full rich metadata for Swarm Lock visual control plane."""
        locks = []
        active_agents = set()
        now = time.time()

        try:
            raw_data = self._sw.async_client.backend._read_data()
            raw_data = self._sw.async_client.backend._prune(raw_data)
            for res, entry in raw_data.items():
                expires_at = entry.get("expires_at", 0)
                if now < expires_at:
                    holder = entry.get("holder", "unknown")
                    active_agents.add(holder)
                    meta = entry.get("metadata") or {}
                    contentions = SwarmLockManager._contentions_by_resource.get(res, [])

                    locks.append({
                        "resource": res,
                        "holder": holder,
                        "lease_id": entry.get("lease_id", ""),
                        "intention": meta.get("intention", "EXCLUSIVE_MUTATION"),
                        "model": meta.get("model", ""),
                        "task_id": meta.get("task_id", ""),
                        "task_provider": meta.get("task_provider", "linear" if "GRO-" in str(meta.get("task_id", "")) else "generic"),
                        "idempotency_key": entry.get("idempotency_key", ""),
                        "created_at": entry.get("created_at", now),
                        "expires_at": expires_at,
                        "remaining_seconds": max(0.0, expires_at - now),
                        "ttl_seconds": entry.get("ttl_seconds", self._stale_ttl_ms / 1000.0),
                        "is_expired": False,
                        "contentions": contentions,
                    })
        except Exception as e:
            logger.error(f"Error compiling enriched status: {e}")

        return {
            "ok": True,
            "timestamp": now,
            "active_lock_count": len(locks),
            "deflected_collisions": SwarmLockManager._total_deflected_collisions,
            "active_agents": sorted(list(active_agents)),
            "locks": locks,
        }
