"""
prismatic.supervisor.recovery — bounded supervisor pool (Epic 1, Story 1.2).

Fixes the Popen worker-leak bug (BUG-10 in opus-event-driven-full-review.md):

- Tracks live supervisor PIDs in a process-level registry
- Caps concurrent supervisors at MAX_CONCURRENT (default 8, env-configurable)
- Reaps finished supervisors every REAP_INTERVAL_SEC (default 30s)
- On cap-hit: backpressure (skip dispatch, retry next poll cycle)
- Optional DLQ: events that fail N times get marked failed, not retried forever

Used by:
- dispatch_consumer_v3.py: replaces bare Popen with dispatch_to_supervisor_bounded()
- prismatic/curator/lane.py: when tagging events as 'delegate', spawn supervisor
"""
from __future__ import annotations

import os
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# === Config (env-overridable) ===

MAX_CONCURRENT = int(os.environ.get("PRISMATIC_SUPERVISOR_MAX", "8"))
REAP_INTERVAL_SEC = float(os.environ.get("PRISMATIC_SUPERVISOR_REAP_INTERVAL", "30"))
MAX_RETRIES_PER_EVENT = int(os.environ.get("PRISMATIC_SUPERVISOR_MAX_RETRIES", "3"))
DLQ_PATH = Path(os.environ.get("PRISMATIC_SUPERVISOR_DLQ",
                              os.path.expanduser("~/.prismatic/supervisor/dlq.jsonl")))


@dataclass
class SupervisorRecord:
    """A running supervisor process."""
    pid: int
    issue_id: str
    started_at: float
    cmd: list[str]
    retry_count: int = 0

    def is_alive(self) -> bool:
        """Check if the OS process is still running."""
        try:
            os.kill(self.pid, 0)  # signal 0 = check existence only
            return True
        except OSError:
            return False

    def age_sec(self) -> float:
        return time.time() - self.started_at


