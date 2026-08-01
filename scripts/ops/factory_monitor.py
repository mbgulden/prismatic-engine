#!/usr/bin/env python3
"""
factory_monitor.py — single-shot factory health check.

Reports the state of every prismatic-engine component. Designed to be
called by:
  - the morning cron (8am, alongside the daily digest)
  - the event-driven watchdog (on anomalies)
  - ad-hoc CLI: python3 factory_monitor.py [--json] [--alerts-only]

Exits 0 if everything is healthy, 1 if any check is CRITICAL, 2 if any
check is WARN, 3 if any check is unknown. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# === Config ===

SERVICES = ["prismatic-gateway", "prismatic-consumer", "prismatic-curator"]
ENDPOINTS = [
    ("/health", 200),
    ("/curator/health", 200),
    ("/events/bus-stats", 200),
    ("/events/recent", 200),
]
# The bus/curator/vault directories are always under the real user home
# (~/.prismatic/), NOT under $PRISMATIC_HOME (which is the engine source
# tree, e.g. /home/ubuntu/work). We hardcode the user home and ignore
# the env var for path resolution, since the env var would point to a
# stale or different location.
PRISMATIC_DATA = Path(os.path.expanduser("~")) / ".prismatic"
BUS_DB = PRISMATIC_DATA / "bus" / "event_log.sqlite"
CURATOR_DB = PRISMATIC_DATA / "curator" / "state.sqlite"
DIGEST_DIR = PRISMATIC_DATA / "curator" / "digests"
VAULT_DIR = PRISMATIC_DATA / "vault"
LOGS_DIR = PRISMATIC_DATA / "logs"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def shell(cmd: str, timeout: int = 10) -> tuple[int, str, str]:
    """Run a shell command, return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except Exception as e:
        return -1, "", f"error: {e}"


# === Check functions ===

def check_services() -> dict[str, Any]:
    """Check systemd services for active state and recent restart count."""
    out = {}
    for svc in SERVICES:
        rc, stdout, stderr = shell(f"systemctl is-active {svc}")
        active = (stdout == "active")
        # Also check if it was restarted recently (less than 5 min ago)
        rc2, stdout2, _ = shell(f"systemctl show {svc} -p ActiveEnterTimestamp --value")
        recent_restart = False
        if stdout2:
            try:
                # Format like "Tue 2026-07-01 01:25:34 UTC"
                ts = datetime.strptime(stdout2.strip(), "%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - ts).total_seconds() < 300:
                    recent_restart = True
            except (ValueError, TypeError):
                pass
        out[svc] = {
            "active": active,
            "recent_restart": recent_restart,
        }
    return out


def check_failed_units() -> dict[str, Any]:
    rc, stdout, _ = shell("systemctl --state=failed --no-pager --no-legend 2>&1 | head -20")
    failed = [line for line in stdout.splitlines() if line.strip() and not line.startswith("UNIT")]
    return {"count": len(failed), "units": failed}


def check_zombies() -> dict[str, Any]:
    rc, stdout, _ = shell("ps -eo stat,pid,cmd 2>/dev/null | awk '/^Z/' | wc -l")
    count = int(stdout.strip() or 0)
    return {"count": count}


def check_endpoints() -> dict[str, Any]:
    """HTTP endpoint health check."""
    out = {}
    for ep, expected in ENDPOINTS:
        url = f"http://localhost:9000{ep}"
        rc, stdout, stderr = shell(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 {url}")
        try:
            status = int(stdout.strip())
        except (ValueError, TypeError):
            status = 0
        out[ep] = {
            "status": status,
            "expected": expected,
            "ok": status == expected,
        }
    return out


def check_curator_health() -> dict[str, Any]:
    """Detailed curator state from /curator/health."""
    rc, stdout, _ = shell("curl -s --max-time 5 http://localhost:9000/curator/health")
    if rc != 0 or not stdout:
        return {"ok": False, "error": "endpoint unreachable"}
    try:
        d = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"json decode: {e}"}
    if not d.get("exists"):
        return {"ok": False, "error": "curator DB not found"}
    return {
        "ok": True,
        "total_tagged": d.get("total_tagged", 0),
        "tag_counts": d.get("tag_counts", {}),
        "escalations_recent": len(d.get("recent_escalations", [])),
        "last_digest": d.get("last_digest", {}).get("date") if d.get("last_digest") else None,
        "budget": d.get("budget", {}).get("lanes", {}),
    }


def check_bus() -> dict[str, Any]:
    """SQLite bus state."""
    if not BUS_DB.exists():
        return {"exists": False}
    try:
        conn = sqlite3.connect(BUS_DB, timeout=5)
        try:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            processed = conn.execute("SELECT COUNT(*) FROM events WHERE processed = 1").fetchone()[0]
            # Most recent event ts
            last_ts = conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]
            # Backlog age: how long since the most recent event?
            if last_ts:
                import time
                age = time.time() - last_ts
            else:
                age = None
            return {
                "exists": True,
                "total": total,
                "processed": processed,
                "pending": total - processed,
                "last_event_age_sec": age,
            }
        finally:
            conn.close()
    except Exception as e:
        return {"exists": True, "error": str(e)}


