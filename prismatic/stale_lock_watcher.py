#!/usr/bin/env python3
"""
Prismatic Stale Lock Watcher — cron-friendly dead-lock cleanup.

Runs every 2m via cron (job 3ff4762d5d32). Reads the centralized lock
registry, prunes any lock whose heartbeat has expired (5min TTL), and
reports what was cleaned up.

Exit codes:
  0 — no stale locks found (nothing to do)
  2 — stale locks were pruned (actionable — cron sees this as "error" so
       the output is visible in logs)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", os.environ.get("HOME", ".")))
LOCK_FILE = PRISMATIC_HOME / ".antigravity" / "swarm_locks.json"
STALE_TTL_MS = 300_000  # 5 minutes


def main() -> int:
    if not LOCK_FILE.exists():
        print("No lock registry found — nothing to watch.")
        return 0

    try:
        with open(LOCK_FILE) as f:
            locks = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  Lock registry unreadable: {e}")
        return 1

    if not isinstance(locks, list):
        print(f"⚠️  Lock registry is not a list (type={type(locks).__name__})")
        return 1

    now_ms = int(time.time() * 1000)
    kept = []
    pruned = []

    for lock in locks:
        last_hb = lock.get("lastHeartbeat", lock.get("timestamp", 0))
        if now_ms - last_hb > STALE_TTL_MS:
            pruned.append(lock)
        else:
            kept.append(lock)

    if not pruned:
        print(f"✅ All {len(locks)} lock(s) healthy.")
        return 0

    # Write back cleaned registry
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCK_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(kept, f, indent=2)
    os.replace(tmp, LOCK_FILE)

    print(f"🔓 Pruned {len(pruned)} stale lock(s):")
    for lock in pruned:
        age_s = (now_ms - lock.get("lastHeartbeat", lock.get("timestamp", 0))) / 1000
        print(
            f"  {lock.get('filePath', '?'):50s}  "
            f"agent={lock.get('agentId', '?'):12s}  "
            f"stale {age_s:.0f}s"
        )
    print(f"  {len(kept)} lock(s) remain active.")

    return 2  # Non-zero so cron sees "action taken"


if __name__ == "__main__":
    sys.exit(main())
