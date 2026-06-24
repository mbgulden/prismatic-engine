"""
prismatic.fleet_watchdog — Standalone Prismatic Engine fleet health monitor.

Runs WITHOUT Hermes, AGY, Linear, or any orchestrator. Pure Python + stdlib.

Detection covers what the engine itself owns:
  - prismatic-gateway.service (webhook receiver, IPC bridge, WebSocket)
  - prismatic-webhook-drain.timer (the drain cron from GRO-2391)
  - linear_webhook_queue.db pending count
  - prismatic_state/*.db sizes (vacuum when >100MB)
  - Stale lock entries (>24h without heartbeat)
  - Engine log files >10MB
  - Gateway /health endpoint response

NOT covered (out of scope for the engine):
  - AGY processes (that's agentic-swarm-ops/agy_watchdog)
  - OAuth tokens (handled by hermes/linear_oauth_refresh.sh)
  - GPU cluster health (handled by agentic-swarm-ops)

Usage:
  # As a script (cron-compatible, stdout = alert report)
  python3 -m prismatic.fleet_watchdog

  # As a CLI subcommand
  prismatic fleet-watchdog
  prismatic fleet-watchdog --dry-run    # don't take actions
  prismatic fleet-watchdog --json       # machine-readable output

  # Silent on green: exit 0, empty stdout
  # Alert + action: exit 0/1, structured report on stdout

Architecture:
  main() → check_all() → list[(status_lines, alerts)]
  main() → for each alert: run auto-action via fleet_actions
  main() → render report (silent on green)
  main() → return exit code based on action outcomes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import NamedTuple

# Allow `python3 -m prismatic.fleet_watchdog` from anywhere
_PKG_PARENT = Path(__file__).resolve().parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from prismatic.fleet_actions import run_action_for_alert  # noqa: E402


# ── Constants ──────────────────────────────────────────────────────────
STATUS_PREFIX = "🟢"
ALERT_PREFIXES = ("🟡", "🔴")

# Defaults derived from HOME so the engine works on any host. Override via
# PRISMATIC_STATE_DIR / PRISMATIC_HOME / PRISMATIC_LOG_DIR at runtime.
_HOME = os.environ.get("HOME", "")
_STATE_BASE = os.path.join(_HOME, "work", "prismatic-engine") if _HOME else "."
_LOG_BASE = os.path.join(_HOME, ".prismatic") if _HOME else "."

DEFAULT_STATE_DIR = os.path.join(_STATE_BASE, "prismatic_state")
DEFAULT_REPO_DIR = _STATE_BASE
DEFAULT_LOG_DIR = os.path.join(_LOG_BASE, "logs")
DEFAULT_GATEWAY_PORT = 9000

WEBHOOK_QUEUE_PENDING_THRESHOLD = 500
STATE_DB_SIZE_THRESHOLD_MB = 100
LOG_SIZE_THRESHOLD_MB = 10
LOCK_STALE_SECONDS = 86400  # 24h


class CheckResult(NamedTuple):
    """Result of a single health check."""
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    actionable: bool  # True if there's an auto-action to take


def _state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", DEFAULT_STATE_DIR))


def _repo_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_HOME", DEFAULT_REPO_DIR))


def _log_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_LOG_DIR", DEFAULT_LOG_DIR))


# ── Check: systemd service active? ─────────────────────────────────────
def check_service(service: str) -> CheckResult:
    """Check if a systemd service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return CheckResult(f"service:{service}", "ok", f"{service} active", False)
        return CheckResult(
            f"service:{service}", "fail",
            f"prismatic-gateway.service not active" if "gateway" in service
            else f"{service} not active",
            True,  # actionable — auto-restart
        )
    except FileNotFoundError:
        return CheckResult(f"service:{service}", "warn",
                           "systemctl not found — not on systemd", False)
    except subprocess.TimeoutExpired:
        return CheckResult(f"service:{service}", "fail",
                           f"Timeout checking {service}", False)
    except Exception as exc:
        return CheckResult(f"service:{service}", "warn",
                           f"check failed: {str(exc)[:100]}", False)


# ── Check: gateway /health responds ────────────────────────────────────
def check_gateway_health(port: int = DEFAULT_GATEWAY_PORT) -> CheckResult:
    """Probe localhost:<port>/health."""
    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=5
        ) as r:
            if r.status == 200:
                return CheckResult("gateway:health", "ok",
                                   "Gateway /health 200", False)
            return CheckResult("gateway:health", "fail",
                               f"Gateway /health HTTP {r.status}", False)
    except Exception as exc:
        return CheckResult("gateway:health", "fail",
                           f"Gateway /health unreachable: {str(exc)[:100]}",
                           False)


