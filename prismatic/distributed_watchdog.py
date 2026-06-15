#!/usr/bin/env python3
"""
prismatic/distributed_watchdog.py — Multi-Node Health Circuit (DistributedWatchdog)
===================================================================================

Extends the local watchdog (``scripts/watchdog.py``) to a multi-node health
circuit that monitors remote agent nodes, detects hung jobs, cancels orphaned
VRAM allocations, and re-routes work through auto-failover.

Key capabilities:
    1. **120s timeout circuit** — detect hung remote jobs and auto-failover
    2. **VRAM orphan detection** — cancel stale GPU allocations on dead nodes
    3. **4-consecutive-failure decommissioning** — mark nodes as offline after
       repeated health-check failures; fall back to degraded local execution
    4. **IPC bridge integration** — emit events to the gateway EventBus for
       dashboard visibility and downstream alerting

Integration:
    - Runs as a companion to ``scripts/watchdog.py`` (the local process watchdog).
    - Reads ``/tmp/prismatic/swarm_nodes.json`` for the known node roster.
    - Writes node health state to ``/tmp/prismatic/distributed_watchdog_state.json``.
    - Emits events via Unix socket IPC (same as the existing lock/watchdog pattern).
    - Optionally triggers signal providers to re-route stuck tasks.

Usage::

    python -m prismatic.distributed_watchdog  [--check-interval 30]

Environment:
    PRISMATIC_HOME            — root path (default: $HOME)
    PRISMATIC_STATE_DIR       — state directory (default: ./prismatic_state)
    PRISMATIC_IPC_SOCKET      — Unix socket for IPC events
    DISTRIBUTED_TIMEOUT_S     — job timeout in seconds (default: 120)
    DISTRIBUTED_MAX_FAILURES  — consecutive failures before decommission (default: 4)
"""

from __future__ import annotations

import json
import os
import signal
import socket as sock_mod
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════
# Constants (env-overridable)
# ═══════════════════════════════════════════════════════════════

PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", os.environ.get("HOME", ".")))
STATE_DIR = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"))

# Where the swarm node roster lives (written by the orchestrator)
NODE_ROSTER = Path(os.environ.get(
    "PRISMATIC_NODE_ROSTER",
    "/tmp/prismatic/swarm_nodes.json",
))

# Where this module persists node health state
HEALTH_STATE_PATH = Path(os.environ.get(
    "PRISMATIC_WATCHDOG_HEALTH_STATE",
    "/tmp/prismatic/distributed_watchdog_state.json",
))

# Where VRAM / GPU allocation markers live
VRAM_MARKER_DIR = Path(os.environ.get(
    "PRISMATIC_VRAM_MARKER_DIR",
    "/tmp/prismatic/vram",
))

# 120s timeout circuit
JOB_TIMEOUT_S = int(os.environ.get("DISTRIBUTED_TIMEOUT_S", "120"))

# 4-consecutive-failure decommission threshold
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("DISTRIBUTED_MAX_FAILURES", "4"))

# How often this watchdog runs health checks (seconds)
DEFAULT_CHECK_INTERVAL_S = 30


# ═══════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════


class NodeDecommissionError(Exception):
    """Raised when a node has exceeded MAX_CONSECUTIVE_FAILURES and is
    being decommissioned.  Work assigned to this node must be re-routed."""

    def __init__(self, node_id: str, failures: int):
        self.node_id = node_id
        self.failures = failures
        super().__init__(
            f"Node {node_id} decommissioned after {failures} consecutive failures"
        )


class TimeoutError(Exception):
    """Raised when a remote job exceeds JOB_TIMEOUT_S without a heartbeat."""

    def __init__(self, node_id: str, job_id: str, age_s: float):
        self.node_id = node_id
        self.job_id = job_id
        self.age_s = age_s
        super().__init__(
            f"Job {job_id} on {node_id} timed out at {age_s:.0f}s "
            f"(threshold {JOB_TIMEOUT_S}s)"
        )


