#!/usr/bin/env python3
"""
Stale Lock Watcher — auto-releases abandoned file locks from crashed agent sessions.

Part of the Prismatic Engine governance layer. Runs as a cron job every 2 minutes.
Reads the centralized lock registry and removes any lock whose heartbeat is older
than the configured TTL (5 minutes by default).

Usage:
    python3 prismatic/stale_lock_watcher.py

Config:
    Lock registry: /home/ubuntu/.antigravity/swarm_locks.json
    Stale TTL: 300000ms (5 minutes) — from PRISMATIC_ENGINE.yaml
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────
LOCK_FILE = Path("/home/ubuntu/.antigravity/swarm_locks.json")
STALE_TTL_MS = 300_000  # 5 minutes


def read_locks() -> list[dict[str, Any]]:
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


def write_locks(locks: list[dict[str, Any]]) -> None:
    """Atomically write the lock registry."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCK_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(locks, f, indent=2)
    os.replace(tmp, LOCK_FILE)


def main() -> int:
    """Read locks, purge stale entries, log actions, write back."""
    locks = read_locks()

    if not locks:
        return 0  # Nothing to do

    now_ms = int(time.time() * 1000)
    active_locks = []
    released_count = 0

    for lock in locks:
        # Support both swarm.js format ('heartbeat') and legacy format ('lastHeartbeat', 'timestamp')
        last_hb = lock.get("heartbeat", lock.get("lastHeartbeat", lock.get("timestamp", 0)))
        age_ms = now_ms - last_hb

        if age_ms > STALE_TTL_MS:
            path = lock.get("path", lock.get("filePath", "unknown"))
            agent = lock.get("agent", lock.get("agentId", "unknown"))
            age_seconds = age_ms / 1000
            print(f"[SwarmLock] Auto-released stale lock on {path} held by {agent} "
                  f"(age: {age_seconds:.0f}s, TTL: {STALE_TTL_MS/1000:.0f}s)")
            released_count += 1
        else:
            active_locks.append(lock)

    if released_count > 0:
        write_locks(active_locks)
        print(f"[SwarmLock] Released {released_count} stale lock(s), "
              f"{len(active_locks)} active lock(s) remaining")

    return 0


if __name__ == "__main__":
    sys.exit(main())