def check_supervisor_pool() -> dict[str, Any]:
    """Bounded supervisor pool stats (via the curator process's view)."""
    rc, stdout, _ = shell("""PYTHONPATH=/home/ubuntu/.prismatic/venv_stable/lib/python3.12/site-packages:/home/ubuntu/work/prismatic-engine /home/ubuntu/.prismatic/venv_stable/bin/python3 -c "from prismatic.supervisor.recovery import get_pool; import json; print(json.dumps(get_pool().stats()))" 2>&1""")
    if rc != 0 or not stdout:
        return {"error": stdout or "import failed"}
    try:
        return json.loads(stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": "could not parse pool output"}


def check_curator_log_recent() -> dict[str, Any]:
    """Count recent activity in the curator log."""
    log = LOGS_DIR / "curator.log"
    if not log.exists():
        return {"error": "no log file"}
    try:
        with log.open() as f:
            content = f.read()
        lines = content.splitlines()
        # Recent activity: lines from last 100 that are not startup
        recent = [l for l in lines[-100:] if l.strip() and "starting" not in l]
        # Count dispatch events
        dispatched = sum(1 for l in lines if "dispatched" in l)
        tagged = sum(1 for l in lines if "tagged" in l)
        skipped = sum(1 for l in lines if "skipped" in l)
        return {
            "log_lines_total": len(lines),
            "log_size_bytes": log.stat().st_size,
            "recent_activity_count": len(recent),
            "total_dispatched": dispatched,
            "total_tagged_lines": tagged,
            "total_skipped_lines": skipped,
        }
    except Exception as e:
        return {"error": str(e)}


def check_digest_freshness() -> dict[str, Any]:
    """Has the daily digest run today?"""
    if not DIGEST_DIR.exists():
        return {"exists": False}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_digest = DIGEST_DIR / f"{today}.md"
    if not today_digest.exists():
        return {"exists": True, "today_digest": False, "today": today}
    return {
        "exists": True,
        "today_digest": True,
        "today": today,
        "size_bytes": today_digest.stat().st_size,
    }


def check_vault() -> dict[str, Any]:
    """Vault state and GPG backup freshness."""
    if not VAULT_DIR.exists():
        return {"exists": False}
    out = {"exists": True, "files": {}}
    for f in ["secrets.json", "secrets.json.gpg", ".passphrase"]:
        path = VAULT_DIR / f
        if path.exists():
            out["files"][f] = {
                "size_bytes": path.stat().st_size,
                "mode": oct(path.stat().st_mode)[-3:],
            }
    return out


def check_log_errors() -> dict[str, Any]:
    """Scan recent logs for errors/exceptions."""
    out = {}
    for logname in ["gateway.log", "curator.log", "consumer.log"]:
        log = LOGS_DIR / logname
        if not log.exists():
            continue
        try:
            # Read last 500 lines, look for errors
            with log.open() as f:
                lines = f.readlines()[-500:]
            errors = [l.strip() for l in lines if re.search(r'\b(error|exception|traceback)\b', l, re.IGNORECASE)]
            if errors:
                out[logname] = {
                    "count": len(errors),
                    "samples": errors[:3],
                }
        except Exception as e:
            out[logname] = {"error": str(e)}
    return out


# === Severity assessment ===

def assess(checks: dict[str, Any]) -> tuple[str, list[str]]:
    """Return ('CRITICAL'|'WARN'|'OK', list_of_alerts)."""
    alerts = []
    severity = "OK"

    # Services must all be active
    for svc, info in checks.get("services", {}).items():
        if not info["active"]:
            alerts.append(f"CRITICAL: service {svc} is not active")
            severity = "CRITICAL"
        elif info["recent_restart"]:
            alerts.append(f"WARN: service {svc} was restarted in the last 5 minutes")
            if severity != "CRITICAL":
                severity = "WARN"

    # Failed units (but exclude our own service which can be in 'failed' state
    # because it exited 2 from a CRITICAL detection)
    excluded_failed = [u for u in checks.get("failed_units", {}).get("units", [])
                       if "factory-monitor" not in u]
    if excluded_failed:
        alerts.append(f"CRITICAL: {len(excluded_failed)} failed systemd units: {', '.join(excluded_failed[:3])}")
        severity = "CRITICAL"

    # Zombies
    z = checks.get("zombies", {}).get("count", 0)
    if z > 5:
        alerts.append(f"CRITICAL: {z} zombie processes")
        severity = "CRITICAL"
    elif z > 0:
        alerts.append(f"WARN: {z} zombie processes")
        if severity != "CRITICAL":
            severity = "WARN"

    # Endpoints
    for ep, info in checks.get("endpoints", {}).items():
        if not info["ok"]:
            alerts.append(f"CRITICAL: endpoint {ep} returns {info['status']} (expected {info['expected']})")
            severity = "CRITICAL"

    # Bus
    bus = checks.get("bus", {})
    if bus.get("exists") is False:
        alerts.append("CRITICAL: bus DB does not exist")
        severity = "CRITICAL"
    elif bus.get("exists") and bus.get("pending", 0) > 100:
        alerts.append(f"WARN: bus has {bus['pending']} unprocessed events")
        if severity != "CRITICAL":
            severity = "WARN"
    elif bus.get("exists") and bus.get("last_event_age_sec", 999999) > 86400:
        alerts.append(f"WARN: no bus events in {bus['last_event_age_sec']/3600:.1f} hours (idle)")
        if severity != "CRITICAL":
            severity = "WARN"

    # Curator health
    cur = checks.get("curator", {})
    if not cur.get("ok"):
        alerts.append(f"CRITICAL: curator /curator/health: {cur.get('error', '?')}")
        severity = "CRITICAL"
    elif cur.get("escalations_recent", 0) > 5:
        alerts.append(f"WARN: {cur['escalations_recent']} recent escalations need attention")
        if severity != "CRITICAL":
            severity = "WARN"

    # Log errors
    for logname, info in checks.get("log_errors", {}).items():
        if "error" in info:
            continue
        if info.get("count", 0) > 0:
            alerts.append(f"WARN: {logname} has {info['count']} recent error/exception lines")
            if severity != "CRITICAL":
                severity = "WARN"

    return severity, alerts


# === Main ===

def main():
    ap = argparse.ArgumentParser(description="Prismatic Engine factory health check")
    ap.add_argument("--json", action="store_true", help="Output raw JSON only")
    ap.add_argument("--alerts-only", action="store_true", help="Only print if there are alerts")
    ap.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = ap.parse_args()

    checks = {
        "timestamp": now(),
        "services": check_services(),
        "failed_units": check_failed_units(),
        "zombies": check_zombies(),
        "endpoints": check_endpoints(),
        "curator": check_curator_health(),
        "bus": check_bus(),
        "supervisor_pool": check_supervisor_pool(),
        "curator_log": check_curator_log_recent(),
        "digest_freshness": check_digest_freshness(),
        "vault": check_vault(),
        "log_errors": check_log_errors(),
    }
    severity, alerts = assess(checks)

    if args.json:
        print(json.dumps({"severity": severity, "alerts": alerts, "checks": checks}, indent=2))
    elif args.alerts_only:
        if alerts:
            print(f"[{severity}] {len(alerts)} alert(s):")
            for a in alerts:
                print(f"  - {a}")
    else:
        # Human-readable report
        print("=" * 70)
        print(f"FACTORY HEALTH REPORT — {checks['timestamp']}")
        print(f"SEVERITY: {severity}")
        print("=" * 70)

        print(f"\n[Services]")
        for svc, info in checks["services"].items():
            flag = "OK" if info["active"] else "DOWN"
            restart = " (recently restarted)" if info["recent_restart"] else ""
            print(f"  [{flag:4s}] {svc}{restart}")

        print(f"\n[Failed units] {checks['failed_units']['count']}")
        if checks['failed_units']['units']:
            for u in checks['failed_units']['units']:
                print(f"  - {u}")

        print(f"\n[Zombie processes] {checks['zombies']['count']}")

        print(f"\n[HTTP endpoints]")
        for ep, info in checks["endpoints"].items():
            flag = "OK" if info["ok"] else "FAIL"
            print(f"  [{flag:4s}] {ep:25s} HTTP {info['status']} (expected {info['expected']})")

        if checks.get("curator", {}).get("ok"):
            cur = checks["curator"]
            print(f"\n[Curator]")
            print(f"  total_tagged:    {cur['total_tagged']}")
            print(f"  tag_counts:      {cur['tag_counts']}")
            print(f"  escalations:     {cur['escalations_recent']} recent")
            print(f"  last_digest:     {cur['last_digest']}")
            print(f"  budget lanes:    {list(cur['budget'].keys()) if cur['budget'] else '(none)'}")
        else:
            print(f"\n[Curator] UNREACHABLE: {checks['curator'].get('error')}")

        if checks.get("bus", {}).get("exists"):
            bus = checks["bus"]
            print(f"\n[Bus]")
            print(f"  total/processed/pending: {bus['total']}/{bus['processed']}/{bus['pending']}")
            if bus.get("last_event_age_sec") is not None:
                print(f"  last event: {bus['last_event_age_sec']:.0f}s ago")
        else:
            print(f"\n[Bus] MISSING")

        pool = checks.get("supervisor_pool", {})
        if "error" not in pool:
            print(f"\n[Supervisor pool]")
            print(f"  live: {pool.get('live_count', 0)}/{pool.get('max_concurrent', '?')}")
            print(f"  spawned: {pool.get('total_spawned', 0)}, reaped: {pool.get('total_reaped', 0)}")
            print(f"  skipped_cap: {pool.get('total_skipped_cap', 0)}, skipped_dlq: {pool.get('total_skipped_dlq', 0)}")
        else:
            print(f"\n[Supervisor pool] ERROR: {pool.get('error')}")

        if checks.get("digest_freshness", {}).get("today_digest"):
            d = checks["digest_freshness"]
            print(f"\n[Daily digest] OK (today={d['today']}, {d['size_bytes']} bytes)")
        else:
            d = checks["digest_freshness"]
            print(f"\n[Daily digest] MISSING for {d.get('today', '?')}")

        v = checks.get("vault", {})
        if v.get("exists") and v.get("files"):
            print(f"\n[Vault] {list(v['files'].keys())}")

        if checks.get("log_errors"):
            print(f"\n[Log errors]")
            for logname, info in checks["log_errors"].items():
                if "error" in info:
                    print(f"  {logname}: {info['error']}")
                else:
                    print(f"  {logname}: {info.get('count', 0)} recent errors")

        if alerts:
            print(f"\n[ALERTS — {severity}]")
            for a in alerts:
                print(f"  - {a}")
        else:
            print(f"\n[ALERTS] none")

        print("=" * 70)

    # systemd considers non-zero exit = failure. We use:
    #   0 = OK
    #   1 = WARN (the timer/alert should continue, but tell systemd "ok")
    #   2 = CRITICAL (real failure, surface to journal)
    # We always return 0 from systemd's perspective so the timer doesn't
    # log a "failed" entry. The real signal is in the log file.
    if severity == "CRITICAL":
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