# ═══════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class NodeHealth:
    """Health state for a single swarm node.

    Written to the health state JSON file after each check cycle so the
    orchestrator and dashboard can read it.
    """

    node_id: str
    hostname: str = ""
    last_heartbeat: float = 0.0          # Unix timestamp
    last_seen_alive: float = 0.0         # Unix timestamp
    consecutive_failures: int = 0
    is_decommissioned: bool = False
    decommissioned_at: float | None = None
    active_jobs: list[str] = field(default_factory=list)   # job_id list
    vram_allocated_mb: int = 0
    vram_total_mb: int = 0
    tags: dict[str, str] = field(default_factory=dict)    # "gpu_type", "arch", etc.

    @property
    def is_alive(self) -> bool:
        """A node is considered alive if it has sent a heartbeat within
        2x JOB_TIMEOUT_S of now."""
        if self.is_decommissioned:
            return False
        return (time.time() - self.last_heartbeat) < (JOB_TIMEOUT_S * 2)

    @property
    def healthy_for_failover(self) -> bool:
        """A node is a valid failover target if it is alive, not
        decommissioned, and has no active jobs."""
        return self.is_alive and not self.is_decommissioned and len(self.active_jobs) == 0


@dataclass
class JobRecord:
    """A tracked remote job with heartbeat tracking."""

    job_id: str
    node_id: str
    issue_id: str = ""
    started_at: float = 0.0
    last_heartbeat: float = 0.0
    status: str = "running"    # running | completed | failed | timed_out
    vram_reserved_mb: int = 0
    error_message: str = ""

    @property
    def age_s(self) -> float:
        return time.time() - self.started_at

    @property
    def idle_s(self) -> float:
        """Seconds since last heartbeat."""
        return time.time() - self.last_heartbeat


# ═══════════════════════════════════════════════════════════════
# Node Registry
# ═══════════════════════════════════════════════════════════════


