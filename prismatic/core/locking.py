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
DEFAULT_AUDIT_LOG_DIR = Path(PRISMATIC_HOME) / ".antigravity" / "audit"
DEFAULT_AUDIT_LOG_FILE = DEFAULT_AUDIT_LOG_DIR / "swarmlock_audit.jsonl"
DEFAULT_STALE_TTL_MS = 3_600_000  # 5 minutes

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

    # In-memory tracking for contention/deflection metrics and event audit history
    _contentions_by_resource: Dict[str, List[Dict[str, Any]]] = {}
    _total_deflected_collisions: int = 0
    _event_history: List[Dict[str, Any]] = []
    _active_lease_start_times: Dict[str, float] = {}

    def __init__(
        self,
        lock_file: Optional[str | Path] = None,
        stale_ttl_ms: int = DEFAULT_STALE_TTL_MS,
    ) -> None:
        self._lock_file_path = Path(lock_file) if lock_file else DEFAULT_LOCK_FILE
        self._stale_ttl_ms = stale_ttl_ms
        self._lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)

        # Normalize registry file to dict format if it was previously an empty or legacy list
        if self._lock_file_path.exists():
            try:
                import json
                content = self._lock_file_path.read_text(encoding="utf-8")
                if content.strip():
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        dict_data = {}
                        for item in parsed:
                            if isinstance(item, dict):
                                res = item.get("resource") or item.get("filePath")
                                if res:
                                    dict_data[res] = item
                        self._lock_file_path.write_text(json.dumps(dict_data, indent=2), encoding="utf-8")
            except Exception:
                pass

        # Delegate storage and lock mechanics to Swarmlock FileBackend
        self._sw = SyncSwarmlock(backend="file", registry_file=str(self._lock_file_path))

    @classmethod
    def _record_audit_event(cls, event_type: str, resource: str, agent_id: str, **kwargs: Any) -> None:
        """Record an immutable lifecycle audit event into in-memory ring buffer and persistent JSONL."""
        import json
        import uuid
        from datetime import datetime, timezone

        now = time.time()
        iso_ts = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        record = {
            "id": f"evt-{int(now * 1000)}-{str(uuid.uuid4())[:6]}",
            "timestamp": now,
            "iso_timestamp": iso_ts,
            "event_type": event_type,
            "resource": resource,
            "agent_id": agent_id,
            **kwargs,
        }
        cls._event_history.append(record)
        if len(cls._event_history) > 200:
            cls._event_history = cls._event_history[-100:]

        # Append to standardized persistent JSONL audit stream
        try:
            DEFAULT_AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(DEFAULT_AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Failed to append to SwarmLock audit log: {e}")

    @classmethod
    def get_audit_file_info(cls) -> Dict[str, Any]:
        """Return standardized relative and absolute paths for Workspaces tab deep linking."""
        rel_path = ".antigravity/audit/swarmlock_audit.jsonl"
        abs_path = str(DEFAULT_AUDIT_LOG_FILE)
        size = DEFAULT_AUDIT_LOG_FILE.stat().st_size if DEFAULT_AUDIT_LOG_FILE.exists() else 0
        return {
            "ok": True,
            "relative_path": rel_path,
            "absolute_path": abs_path,
            "exists": DEFAULT_AUDIT_LOG_FILE.exists(),
            "size_bytes": size,
            "total_events": len(cls._event_history),
            "workspace_deep_link": f"/dashboard?file={rel_path}#workspaces",
        }

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
                SwarmLockManager._active_lease_start_times[resource_id] = start_time
                SwarmLockManager._record_audit_event(
                    "acquired",
                    resource_id,
                    agent_id,
                    lease_id=lease.lease_id,
                    intention=meta.get("intention", "EXCLUSIVE_MUTATION"),
                    task_id=meta.get("task_id", ""),
                    model=meta.get("model", ""),
                )
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
                SwarmLockManager._record_audit_event(
                    "deflected",
                    resource_id,
                    agent_id,
                    holder=err.holder,
                    deflected_total=SwarmLockManager._total_deflected_collisions,
                    intention=meta.get("intention", "EXCLUSIVE_MUTATION"),
                    task_id=meta.get("task_id", ""),
                )
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
                start_ts = SwarmLockManager._active_lease_start_times.pop(resource_id, time.time())
                duration = max(0.0, time.time() - start_ts)
                logger.info(f"Released lock: {resource_id} (held by {agent_id}) after {duration:.2f}s")
                SwarmLockManager._record_audit_event(
                    "released",
                    resource_id,
                    agent_id,
                    lease_id=lease.lease_id,
                    duration_seconds=round(duration, 2),
                )
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
            SwarmLockManager._record_audit_event(
                "renewed",
                resource_id,
                agent_id,
                lease_id=lease.lease_id,
            )
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
                holder = evicted_lease.get("holder", "unknown")
                start_ts = SwarmLockManager._active_lease_start_times.pop(resource_id, time.time())
                duration = max(0.0, time.time() - start_ts)
                SwarmLockManager._record_audit_event(
                    "evicted",
                    resource_id,
                    holder,
                    reason=reason,
                    lease_id=evicted_lease.get("lease_id", ""),
                    duration_seconds=round(duration, 2),
                )
                _emit_lock_event("expire", resource_id, holder, reason=reason)
                SwarmLockManager._contentions_by_resource.pop(resource_id, None)
                logger.info(f"Evicted lock on {resource_id}: {reason}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error evicting lock for {resource_id}: {e}")
            return False

    def get_history(self, limit: int = 50, resource: Optional[str] = None, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return rolling list of lease lifecycle events, combining in-memory buffer with persistent audit log."""
        import json
        events_dict: Dict[str, Dict[str, Any]] = {
            e.get("id", str(idx)): e for idx, e in enumerate(SwarmLockManager._event_history)
        }
        
        candidates = [
            DEFAULT_AUDIT_LOG_FILE,
            Path(os.path.expanduser("~/.antigravity/audit/swarmlock_audit.jsonl")),
            Path(os.path.expanduser("~/.prismatic/.antigravity/audit/swarmlock_audit.jsonl")),
        ]
        for candidate in candidates:
            if candidate.exists():
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                record = json.loads(line)
                                if record.get("id"):
                                    events_dict[record["id"]] = record
                            except Exception:
                                continue
                except Exception as e:
                    logger.warning(f"Failed to read SwarmLock audit log {candidate}: {e}")

        events = list(events_dict.values())
        if resource:
            events = [e for e in events if resource in e.get("resource", "")]
        if agent_id:
            events = [e for e in events if e.get("agent_id") == agent_id]
        events.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return events[:limit]

    def get_config(self) -> Dict[str, Any]:
        """Return current lock governance configuration parameters."""
        return {
            "lock_file": str(self._lock_file_path),
            "stale_ttl_ms": self._stale_ttl_ms,
            "stale_ttl_seconds": self._stale_ttl_ms / 1000.0,
            "backend": "file (SyncSwarmlock v0.2.0)",
            "heartbeat_interval_seconds": 15,
            "total_deflected_collisions": SwarmLockManager._total_deflected_collisions,
        }

    def update_config(self, stale_ttl_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Update runtime lock configuration parameters."""
        if stale_ttl_seconds and stale_ttl_seconds >= 5.0:
            self._stale_ttl_ms = int(stale_ttl_seconds * 1000)
        return self.get_config()

    def get_status(self) -> List[Dict[str, Any]]:
        """Returns all active (non-stale) locks in JSON-compatible format."""
        status_list = []
        try:
            raw_data = self._sw.async_client.backend._read_data()
            raw_data = self._sw.async_client.backend._prune(raw_data)
            now = time.time()
            if isinstance(raw_data, list):
                iter_items = [(e.get("resource") or e.get("filePath", f"res_{i}"), e) for i, e in enumerate(raw_data) if isinstance(e, dict)]
            elif isinstance(raw_data, dict):
                iter_items = list(raw_data.items())
            else:
                iter_items = []

            for res, entry in iter_items:
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
            if isinstance(raw_data, list):
                iter_items = [(e.get("resource") or e.get("filePath", f"res_{i}"), e) for i, e in enumerate(raw_data) if isinstance(e, dict)]
            elif isinstance(raw_data, dict):
                iter_items = list(raw_data.items())
            else:
                iter_items = []

            for res, entry in iter_items:
                expires_at = entry.get("expires_at", 0)
                if now < expires_at:
                    holder = entry.get("holder", "unknown")
                    active_agents.add(holder)
                    meta = entry.get("metadata") or {}
                    contentions = SwarmLockManager._contentions_by_resource.get(res, [])

                    # Detect workspace from resource path prefix
                    workspace_name = "prismatic-engine"
                    if "/" in res:
                        prefix = res.split("/")[0]
                        if prefix in ["guest_hermes_setup", "scratch"]:
                            workspace_name = "Hermes"
                        elif prefix in ["swarmlock"]:
                            workspace_name = "swarmlock"
                        elif prefix in ["hd-platform", "api"]:
                            workspace_name = "hd-platform"

                    locks.append({
                        "resource": res,
                        "workspace": meta.get("workspace", workspace_name),
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