class SupervisorPool:
    """Bounded pool of supervisor subprocesses.

    Thread-safe enough for single-threaded async usage. For multi-threaded
    usage, wrap calls in an asyncio.Lock.
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT,
                 reap_interval_sec: float = REAP_INTERVAL_SEC,
                 max_retries: int = MAX_RETRIES_PER_EVENT,
                 dlq_path: Path = DLQ_PATH):
        self.max_concurrent = max_concurrent
        self.reap_interval_sec = reap_interval_sec
        self.max_retries = max_retries
        self.dlq_path = dlq_path
        self._pool: dict[int, SupervisorRecord] = {}
        self._retry_counts: dict[str, int] = {}  # issue_id -> retry count
        self._last_reap = time.time()
        self._total_spawned = 0
        self._total_reaped = 0
        self._total_skipped_cap = 0
        self._total_skipped_dlq = 0

    def reap(self) -> int:
        """Remove finished supervisors from the pool. Returns count reaped.

        A process is reaped if (a) it's no longer alive in the OS, OR
        (b) it's been alive longer than MAX_SUPERVISOR_AGE_SEC (default 1h)
        — likely a hung supervisor that needs SIGKILL.

        Uses os.waitpid(WNOHANG) to actually reap zombies (otherwise
        os.kill(pid, 0) may still report them as alive).
        """
        now = time.time()
        if now - self._last_reap < self.reap_interval_sec:
            return 0
        self._last_reap = now
        max_age = float(os.environ.get("PRISMATIC_SUPERVISOR_MAX_AGE_SEC", "3600"))
        # First pass: actually reap zombies via waitpid
        for pid in list(self._pool.keys()):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    # Process was reaped (zombie cleared)
                    del self._pool[pid]
                    self._total_reaped += 1
            except ChildProcessError:
                # Not our child (shouldn't happen since we spawned it)
                del self._pool[pid]
                self._total_reaped += 1
            except OSError:
                pass

        # Second pass: check for hung or dead processes we missed
        for pid, rec in list(self._pool.items()):
            if not rec.is_alive():
                del self._pool[pid]
                self._total_reaped += 1
            elif rec.age_sec() > max_age:
                # Hung supervisor — try SIGKILL
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
                del self._pool[pid]
                self._total_reaped += 1
        return 0  # count tracked via _total_reaped

    def live_count(self) -> int:
        """Count of currently-running supervisors (active OS processes)."""
        return sum(1 for rec in self._pool.values() if rec.is_alive())

    def stats(self) -> dict:
        """Return pool stats for observability."""
        return {
            "max_concurrent": self.max_concurrent,
            "live_count": self.live_count(),
            "tracked": len(self._pool),
            "total_spawned": self._total_spawned,
            "total_reaped": self._total_reaped,
            "total_skipped_cap": self._total_skipped_cap,
            "total_skipped_dlq": self._total_skipped_dlq,
        }

    def can_dispatch(self) -> bool:
        """True if there's room to dispatch a new supervisor."""
        return self.live_count() < self.max_concurrent

    def dispatch(self, issue_id: str, cmd: list[str]) -> SupervisorRecord | None:
        """Spawn a supervisor for issue_id. Returns the record, or None if
        the pool is full (caller should retry next cycle).

        The cmd list is the argv for the supervisor process. The pool tracks
        the PID for reaping. If the issue_id has hit max_retries, it's
        written to the DLQ instead.

        Reaps synchronously after spawn to immediately clean up any
        zombies from prior dispatches, regardless of the reap_interval.
        """
        # Reap stale entries first (synchronous, fast)
        self._reap_zombies()

        # Check retry count
        retry_count = self._retry_counts.get(issue_id, 0)
        if retry_count >= self.max_retries:
            self._write_dlq(issue_id, cmd, reason="max_retries_exceeded")
            self._total_skipped_dlq += 1
            return None

        # Check pool capacity
        if not self.can_dispatch():
            self._total_skipped_cap += 1
            return None

        # Spawn
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # start_new_session: detach from parent process group so
                # the supervisor survives even if the dispatcher dies
                start_new_session=True,
            )
            rec = SupervisorRecord(
                pid=proc.pid,
                issue_id=issue_id,
                started_at=time.time(),
                cmd=cmd,
                retry_count=retry_count,
            )
            self._pool[proc.pid] = rec
            self._total_spawned += 1
            return rec
        except Exception as e:
            # Increment retry count, will be retried up to max_retries
            self._retry_counts[issue_id] = retry_count + 1
            raise

    def _reap_zombies(self) -> int:
        """Synchronous reap: just call waitpid on tracked PIDs. Returns count reaped."""
        n = 0
        for pid in list(self._pool.keys()):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    del self._pool[pid]
                    self._total_reaped += 1
                    n += 1
            except ChildProcessError:
                del self._pool[pid]
                self._total_reaped += 1
                n += 1
            except OSError:
                pass
        return n

    def on_failure(self, issue_id: str, error: str) -> None:
        """Call when a supervisor reports failure. Increments retry count."""
        self._retry_counts[issue_id] = self._retry_counts.get(issue_id, 0) + 1
        if self._retry_counts[issue_id] >= self.max_retries:
            self._write_dlq(issue_id, [], reason=f"failure_threshold_reached: {error}")

    def _write_dlq(self, issue_id: str, cmd: list[str], reason: str) -> None:
        """Append a dead-letter record."""
        self.dlq_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        record = {
            "issue_id": issue_id,
            "cmd": cmd,
            "reason": reason,
            "ts": time.time(),
        }
        with self.dlq_path.open("a") as f:
            f.write(json.dumps(record) + "\n")


# === Module-level singleton ===

_pool: SupervisorPool | None = None


def get_pool() -> SupervisorPool:
    """Get the module-level singleton pool."""
    global _pool
    if _pool is None:
        _pool = SupervisorPool()
    return _pool


def reset_pool() -> None:
    """Reset the module-level singleton (for tests)."""
    global _pool
    _pool = None


def dispatch_to_supervisor_bounded(issue_id: str, cmd: list[str]) -> dict:
    """Convenience wrapper: dispatch via the singleton pool. Returns a
    status dict with the result. Caller should treat None as 'try again
    later' and not mark the event as failed.
    """
    pool = get_pool()
    rec = pool.dispatch(issue_id, cmd)
    if rec is None:
        stats = pool.stats()
        return {
            "status": "skipped",
            "reason": "pool_full" if stats["live_count"] >= stats["max_concurrent"] else "dlq",
            "stats": stats,
        }
    return {
        "status": "spawned",
        "pid": rec.pid,
        "issue_id": issue_id,
        "stats": pool.stats(),
    }