class NodeRegistry:
    """In-memory + file-backed registry of swarm node health states.

    Persists to ``HEALTH_STATE_PATH`` so the orchestrator and dashboard
    can read the latest health snapshot without calling into this process.
    """

    def __init__(self, state_path: str | Path | None = None):
        self._state_path = Path(state_path) if state_path else HEALTH_STATE_PATH
        self._nodes: dict[str, NodeHealth] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._load()

    # ── Serialization ──────────────────────────────────────────

    def _load(self) -> None:
        """Restore node and job state from disk."""
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            for node_data in data.get("nodes", []):
                n = NodeHealth(**node_data)
                self._nodes[n.node_id] = n
            for job_data in data.get("jobs", []):
                j = JobRecord(**job_data)
                self._jobs[j.job_id] = j
        except (json.JSONDecodeError, OSError, TypeError):
            pass  # Corrupt state — start fresh

    def save(self) -> None:
        """Persist current state to disk atomically."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "nodes": [asdict(n) for n in self._nodes.values()],
            "jobs": [asdict(j) for j in self._jobs.values()],
        }
        tmp = self._state_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, self._state_path)

    # ── Node operations ────────────────────────────────────────

    def register_node(
        self,
        node_id: str,
        hostname: str = "",
        vram_total_mb: int = 0,
        **tags,
    ) -> NodeHealth:
        """Register or update a swarm node."""
        if node_id in self._nodes:
            node = self._nodes[node_id]
            node.hostname = hostname or node.hostname
            node.vram_total_mb = vram_total_mb or node.vram_total_mb
            node.tags.update(tags)
        else:
            node = NodeHealth(
                node_id=node_id,
                hostname=hostname,
                vram_total_mb=vram_total_mb,
                tags=tags,
            )
            self._nodes[node_id] = node
        return node

    def record_heartbeat(self, node_id: str) -> None:
        """Update a node's heartbeat timestamp and reset fail counter if alive."""
        node = self._nodes.get(node_id)
        if node is None:
            return
        now = time.time()
        node.last_heartbeat = now
        node.last_seen_alive = now
        # A heartbeat clears the consecutive failure counter (the node
        # is responding again).
        if not node.is_decommissioned:
            node.consecutive_failures = 0

    def record_failure(self, node_id: str) -> NodeHealth | None:
        """Increment the consecutive-failure counter for a node.

        Returns the node or None if the node is unknown.  Decommissioning
        is handled by the ``check_failures()`` method during the health-check
        cycle — this method only increments the counter.
        """
        node = self._nodes.get(node_id)
        if node is None:
            return None

        node.consecutive_failures += 1
        return node

    def decommission_node(self, node_id: str) -> NodeHealth | None:
        """Manually decommission a node (e.g., from a dashboard command)."""
        node = self._nodes.get(node_id)
        if node is None:
            return None
        node.is_decommissioned = True
        node.decommissioned_at = time.time()
        return node

    def recommission_node(self, node_id: str) -> NodeHealth | None:
        """Manually re-enable a decommissioned node."""
        node = self._nodes.get(node_id)
        if node is None:
            return None
        node.is_decommissioned = False
        node.decommissioned_at = None
        node.consecutive_failures = 0
        return node

    def get_failover_targets(self) -> list[NodeHealth]:
        """Return all nodes suitable for failover routing (alive, not
        decommissioned, no active jobs)."""
        return [n for n in self._nodes.values() if n.healthy_for_failover]

    def get_node(self, node_id: str) -> NodeHealth | None:
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[NodeHealth]:
        return list(self._nodes.values())

    # ── Job operations ─────────────────────────────────────────

    def register_job(
        self,
        job_id: str,
        node_id: str,
        issue_id: str = "",
        vram_reserved_mb: int = 0,
    ) -> JobRecord:
        """Register a new remote job on a node."""
        job = JobRecord(
            job_id=job_id,
            node_id=node_id,
            issue_id=issue_id,
            started_at=time.time(),
            last_heartbeat=time.time(),
            vram_reserved_mb=vram_reserved_mb,
        )
        self._jobs[job_id] = job

        # Add to node's active job list
        node = self._nodes.get(node_id)
        if node and job_id not in node.active_jobs:
            node.active_jobs.append(job_id)
            node.vram_allocated_mb += vram_reserved_mb

        return job

    def heartbeat_job(self, job_id: str) -> JobRecord | None:
        """Refresh a job's heartbeat timestamp."""
        job = self._jobs.get(job_id)
        if job:
            job.last_heartbeat = time.time()
        return job

    def complete_job(
        self,
        job_id: str,
        status: str = "completed",
        error_message: str = "",
    ) -> JobRecord | None:
        """Mark a job as completed/failed and free its VRAM reservation."""
        job = self._jobs.get(job_id)
        if job is None:
            return None

        job.status = status
        job.error_message = error_message

        # Remove from node's active job list and free VRAM
        node = self._nodes.get(job.node_id)
        if node:
            if job_id in node.active_jobs:
                node.active_jobs.remove(job_id)
            node.vram_allocated_mb -= job.vram_reserved_mb
            if node.vram_allocated_mb < 0:
                node.vram_allocated_mb = 0

        return job

    def find_stale_jobs(self) -> list[JobRecord]:
        """Find all jobs whose last heartbeat is older than JOB_TIMEOUT_S.

        Returns jobs sorted by age (oldest first).
        """
        now = time.time()
        stale = []
        for job in self._jobs.values():
            if job.status != "running":
                continue
            idle_time = now - job.last_heartbeat
            if idle_time > JOB_TIMEOUT_S:
                stale.append(job)
        stale.sort(key=lambda j: j.idle_s, reverse=True)
        return stale

    def get_node_jobs(self, node_id: str) -> list[JobRecord]:
        """Return all active jobs on a node."""
        return [j for j in self._jobs.values() if j.node_id == node_id and j.status == "running"]

    def active_job_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == "running")


# ═══════════════════════════════════════════════════════════════
# IPC Event Emission
# ═══════════════════════════════════════════════════════════════


