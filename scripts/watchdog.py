#!/usr/bin/env python3
"""
Prismatic Engine Watchdog — Agent Liveness Monitor & Auto-Restarter
===================================================================

Runs as a systemd oneshot service triggered by ``prismatic-watchdog.timer``
every 30s. Checks agent health via:

1. **Lock registry heartbeats** — Reads ``swarm_locks.json`` for stale lock
   heartbeats (>5min TTL). Prunes and reports.
2. **Process liveness** — Uses ``psutil`` (best-effort) to check if known
   agent processes (agy, jules, codex) are still alive.
3. **Gateway heartbeat events** — Sends an ``agent_heartbeat_watchdog`` event
   to the Port 9000 IPC bridge so the gateway can track watchdog health itself.

When an agent process is found dead but holding locks, the watchdog:
  - Force-releases the stale locks
  - Emits ``agent_failed`` via IPC bridge so the dispatcher can re-assign

Exit codes:
  0 — All agents healthy, nothing to do
  2 — Stale agents/locks cleaned up (action taken)
  1 — Error (lock registry unreadable, etc.)

Environment:
  PRISMATIC_HOME — root path (default: /home/ubuntu)
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", "/home/ubuntu"))
LOCK_FILE = PRISMATIC_HOME / ".antigravity" / "swarm_locks.json"
STATE_DIR = Path("/tmp/prismatic")
WATCHDOG_STATE = STATE_DIR / "watchdog_state.json"
STALE_LOCK_TTL_MS = 300_000  # 5 minutes
STALE_AGENT_TTL_S = 45       # 3 missed heartbeats @ 15s each

# Known agent process names to check for
AGENT_PROCESS_NAMES = {"agy", "jules", "codex"}

# Try psutil import (best-effort)
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def _log(msg: str) -> None:
    """Timestamped log line to stdout (captured by systemd journal)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def _emit_ipc_event(event_type: str, source: str, payload: dict) -> bool:
    """Send an event to the IPC bridge Unix socket (best-effort).

    Falls back to HTTP POST to the gateway if Unix socket is unavailable.
    """
    import socket as sock_mod

    # Try Unix socket first
    socket_path = os.environ.get(
        "PRISMATIC_IPC_SOCKET",
        "/tmp/ipc_bridge.sock",
    )
    sock_path = Path(socket_path)

    event = json.dumps({
        "type": event_type,
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    })

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
        data = json.dumps(event).encode()
        _req.urlopen(
            _req.Request(gateway_url, data=data,
                         headers={"Content-Type": "application/json"}),
            timeout=3,
        )
        return True
    except Exception:
        pass

    return False


def _check_lock_registry() -> tuple[list[dict], list[dict]]:
    """Read lock registry, return (kept, pruned)."""
    if not LOCK_FILE.exists():
        return [], []

    try:
        with open(LOCK_FILE) as f:
            locks = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        _log(f"Lock registry unreadable: {e}")
        return [], []

    if not isinstance(locks, list):
        _log(f"Lock registry is not a list (type={type(locks).__name__})")
        return [], []

    now_ms = int(time.time() * 1000)
    kept, pruned = [], []

    for lock in locks:
        last_hb = lock.get("lastHeartbeat", lock.get("timestamp", 0))
        if now_ms - last_hb > STALE_LOCK_TTL_MS:
            pruned.append(lock)
        else:
            kept.append(lock)

    return kept, pruned


def _check_agent_processes() -> dict[str, bool]:
    """Check which known agent processes are alive. Returns {name: alive}."""
    if not _HAS_PSUTIL:
        _log("psutil not available — skipping process check")
        return {}

    alive = {}
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            info = proc.info
            cmdline = ' '.join(info.get('cmdline', []) or [])
            # Check for agent binary names in cmdline
            for agent_name in AGENT_PROCESS_NAMES:
                if agent_name in cmdline.lower():
                    alive[agent_name] = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for name in AGENT_PROCESS_NAMES:
        if name not in alive:
            alive[name] = False

    return alive


def _kill_stale_agent(agent_name: str) -> bool:
    """Find and kill a stale agent process. Returns True if killed."""
    if not _HAS_PSUTIL:
        return False

    killed = False
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            info = proc.info
            cmdline = ' '.join(info.get('cmdline', []) or [])
            if agent_name in cmdline.lower():
                _log(f"Killing stale agent '{agent_name}' (PID={info['pid']})")
                os.kill(info['pid'], signal.SIGKILL)
                killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            continue

    return killed


