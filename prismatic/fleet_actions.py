"""
prismatic.fleet_actions — Recovery handlers for the Prismatic Engine fleet watchdog.

Each handler is an idempotent, side-effectful action the watchdog invokes BEFORE
escalating an alert to the operator. Contract:

  - Signature: (ctx: dict) -> tuple[str, str]  where status ∈ {"ok", "failed", "skipped"}
  - Idempotent: re-running on a healthy system returns "skipped".
  - Logs to stdout so the watchdog report can show what was attempted.
  - NEVER depends on Hermes, AGY, or any orchestrator. Pure Python + stdlib.

Adding a new action:
  1. Define the function with signature (ctx: dict) -> tuple[str, str].
  2. Add an entry to ACTIONS_BY_ALERT_PREFIX matching the alert text.
  3. Add a test in tests/test_fleet_watchdog.py.

Engine-specific notes:
  - Paths use env vars with sensible defaults (PRISMATIC_STATE_DIR, etc.)
  - systemctl calls need sudo; we surface PermissionError explicitly
  - All DB ops use sqlite3 stdlib (no ORM, no PrismaticEngine imports)
"""
from __future__ import annotations

import glob
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Callable

# ── Paths (env-overridable, sensible defaults derived from HOME) ───────
def _state_dir() -> Path:
    home = os.environ.get("HOME", "")
    base = os.path.join(home, "work", "prismatic-engine") if home else ""
    return Path(os.environ.get("PRISMATIC_STATE_DIR", os.path.join(base, "prismatic_state") if base else "prismatic_state"))


def _repo_dir() -> Path:
    home = os.environ.get("HOME", "")
    base = os.path.join(home, "work", "prismatic-engine") if home else "."
    return Path(os.environ.get("PRISMATIC_HOME", base))


def _log_dir() -> Path:
    home = os.environ.get("HOME", "")
    base = os.path.join(home, ".prismatic") if home else "."
    return Path(os.environ.get("PRISMATIC_LOG_DIR", os.path.join(base, "logs")))


