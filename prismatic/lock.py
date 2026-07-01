"""
Swarm Lock CLI — centralized file locking for multi-agent repos.

Part of the Prismatic Engine governance layer. Prevents multiple AI agents
from editing the same file simultaneously by maintaining a centralized lock
registry with heartbeat-based stale detection.

Usage:
    prismatic-lock lock <file> <agent>     # Claim a file
    prismatic-lock unlock <file> <agent>   # Release a file
    prismatic-lock status                   # Show all locks
    prismatic-lock heartbeat <file> <agent> # Refresh heartbeat

Config:
    Lock registry: $PRISMATIC_HOME/.antigravity/swarm_locks.json
    Stale TTL: 300000ms (5 minutes) — from PRISMATIC_ENGINE.yaml
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# ── Event Emission via IPC Bridge ─────────────────────
try:
    from prismatic.gateway.ipc_bridge import DEFAULT_SOCKET_PATH, send_event_via_socket
    _HAS_IPC = True
except ImportError:
    _HAS_IPC = False


def _emit_lock_event(event_type: str, filepath: str, agent_id: str, **extra: Any) -> None:
    """Emit a lock event to the IPC bridge (best-effort, no-op if unavailable)."""
    if not _HAS_IPC:
        return
    try:
        send_event_via_socket(
            event_type=event_type,
            source=f"lock:{agent_id}",
            payload={"file": filepath, "agent": agent_id, **extra},
        )
    except Exception:
        pass  # Best-effort — don't let event emission break locking

# ── Constants ──────────────────────────────────────────
_PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~"))
LOCK_FILE = _PRISMATIC_HOME / ".antigravity" / "swarm_locks.json"
STALE_TTL_MS = 300_000  # 5 minutes


# ── Lock Registry Operations ───────────────────────────


def _read_locks() -> list[dict[str, Any]]:
    """Read the lock registry, returning empty list if missing or corrupt."""
    if not LOCK_FILE.exists():
        return []
    try:
        with open(LOCK_FILE) as f:
            data = json.load(f)
            if not isinstance(data, list):
                return []
            return data
    except (json.JSONDecodeError, OSError):
        return []


def _write_locks(locks: list[dict[str, Any]]) -> None:
    """Atomically write the lock registry."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCK_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(locks, f, indent=2)
    os.replace(tmp, LOCK_FILE)