def _load_agent_heartbeat_state() -> dict:
    """Load persistent agent heartbeat tracking state."""
    if not WATCHDOG_STATE.exists():
        return {"agents": {}, "last_run": None}
    try:
        with open(WATCHDOG_STATE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"agents": {}, "last_run": None}


def _save_agent_heartbeat_state(state: dict) -> None:
    """Save persistent agent heartbeat tracking state."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = WATCHDOG_STATE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, WATCHDOG_STATE)


def main() -> int:
    actions_taken = 0

    # ── 1. Lock registry cleanup ───────────────────────────────
    kept, pruned = _check_lock_registry()
    if pruned:
        _log(f"Pruned {len(pruned)} stale lock(s):")
        for lock in pruned:
            now_ms = int(time.time() * 1000)
            age_s = (now_ms - lock.get("lastHeartbeat", lock.get("timestamp", 0))) / 1000
            agent = lock.get("agentId", "?")
            filepath = lock.get("filePath", "?")
            _log(f"  {filepath:50s}  agent={agent:12s}  stale {age_s:.0f}s")
            # Emit agent_failed for stale locks from known agents
            if agent in AGENT_PROCESS_NAMES:
                _emit_ipc_event(
                    "agent_failed",
                    f"watchdog:lock_prune:{agent}",
                    {"agent": agent, "reason": f"stale_lock_{age_s:.0f}s",
                     "file": filepath}
                )

        # Write back cleaned registry
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOCK_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(kept, f, indent=2)
        os.replace(tmp, LOCK_FILE)
        actions_taken += 1

    if kept:
        _log(f"{len(kept)} lock(s) active, {len(pruned)} stale pruned")

    # ── 2. Agent process liveness check ────────────────────────
    agent_state = _load_agent_heartbeat_state()
    alive_agents = _check_agent_processes()
    now_ts = datetime.now(timezone.utc).isoformat()

    for agent_name in AGENT_PROCESS_NAMES:
        is_alive = alive_agents.get(agent_name, None)

        if is_alive is None:
            continue  # psutil not available, skip

        prev = agent_state.get("agents", {}).get(agent_name, {})
        prev_alive = prev.get("last_alive", True)
        missed_count = prev.get("missed_checks", 0)

        if not is_alive and prev_alive:
            # Agent just went down
            _log(f"Agent '{agent_name}' process NOT found — tracking missed checks")
            missed_count += 1
        elif not is_alive and not prev_alive:
            # Still down
            missed_count += 1
        elif is_alive:
            # Agent is alive — reset counter
            missed_count = 0

        # Check if agent was holding locks but is now dead
        if not is_alive and prev_alive:
            agent_locks = [l for l in kept for _ in [1] if l.get("agentId") == agent_name]
            if agent_locks:
                _log(f"Agent '{agent_name}' dead but holds {len(agent_locks)} lock(s)")
                # Emit failed event so dispatcher can re-assign work
                _emit_ipc_event(
                    "agent_failed",
                    f"watchdog:process:{agent_name}",
                    {"agent": agent_name, "reason": "process_dead",
                     "lock_count": len(agent_locks)}
                )
                actions_taken += 1

        agent_state["agents"][agent_name] = {
            "last_alive": is_alive,
            "missed_checks": missed_count,
            "last_checked": now_ts,
        }

    agent_state["last_run"] = now_ts
    _save_agent_heartbeat_state(agent_state)

    # ── 3. Emit watchdog self-heartbeat ────────────────────────
    _emit_ipc_event(
        "agent_heartbeat",
        "watchdog",
        {
            "agent_id": "watchdog",
            "session_id": f"watchdog-{int(time.time())}",
            "status": "busy" if actions_taken > 0 else "idle",
            "actions_taken": actions_taken,
            "agents_checked": list(alive_agents.keys()) if alive_agents else [],
        }
    )

    if actions_taken == 0:
        _log("All agents healthy — nothing to do")
        return 0

    _log(f"Watchdog complete — {actions_taken} action(s) taken")
    return 2  # Non-zero so cron/systemd sees "action taken"


if __name__ == "__main__":
    sys.exit(main())