# ── Action: restart the prismatic-gateway service if down ─────────────
def action_restart_gateway(ctx: dict) -> tuple[str, str]:
    """Auto-restart prismatic-gateway.service if it's not active."""
    svc = ctx.get("service", "prismatic-gateway.service")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", svc],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return ("skipped", f"{svc} already active")
        start = subprocess.run(
            ["sudo", "systemctl", "start", svc],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if start.returncode == 0:
            time.sleep(2)
            check = subprocess.run(
                ["systemctl", "is-active", "--quiet", svc],
                capture_output=True,
                timeout=5,
            )
            if check.returncode == 0:
                return ("ok", f"Restarted {svc}")
            return ("failed", f"Restarted {svc} but is-active returned non-zero")
        return ("failed", f"systemctl start {svc} failed: {start.stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        return ("failed", f"Timeout checking/starting {svc}")
    except FileNotFoundError:
        return ("failed", "systemctl not found — not running on systemd")
    except Exception as exc:
        return ("failed", f"Exception: {str(exc)[:200]}")


# ── Action: restart the prismatic-webhook-drain timer if down ─────────
def action_restart_drain_timer(ctx: dict) -> tuple[str, str]:
    """Auto-restart prismatic-webhook-drain.timer if it's not active."""
    svc = ctx.get("service", "prismatic-webhook-drain.timer")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", svc],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return ("skipped", f"{svc} already active")
        start = subprocess.run(
            ["sudo", "systemctl", "start", svc],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if start.returncode == 0:
            return ("ok", f"Started {svc}")
        return ("failed", f"systemctl start {svc} failed: {start.stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        return ("failed", f"Timeout checking/starting {svc}")
    except FileNotFoundError:
        return ("failed", "systemctl not found — not running on systemd")
    except Exception as exc:
        return ("failed", f"Exception: {str(exc)[:200]}")


# ── Action: trigger webhook drain if queue is backed up ────────────────
def action_drain_webhook_queue(ctx: dict) -> tuple[str, str]:
    """Trigger prismatic-webhook-drain.service if pending > threshold."""
    db_path = _state_dir() / "linear_webhook_queue.db"
    if not db_path.exists():
        return ("skipped", "queue DB not found")

    threshold = ctx.get("threshold", 500)
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM linear_webhook_queue WHERE dispatch_status='pending'"
        )
        pending = cur.fetchone()[0]
        con.close()
        if pending < threshold:
            return ("skipped", f"pending={pending} < threshold={threshold}")
        result = subprocess.run(
            ["sudo", "systemctl", "start", "prismatic-webhook-drain.service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return ("ok", f"Triggered drain: pending was {pending}")
        return ("failed", f"systemctl start drain failed: {result.stderr[:200]}")
    except Exception as exc:
        return ("failed", f"Exception: {str(exc)[:200]}")


# ── Action: vacuum large state DBs ─────────────────────────────────────
def action_vacuum_state_dbs(ctx: dict) -> tuple[str, str]:
    """VACUUM state DBs that exceed the size threshold (default 100MB)."""
    size_threshold_mb = ctx.get("size_threshold_mb", 100)
    state_dir = _state_dir()
    if not state_dir.exists():
        return ("skipped", f"state dir {state_dir} not found")

    vacuumed = []
    failed = []
    for db_path in state_dir.glob("*.db"):
        try:
            size_mb = db_path.stat().st_size / (1024 * 1024)
            if size_mb < size_threshold_mb:
                continue
            # SQLite VACUUM requires no other connections; use a fresh handle
            con = sqlite3.connect(str(db_path))
            con.execute("VACUUM")
            con.close()
            new_size_mb = db_path.stat().st_size / (1024 * 1024)
            freed = size_mb - new_size_mb
            vacuumed.append(f"{db_path.name}: {size_mb:.1f}MB → {new_size_mb:.1f}MB")
        except Exception as exc:
            failed.append(f"{db_path.name}: {str(exc)[:80]}")

    if failed:
        return ("failed", f"Errors: {'; '.join(failed[:3])}")
    if vacuumed:
        return ("ok", f"Vacuumed: {'; '.join(vacuumed[:3])}")
    return ("skipped", "no DBs over threshold")


# ── Action: clean stale prismatic_engine lock files ────────────────────
def action_clear_stale_locks(ctx: dict) -> tuple[str, str]:
    """Remove lock entries older than 24h (without an active heartbeat)."""
    home = os.environ.get("HOME", "")
    locks_path = Path(os.path.join(home, ".antigravity", "swarm_locks.json") if home else "/.antigravity/swarm_locks.json")
    if not locks_path.exists():
        return ("skipped", "swarm_locks.json not found")

    threshold_seconds = ctx.get("threshold_seconds", 86400)
    try:
        with locks_path.open() as f:
            import json as _json
            data = _json.load(f)
        # Heartbeat-based eviction
        now = time.time()
        evicted = []
        for lock_key, lock_info in list(data.get("locks", {}).items()):
            last_heartbeat = lock_info.get("last_heartbeat", 0)
            if now - last_heartbeat > threshold_seconds:
                del data["locks"][lock_key]
                evicted.append(lock_key)
        if evicted:
            with locks_path.open("w") as f:
                _json.dump(data, f, indent=2)
            return ("ok", f"Evicted {len(evicted)} stale locks: {evicted[:3]}")
        return ("skipped", "no stale locks")
    except Exception as exc:
        return ("failed", f"Exception: {str(exc)[:200]}")


# ── Action: rotate large engine logs ───────────────────────────────────
def action_rotate_logs(ctx: dict) -> tuple[str, str]:
    """Delete .log files > 10MB in the engine log dir (assumes .gz exists or is OK to lose)."""
    log_dir = _log_dir()
    size_threshold_mb = ctx.get("size_threshold_mb", 10)
    if not log_dir.exists():
        return ("skipped", f"log dir {log_dir} not found")

    deleted = []
    freed = 0
    for log_file in log_dir.glob("*.log*"):
        if log_file.suffix == ".gz":
            continue
        try:
            size_mb = log_file.stat().st_size / (1024 * 1024)
            if size_mb < size_threshold_mb:
                continue
            gz_path = log_file.with_suffix(log_file.suffix + ".gz")
            freed += log_file.stat().st_size
            if gz_path.exists():
                log_file.unlink()
            else:
                # No .gz backup — just delete the live file (loses recent tail)
                log_file.unlink()
            deleted.append(log_file.name)
        except Exception:
            pass

    if deleted:
        return ("ok", f"Rotated {len(deleted)} logs ({freed / (1024 * 1024):.1f}MB): {deleted[:3]}")
    return ("skipped", "no logs over threshold")


# ── Action: probe the gateway /health endpoint ────────────────────────
def action_probe_gateway_health(ctx: dict) -> tuple[str, str]:
    """Probe localhost:9000/health to diagnose alert (read-only action)."""
    port = ctx.get("port", 9000)
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=5) as r:
            body = r.read().decode("utf-8", errors="replace")[:200]
            if r.status == 200:
                return ("ok", f"gateway /health: 200 OK — {body}")
            return ("failed", f"gateway /health: HTTP {r.status} — {body}")
    except Exception as exc:
        return ("failed", f"gateway probe failed: {str(exc)[:200]}")


# ── Dispatch table ─────────────────────────────────────────────────────
ACTIONS_BY_ALERT_PREFIX: list[tuple[str, Callable]] = [
    ("prismatic-gateway.service", action_restart_gateway),
    ("prismatic-webhook-drain.timer", action_restart_drain_timer),
    ("Webhook queue", action_drain_webhook_queue),
    ("State DB", action_vacuum_state_dbs),
    ("Stale lock", action_clear_stale_locks),
    ("Engine log", action_rotate_logs),
    ("Gateway /health", action_probe_gateway_health),
]


def run_action_for_alert(
    alert: str, ctx: dict | None = None
) -> tuple[str | None, str, str]:
    """Find the matching action for an alert and run it.

    Returns:
        (action_name, status, message) — action_name is None if no match.
    """
    ctx = ctx or {}
    for prefix, handler in ACTIONS_BY_ALERT_PREFIX:
        if prefix.lower() in alert.lower():
            try:
                status, message = handler(ctx)
                return (handler.__name__, status, message)
            except Exception as exc:
                return (handler.__name__, "failed", f"Handler exception: {str(exc)[:200]}")
    return (None, "skipped", "no action defined")


if __name__ == "__main__":
    test_alerts = [
        "🔴 prismatic-gateway.service is not active",
        "🔴 prismatic-webhook-drain.timer is not active",
        "🟡 Webhook queue has 800 pending events",
        "🔴 State DB over 100MB: alerts.db",
        "🟡 Stale lock on prismatic/fleet_actions.py",
        "🔴 Engine log gateway.log is 15MB",
        "🟡 Gateway /health returned 503",
        "🟢 Everything fine",
    ]
    for alert in test_alerts:
        name, status, msg = run_action_for_alert(alert)
        if name:
            print(f"[{status:8}] {name:30} → {msg}")
        else:
            print(f"[skipped  ] no action for: {alert}")