# ── Check: webhook queue pending count ─────────────────────────────────
def check_webhook_queue() -> CheckResult:
    """Count pending events in linear_webhook_queue.db."""
    db_path = _state_dir() / "linear_webhook_queue.db"
    if not db_path.exists():
        return CheckResult("webhook:queue", "ok",
                           "queue DB not yet created (fresh install)", False)
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM linear_webhook_queue WHERE dispatch_status='pending'"
        )
        pending = cur.fetchone()[0]
        con.close()
        if pending >= WEBHOOK_QUEUE_PENDING_THRESHOLD:
            return CheckResult(
                "webhook:queue", "fail",
                f"Webhook queue has {pending} pending events (threshold: {WEBHOOK_QUEUE_PENDING_THRESHOLD})",
                True,
            )
        return CheckResult("webhook:queue", "ok",
                           f"Webhook queue {pending} pending", False)
    except Exception as exc:
        return CheckResult("webhook:queue", "warn",
                           f"queue check failed: {str(exc)[:100]}", False)


# ── Check: state DB sizes ──────────────────────────────────────────────
def check_state_db_sizes() -> CheckResult:
    """Find any state DB over the size threshold."""
    state_dir = _state_dir()
    if not state_dir.exists():
        return CheckResult("state:db_sizes", "ok",
                           "state dir not yet created", False)
    oversized = []
    for db_path in state_dir.glob("*.db"):
        try:
            size_mb = db_path.stat().st_size / (1024 * 1024)
            if size_mb >= STATE_DB_SIZE_THRESHOLD_MB:
                oversized.append(f"{db_path.name} ({size_mb:.0f}MB)")
        except Exception:
            pass
    if oversized:
        return CheckResult(
            "state:db_sizes", "warn",
            f"State DB over {STATE_DB_SIZE_THRESHOLD_MB}MB: {', '.join(oversized[:3])}",
            True,
        )
    return CheckResult("state:db_sizes", "ok", "All state DBs within size", False)


# ── Check: stale swarm lock entries ────────────────────────────────────
def check_stale_locks() -> CheckResult:
    """Check for lock entries without heartbeat >24h."""
    _home = os.environ.get("HOME", "")
    locks_path = Path(os.path.join(_home, ".antigravity", "swarm_locks.json") if _home else "/.antigravity/swarm_locks.json")
    if not locks_path.exists():
        return CheckResult("locks:stale", "ok", "no lock registry", False)
    try:
        with locks_path.open() as f:
            data = json.load(f)
        # Schema may be a list of entries or a dict {"locks": {...}}
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = list(data.get("locks", {}).items())
            # Normalize to (key, info) tuples
            entries = [(k, v) for k, v in entries]
        else:
            return CheckResult("locks:stale", "ok",
                               f"unknown lock schema: {type(data).__name__}", False)
        if not entries:
            return CheckResult("locks:stale", "ok", "no active locks", False)
        now = time.time()
        stale = []
        for entry in entries:
            if isinstance(entry, tuple):
                lock_key, lock_info = entry
            else:
                # list-of-dicts format
                lock_key = entry.get("path") or entry.get("key") or "?"
                lock_info = entry
            last_heartbeat = lock_info.get("last_heartbeat", 0)
            if now - last_heartbeat > LOCK_STALE_SECONDS:
                stale.append(lock_key)
        if stale:
            return CheckResult(
                "locks:stale", "warn",
                f"Stale lock entries (>{LOCK_STALE_SECONDS // 3600}h): {', '.join(stale[:3])}",
                True,
            )
        return CheckResult("locks:stale", "ok",
                           f"{len(entries)} active lock(s), none stale", False)
    except Exception as exc:
        return CheckResult("locks:stale", "warn",
                           f"lock check failed: {str(exc)[:100]}", False)