def _emit_ipc_event(event_type: str, source: str, payload: dict) -> bool:
    """Send an event to the gateway IPC bridge (Unix socket, best-effort).

    Falls back to HTTP POST if the Unix socket is unavailable.
    Uses the same pattern as ``scripts/watchdog.py``.
    """
    socket_path = os.environ.get(
        "PRISMATIC_IPC_SOCKET",
        str(STATE_DIR / "ipc_bridge.sock"),
    )
    sock_path = Path(socket_path)

    event = json.dumps({
        "type": event_type,
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    })

    # Try Unix socket first
    if sock_path.exists():
        try:
            with sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(str(sock_path))
                s.sendall((event + "\n").encode())
            return True
        except Exception:
            pass  # Fall through to HTTP

    # HTTP fallback — POST to gateway API
    import urllib.request as _req
    gateway_url = os.environ.get(
        "PRISMATIC_GATEWAY_URL",
        "http://localhost:9000/api/gateway/events",
    )
    try:
        data = event.encode()
        _req.urlopen(
            _req.Request(
                gateway_url,
                data=data,
                headers={"Content-Type": "application/json"},
            ),
            timeout=3,
        )
        return True
    except Exception:
        pass

    return False


# ═══════════════════════════════════════════════════════════════
# VRAM Orphan Detection
# ═══════════════════════════════════════════════════════════════