@contextmanager
def _lock_file():
    """Lock the registry file for thread-safe access."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.touch(exist_ok=True)
    with open(LOCK_FILE, "r+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _get_repo_root() -> Path | None:
    """Find the git repository root for the current directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _make_relative(filepath: str) -> str:
    """Convert a path to repo-relative form.

    If we're inside a git repo, paths are relative to the repo root.
    If not, paths are relative to the current working directory.
    """
    path = Path(filepath).resolve()
    repo_root = _get_repo_root()
    if repo_root:
        try:
            rel = path.relative_to(repo_root)
            return str(rel)
        except ValueError:
            pass
    # Not inside repo or path outside repo — use relative to CWD
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _prune_stale(locks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Remove locks whose heartbeat has expired. Returns (pruned_locks, removed_count)."""
    now_ms = int(time.time() * 1000)
    kept = []
    removed = 0
    for lock in locks:
        last_hb = lock.get("lastHeartbeat", lock.get("timestamp", 0))
        if now_ms - last_hb > STALE_TTL_MS:
            removed += 1
        else:
            kept.append(lock)
    return kept, removed


# ── Commands ───────────────────────────────────────────


def cmd_lock(filepath: str, agent_id: str) -> int:
    """Claim a file for an agent."""
    filepath = _make_relative(filepath)

    with _lock_file():
        locks = _read_locks()
        locks, removed = _prune_stale(locks)
        if removed:
            print(f"  Pruned {removed} stale lock(s)")

        # Check if already locked
        for lock in locks:
            if lock["filePath"] == filepath:
                if lock["agentId"] == agent_id:
                    # Same agent re-locking — update heartbeat
                    lock["lastHeartbeat"] = int(time.time() * 1000)
                    _write_locks(locks)
                    print(f"✅ Lock refreshed: {filepath} (held by {agent_id})")
                    return 0
                else:
                    print(
                        f"❌ LOCKED: {filepath} is held by {lock['agentId']} "
                        f"(since {_format_time(lock['timestamp'])})"
                    )
                    return 1

        # Acquire new lock
        now_ms = int(time.time() * 1000)
        locks.append({
            "filePath": filepath,
            "agentId": agent_id,
            "timestamp": now_ms,
            "lastHeartbeat": now_ms,
        })
        _write_locks(locks)

    print(f"🔒 Locked: {filepath} → {agent_id}")
    _emit_lock_event("lock", filepath, agent_id)
    return 0


def cmd_unlock(filepath: str, agent_id: str) -> int:
    """Release a file lock."""
    filepath = _make_relative(filepath)

    with _lock_file():
        locks = _read_locks()
        locks, removed = _prune_stale(locks)
        if removed:
            print(f"  Pruned {removed} stale lock(s)")

        for i, lock in enumerate(locks):
            if lock["filePath"] == filepath:
                if lock["agentId"] != agent_id:
                    print(
                        f"❌ Cannot unlock: {filepath} is held by {lock['agentId']}, "
                        f"not {agent_id}"
                    )
                    return 1
                removed_lock = locks.pop(i)
                _write_locks(locks)
                print(
                    f"🔓 Unlocked: {filepath} (was held by {agent_id} "
                    f"for {_duration_ms(removed_lock['timestamp'])})"
                )
                _emit_lock_event("unlock", filepath, agent_id, duration_ms=removed_lock["timestamp"])
                return 0

    print(f"⚠️  Not locked: {filepath}")
    return 0


def cmd_status() -> int:
    """Show all active locks with stale pruning."""
    with _lock_file():
        locks = _read_locks()
        locks, removed = _prune_stale(locks)
        if removed:
            _write_locks(locks)

    if not locks:
        print("No active locks.")
        if removed:
            print(f"  (Pruned {removed} stale lock(s))")
        return 0

    print(f"Active locks ({len(locks)}):")
    if removed:
        print(f"  (Pruned {removed} stale lock(s))")
    print()

    now_ms = int(time.time() * 1000)
    for lock in sorted(locks, key=lambda l: l["timestamp"]):
        age_ms = now_ms - lock["lastHeartbeat"]
        age_str = _duration_ms(lock["lastHeartbeat"], suffix=" ago")
        staleness = ""
        if age_ms > STALE_TTL_MS * 0.8:
            staleness = " ⚠️ near-stale"
        print(
            f"  {lock['filePath']:50s}  {lock['agentId']:15s}  "
            f"held {age_str}{staleness}"
        )
    return 0


def cmd_heartbeat(filepath: str, agent_id: str) -> int:
    """Refresh the heartbeat timestamp for a lock."""
    filepath = _make_relative(filepath)

    with _lock_file():
        locks = _read_locks()
        locks, removed = _prune_stale(locks)

        for lock in locks:
            if lock["filePath"] == filepath and lock["agentId"] == agent_id:
                lock["lastHeartbeat"] = int(time.time() * 1000)
                _write_locks(locks)
                print(f"💓 Heartbeat: {filepath} ({agent_id})")
                _emit_lock_event("heartbeat", filepath, agent_id)
                if removed:
                    print(f"  Pruned {removed} stale lock(s)")
                return 0

    print(f"⚠️  No lock found for {filepath} by {agent_id}")
    return 1


# ── Helpers ────────────────────────────────────────────


def _format_time(ts_ms: int) -> str:
    """Format a millisecond timestamp as a human-readable string."""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%H:%M:%S UTC")


def _duration_ms(start_ms: int, suffix: str = "") -> str:
    """Format a duration from start_ms to now."""
    elapsed_ms = int(time.time() * 1000) - start_ms
    if elapsed_ms < 1000:
        return f"{elapsed_ms}ms{suffix}"
    elif elapsed_ms < 60_000:
        return f"{elapsed_ms / 1000:.1f}s{suffix}"
    elif elapsed_ms < 3_600_000:
        return f"{elapsed_ms / 60_000:.1f}m{suffix}"
    else:
        return f"{elapsed_ms / 3_600_000:.1f}h{suffix}"


# ── CLI Entry Point ────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="prismatic-lock",
        description="Centralized file locking for multi-agent repos (Prismatic Engine)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # lock
    p_lock = sub.add_parser("lock", help="Claim a file for an agent")
    p_lock.add_argument("file", help="File path (repo-relative or absolute)")
    p_lock.add_argument("agent", help="Agent identifier (e.g., fred, kai, ned)")

    # unlock
    p_unlock = sub.add_parser("unlock", help="Release a file lock")
    p_unlock.add_argument("file", help="File path")
    p_unlock.add_argument("agent", help="Agent identifier")

    # status
    sub.add_parser("status", help="Show all active locks")

    # heartbeat
    p_hb = sub.add_parser("heartbeat", help="Refresh lock heartbeat")
    p_hb.add_argument("file", help="File path")
    p_hb.add_argument("agent", help="Agent identifier")

    args = parser.parse_args()

    if args.command == "lock":
        sys.exit(cmd_lock(args.file, args.agent))
    elif args.command == "unlock":
        sys.exit(cmd_unlock(args.file, args.agent))
    elif args.command == "status":
        sys.exit(cmd_status())
    elif args.command == "heartbeat":
        sys.exit(cmd_heartbeat(args.file, args.agent))


if __name__ == "__main__":
    main()