# ── Check: engine log file sizes ───────────────────────────────────────
def check_log_sizes() -> CheckResult:
    """Find any .log file > 10MB that should be rotated."""
    log_dir = _log_dir()
    if not log_dir.exists():
        return CheckResult("logs:sizes", "ok", "no log dir", False)
    oversized = []
    for log_file in log_dir.glob("*.log*"):
        if log_file.suffix == ".gz":
            continue
        try:
            size_mb = log_file.stat().st_size / (1024 * 1024)
            if size_mb >= LOG_SIZE_THRESHOLD_MB:
                oversized.append(f"{log_file.name} ({size_mb:.0f}MB)")
        except Exception:
            pass
    if oversized:
        return CheckResult(
            "logs:sizes", "warn",
            f"Engine log over {LOG_SIZE_THRESHOLD_MB}MB: {', '.join(oversized[:3])}",
            True,
        )
    return CheckResult("logs:sizes", "ok", "all logs within size", False)


# ── Run all checks ─────────────────────────────────────────────────────
def check_all() -> list[CheckResult]:
    """Run every health check and return results."""
    return [
        check_service("prismatic-gateway.service"),
        check_service("prismatic-webhook-drain.timer"),
        check_gateway_health(),
        check_webhook_queue(),
        check_state_db_sizes(),
        check_stale_locks(),
        check_log_sizes(),
    ]


# ── Format helpers ─────────────────────────────────────────────────────
def _format_result(r: CheckResult) -> str:
    icon = {"ok": STATUS_PREFIX, "warn": "🟡", "fail": "🔴"}[r.status]
    return f"{icon} {r.message}"


def _extract_ctx(alert_msg: str) -> dict:
    """Pull structured context out of an alert message."""
    ctx: dict = {}
    m = re.search(r"threshold:\s*(\d+)", alert_msg)
    if m:
        ctx["threshold"] = int(m.group(1))
    m = re.search(r"(\d+)\s*pending", alert_msg)
    if m:
        ctx["threshold"] = max(100, int(m.group(1)) // 2)
    m = re.search(r"([\w-]+\.service|[\w-]+\.timer)", alert_msg)
    if m:
        ctx["service"] = m.group(1)
    return ctx


# ── Render report ──────────────────────────────────────────────────────
def render_report(
    results: list[CheckResult], dry_run: bool = False
) -> tuple[str, int]:
    """Build the structured alert+action report.

    Returns:
        (report_text, failed_action_count)
    """
    # Split into status vs alerts
    status_lines = [r for r in results if r.status == "ok"]
    alerts = [r for r in results if r.status in ("warn", "fail")]

    if not alerts:
        return "", 0  # Silent on green

    has_red = any(r.status == "fail" for r in alerts)
    overall = "🔴 red" if has_red else "🟡 yellow"

    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    lines = [
        f"🛰️ Prismatic Engine Fleet Watchdog — {now}",
        f"Status: {overall}",
        f"Alerts: {len(alerts)} (healthy checks: {len(status_lines)})",
        f"Dry-run: {dry_run}",
        "",
    ]

    failed = 0
    for alert in alerts:
        formatted = _format_result(alert)
        lines.append(formatted)
        ctx = _extract_ctx(alert.message)
        if dry_run:
            lines.append("   → 🧪 dry-run (no action taken)")
        else:
            name, status_str, msg = run_action_for_alert(formatted, ctx)
            if name:
                icon = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}.get(
                    status_str, "?"
                )
                lines.append(f"   → {icon} action: {name}")
                lines.append(f"     {msg}")
                if status_str == "failed":
                    failed += 1
            else:
                lines.append("   → ⏭️ no auto-action (manual review needed)")
        lines.append("")

    # Tail of healthy checks for context
    if status_lines:
        lines.append("Healthy checks:")
        for r in status_lines[-3:]:
            lines.append(f"  {_format_result(r)}")

    return "\n".join(lines), failed


# ── JSON output (machine-readable) ────────────────────────────────────
def render_json(results: list[CheckResult]) -> str:
    """Machine-readable output for monitoring systems."""
    return json.dumps(
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "results": [
                {
                    "name": r.name,
                    "status": r.status,
                    "message": r.message,
                    "actionable": r.actionable,
                }
                for r in results
            ],
            "summary": {
                "ok": sum(1 for r in results if r.status == "ok"),
                "warn": sum(1 for r in results if r.status == "warn"),
                "fail": sum(1 for r in results if r.status == "fail"),
            },
        },
        indent=2,
    )


# ── Main entry point ───────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prismatic Engine fleet health watchdog (standalone)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't take auto-actions, just report"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output (no actions)"
    )
    args = parser.parse_args()

    results = check_all()

    if args.json:
        print(render_json(results))
        return 0

    report, failed = render_report(results, dry_run=args.dry_run)
    if report:
        print(report)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())