def _scan_vram_markers() -> list[dict[str, Any]]:
    """Scan the VRAM marker directory for stale allocation markers.

    VRAM markers are JSON files in ``/tmp/prismatic/vram/`` named
    ``<node_id>_<pid>.vram`` containing allocated MB and the PID.
    Returns a list of markers whose process is no longer alive.
    """
    if not VRAM_MARKER_DIR.exists():
        return []

    import psutil

    orphans = []
    for marker_file in VRAM_MARKER_DIR.glob("*.vram"):
        try:
            with open(marker_file) as f:
                data = json.load(f)
            pid = data.get("pid", 0)
            node_id = data.get("node_id", "unknown")
            mb = data.get("mb", 0)

            # Check if the owning process is still alive
            try:
                proc = psutil.Process(pid)
                if not proc.is_running():
                    orphans.append({
                        "node_id": node_id,
                        "pid": pid,
                        "mb": mb,
                        "marker": str(marker_file),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                orphans.append({
                    "node_id": node_id,
                    "pid": pid,
                    "mb": mb,
                    "marker": str(marker_file),
                })
        except (json.JSONDecodeError, OSError):
            continue

    return orphans


def _cleanup_vram_marker(marker_path: str | Path) -> bool:
    """Remove a VRAM marker file.  Returns True on success."""
    try:
        Path(marker_path).unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ═══════════════════════════════════════════════════════════════
# Roster Loader
# ═══════════════════════════════════════════════════════════════


def _load_node_roster() -> list[dict[str, Any]]:
    """Load the swarm node roster from ``NODE_ROSTER``.

    Returns an empty list if the roster is unavailable.
    Expected format::

        [
            {
                "node_id": "k3s-worker-1",
                "hostname": "worker-1.local",
                "vram_total_mb": 24576,
                "tags": {"gpu_type": "rtx-3090"}
            },
            ...
        ]
    """
    if not NODE_ROSTER.exists():
        return []
    try:
        with open(NODE_ROSTER) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# ═══════════════════════════════════════════════════════════════
# Main Watchdog Circuit
# ═══════════════════════════════════════════════════════════════


class DistributedWatchdog:
    """Multi-node health circuit with 120s timeout, VRAM cleanup, and
    4-consecutive-failure node decommissioning.

    Typical usage::

        wd = DistributedWatchdog()
        wd.sync_roster()           # Load nodes from roster file
        stale = wd.find_timeouts() # Find jobs past 120s without heartbeat
        wd.cleanup_stale(stale)    # Mark timeouts, free VRAM, emit events
        wd.check_orphan_vram()     # Scan for orphaned GPU allocations
        wd.check_failures()        # Auto-decommission after 4 failures
        wd.emit_heartbeat()        # Signal that the watchdog is alive
        wd.save_state()
    """

    def __init__(
        self,
        registry: NodeRegistry | None = None,
        check_interval_s: int = DEFAULT_CHECK_INTERVAL_S,
    ):
        self.registry = registry or NodeRegistry()
        self.check_interval_s = check_interval_s
        self._actions_taken: int = 0
        self._log_lines: list[str] = []

    # ── Public API ─────────────────────────────────────────────

    def sync_roster(self) -> int:
        """Load swarm nodes from the roster file and register any new ones.

        Returns the number of nodes registered or updated.
        """
        roster = _load_node_roster()
        count = 0
        for entry in roster:
            node_id = entry.get("node_id", "")
            if not node_id:
                continue
            self.registry.register_node(
                node_id=node_id,
                hostname=entry.get("hostname", ""),
                vram_total_mb=entry.get("vram_total_mb", 0),
                **entry.get("tags", {}),
            )
            count += 1
        return count

    def find_timeouts(self) -> list[JobRecord]:
        """Find remote jobs that have exceeded JOB_TIMEOUT_S without a
        heartbeat from their node."""
        stale = self.registry.find_stale_jobs()
        for job in stale:
            self._log(f"STALE JOB: {job.job_id} on {job.node_id} "
                       f"idle {job.idle_s:.0f}s (threshold {JOB_TIMEOUT_S}s)")
        return stale

    def cleanup_stale(self, stale_jobs: list[JobRecord]) -> int:
        """Process a list of timed-out jobs.

        For each stale job:
        1. Marks the job as ``timed_out``
        2. Frees the VRAM reservation
        3. Emits a ``circuit_breaker_trip`` IPC event
        4. Records a failure on the owning node

        Returns the number of jobs cleaned up.
        """
        count = 0
        for job in stale_jobs:
            self.registry.complete_job(
                job.job_id,
                status="timed_out",
                error_message=f"Job timed out after {job.idle_s:.0f}s idle",
            )
            self.registry.record_failure(job.node_id)

            # Emit IPC event for circuit breaker
            _emit_ipc_event(
                "circuit_breaker_trip",
                "distributed_watchdog:timeout",
                {
                    "job_id": job.job_id,
                    "node_id": job.node_id,
                    "issue_id": job.issue_id,
                    "idle_seconds": round(job.idle_s, 1),
                    "timeout_threshold": JOB_TIMEOUT_S,
                },
            )
            count += 1
            self._actions_taken += 1

        return count

    def check_orphan_vram(self) -> int:
        """Scan for orphaned VRAM allocations whose owning process has died.

        Removes stale marker files and emits ``error_alert`` events for
        each orphan found.

        Returns the number of orphaned allocations cleaned up.
        """
        orphans = _scan_vram_markers()
        count = 0
        for orphan in orphans:
            _cleanup_vram_marker(orphan["marker"])
            _emit_ipc_event(
                "error_alert",
                "distributed_watchdog:vram_orphan",
                {
                    "node_id": orphan["node_id"],
                    "pid": orphan["pid"],
                    "mb": orphan["mb"],
                    "marker": orphan["marker"],
                },
            )
            self._log(f"VRAM ORPHAN: {orphan['mb']}MB on {orphan['node_id']} "
                       f"(PID {orphan['pid']}) — cleaned")
            count += 1
            self._actions_taken += 1

        return count

    def check_failures(self) -> list[str]:
        """Evaluate node failure counts and decommission nodes that have
        exceeded MAX_CONSECUTIVE_FAILURES.

        Returns a list of decommissioned node IDs.
        """
        decommissioned = []
        for node in self.registry.all_nodes():
            if (
                node.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                and not node.is_decommissioned
            ):
                self.registry.decommission_node(node.node_id)
                self._log(f"DECOMMISSIONED: {node.node_id} after "
                           f"{node.consecutive_failures} consecutive failures")

                _emit_ipc_event(
                    "agent_offline",
                    "distributed_watchdog:decommission",
                    {
                        "node_id": node.node_id,
                        "hostname": node.hostname,
                        "consecutive_failures": node.consecutive_failures,
                        "decommissioned_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                decommissioned.append(node.node_id)
                self._actions_taken += 1

        return decommissioned

    def find_failover_candidates(self, exclude_node_id: str) -> list[NodeHealth]:
        """Find healthy nodes suitable for re-routing work from a failing node.

        Filters out the given ``exclude_node_id``.
        """
        return [
            n for n in self.registry.get_failover_targets()
            if n.node_id != exclude_node_id
        ]

    def emit_heartbeat(self) -> None:
        """Emit a watchdog self-heartbeat via IPC so the dashboard can track
        that *this* watchdog is alive."""
        _emit_ipc_event(
            "agent_heartbeat",
            "distributed_watchdog",
            {
                "agent_id": "distributed_watchdog",
                "session_id": f"distributed-watchdog-{int(time.time())}",
                "status": "busy" if self._actions_taken > 0 else "idle",
                "actions_taken": self._actions_taken,
                "nodes_monitored": len(self.registry.all_nodes()),
                "active_jobs": self.registry.active_job_count(),
            },
        )

    def run_once(self) -> int:
        """Execute one full health-check cycle.

        ``sync_roster`` → ``find_timeouts`` → ``cleanup_stale`` →
        ``check_orphan_vram`` → ``check_failures`` → ``emit_heartbeat`` →
        ``save_state``

        Returns the number of actions taken.
        """
        self._actions_taken = 0
        self._log_lines = []

        # 1. Sync node roster
        nodes = self.sync_roster()
        if nodes:
            self._log(f"Roster synced: {nodes} nodes")

        # 2. Check for stale jobs
        stale = self.find_timeouts()
        if stale:
            cleaned = self.cleanup_stale(stale)
            self._log(f"Cleaned {cleaned} stale job(s)")

            # For each node with stale jobs, find failover candidates
            affected_nodes = set(j.node_id for j in stale)
            for node_id in affected_nodes:
                candidates = self.find_failover_candidates(node_id)
                if candidates:
                    names = ", ".join(c.node_id for c in candidates)
                    self._log(f"Failover targets for {node_id}: {names}")

        # 3. Check orphan VRAM
        orphans = self.check_orphan_vram()
        if orphans:
            self._log(f"Cleaned {orphans} orphaned VRAM allocation(s)")

        # 4. Check failure thresholds
        decommissioned = self.check_failures()
        if decommissioned:
            self._log(f"Decommissioned node(s): {', '.join(decommissioned)}")

        # 5. Emit heartbeat
        self.emit_heartbeat()

        # 6. Save state
        self.registry.save()

        return self._actions_taken

    def load_state(self) -> None:
        """Reload node and job state from disk."""
        self.registry = NodeRegistry()
        self._actions_taken = 0
        self._log_lines = []

    def save_state(self) -> None:
        """Persist current health state to disk."""
        self.registry.save()

    def actions_taken(self) -> int:
        return self._actions_taken

    def last_log(self) -> list[str]:
        return self._log_lines

    # ── Internal ───────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        self._log_lines.append(line)


# ═══════════════════════════════════════════════════════════════
# CLI Entrypoint
# ═══════════════════════════════════════════════════════════════


def main() -> int:
    """CLI entrypoint: run one health-check cycle.

    Returns:
        0 — all healthy, no actions taken
        2 — actions taken (stale jobs cleaned, nodes decommissioned, etc.)
        1 — error
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Prismatic DistributedWatchdog — Multi-Node Health Circuit",
    )
    parser.add_argument(
        "--check-interval",
        type=int,
        default=DEFAULT_CHECK_INTERVAL_S,
        help="Check interval in seconds (default: %(default)s)",
    )
    args = parser.parse_args()

    wd = DistributedWatchdog(check_interval_s=args.check_interval)
    actions = wd.run_once()

    if actions == 0:
        print("DistributedWatchdog: All nodes healthy — nothing to do")
        return 0

    print(f"DistributedWatchdog: {actions} action(s) taken")
    return 2


if __name__ == "__main__":
    sys.exit(main())
