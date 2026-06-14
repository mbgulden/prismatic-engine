"""
Prismatic Watchdog — health monitoring and auto-recovery for the gateway server.

Checks:
  - Server health endpoint (/health)
  - File lock staleness
  - Run record staleness (stuck agent runs)

Usage:
    # Run a single health check
    python -m prismatic.gateway.watchdog --check

    # Run in monitoring loop (every 30s)
    python -m prismatic.gateway.watchdog --monitor --interval 30
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.watchdog")


# ── Health Check ───────────────────────────────────────


def check_health(base_url: str = "http://localhost:9000") -> dict[str, Any]:
    """Check the gateway server health endpoint."""
    try:
        req = urllib.request.Request(f"{base_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return {"healthy": data.get("status") == "ok", "data": data, "http_status": resp.status}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


def check_locks(base_url: str = "http://localhost:9000") -> dict[str, Any]:
    """Check for stale file locks."""
    try:
        req = urllib.request.Request(f"{base_url}/locks/stale", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return {"stale_count": len(data), "stale_locks": data}
    except Exception as e:
        return {"stale_count": -1, "error": str(e)}


def check_stuck_runs(base_url: str = "http://localhost:9000", max_run_seconds: int = 3600) -> dict[str, Any]:
    """Check for runs stuck in 'running' state for too long."""
    try:
        req = urllib.request.Request(f"{base_url}/runs?status=running", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            now = time.time()
            stuck = []
            for run in data:
                started = run.get("started_at", "")
                if started:
                    try:
                        started_ts = _parse_iso_timestamp(started)
                        elapsed = now - started_ts
                        if elapsed > max_run_seconds:
                            stuck.append({"run_id": run["run_id"], "elapsed_seconds": round(elapsed)})
                    except (ValueError, TypeError):
                        pass
            return {"stuck_count": len(stuck), "stuck_runs": stuck}
    except Exception as e:
        return {"stuck_count": -1, "error": str(e)}


def _parse_iso_timestamp(iso_str: str) -> float:
    """Parse ISO-8601 timestamp string to Unix timestamp (float)."""
    # Handle various ISO formats Python's datetime can parse
    from datetime import datetime

    if "+" in iso_str or iso_str.endswith("Z"):
        # Python 3.11+ supports 'Z' suffix, handle manually for 3.10
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    return time.time()


# ── CLI ────────────────────────────────────────────────


def run_check(base_url: str = "http://localhost:9000") -> int:
    """Run a single health check cycle and print results."""
    health = check_health(base_url)
    locks = check_locks(base_url)
    runs = check_stuck_runs(base_url)

    all_ok = health.get("healthy", False) and locks.get("stale_count", -1) == 0 and runs.get("stuck_count", -1) == 0

    if not all_ok:
        logger.warning("Watchdog check FAILED:")
        if not health.get("healthy"):
            logger.warning("  ❌ Health: %s", health.get("error", "unhealthy"))
        if locks.get("stale_count", 0) > 0:
            logger.warning("  ⚠️  Stale locks: %d", locks["stale_count"])
        if runs.get("stuck_count", 0) > 0:
            logger.warning("  ⚠️  Stuck runs: %d", runs["stuck_count"])
        return 1

    logger.info("Watchdog check PASSED — all systems healthy")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Prismatic Engine Watchdog")
    parser.add_argument("--check", action="store_true", help="Run a single health check")
    parser.add_argument("--monitor", action="store_true", help="Run in monitoring loop")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    parser.add_argument("--base-url", type=str,
                        default=f"http://localhost:{os.environ.get('PRISMATIC_GATEWAY_PORT', os.environ.get('PRISMATIC_PORT', '9000'))}",
                        help="Gateway base URL")
    parser.add_argument("--log-level", type=str, default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.monitor:
        logger.info("Watchdog monitoring started (interval=%ds, target=%s)", args.interval, args.base_url)
        while True:
            try:
                run_check(args.base_url)
            except Exception as e:
                logger.error("Watchdog check failed: %s", e)
            time.sleep(args.interval)
    else:
        sys.exit(run_check(args.base_url))


if __name__ == "__main__":
    main()
