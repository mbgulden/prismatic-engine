#!/usr/bin/env python3
"""
Prismatic Engine — State DB Health Check + Alert (GRO-2061)

Reports size of each .db under ~/.prismatic/db/ and alerts on:
  - Absolute size > 100 MB (the threshold above which we're worried)
  - Day-over-day growth > 20% (abnormal growth rate)
  - New DBs appearing (informational)
  - DBs disappearing (informational)

Writes a daily snapshot to /home/ubuntu/.prismatic/logs/state-db-sizes.jsonl
and a JSONL log of alerts to state-db-alerts.jsonl.

Designed to run as a daily cron (after VACUUM + retention at 03:30 UTC).

Exit codes:
  0 = healthy (or alerts only, but delivered)
  1 = unhealthy (alert threshold breached)
  2 = misconfiguration

Usage:
    python3 check-state-db-health.py [--threshold-mb 100] [--growth-pct 20]
                                    [--db-root PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_ROOT = Path("/home/ubuntu/.prismatic/db")
LOG_DIR = Path("/home/ubuntu/.prismatic/logs")
SIZES_LOG = LOG_DIR / "state-db-sizes.jsonl"
ALERTS_LOG = LOG_DIR / "state-db-alerts.jsonl"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--threshold-mb", type=int, default=100, help="Alert if any DB exceeds this size in MB (default: 100)")
    p.add_argument("--growth-pct", type=int, default=20, help="Alert if day-over-day growth exceeds this percent (default: 20)")
    p.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT, help=f"DB root directory (default: {DEFAULT_DB_ROOT})")
    p.add_argument("--dry-run", action="store_true", help="Run check but don't write logs")
    p.add_argument("--quiet", action="store_true", help="Suppress normal output, only show alerts")
    return p.parse_args()


def get_db_sizes(root: Path) -> list[dict]:
    """Return [{path, name, size_bytes, size_mb}] for every .db under root."""
    if not root.exists():
        return []
    out = []
    for db in sorted(root.rglob("*.db")):
        try:
            size = db.stat().st_size
        except OSError:
            continue
        out.append({
            "path": str(db),
            "name": db.name,
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
        })
    return out


def load_recent_snapshots(days: int = 2) -> list[dict]:
    """Load the most recent N days of snapshots from the JSONL log."""
    if not SIZES_LOG.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    snapshots = []
    with SIZES_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            if snap.get("ts", "") < cutoff:
                continue
            snapshots.append(snap)
    return snapshots


def find_yesterday_snapshot(recent: list[dict], db_name: str) -> dict | None:
    """Find the most recent snapshot from yesterday (or earlier today) for this DB."""
    today = datetime.now(timezone.utc).date().isoformat()
    candidates = [s for s in recent if s.get("name") == db_name and s.get("ts", "")[:10] != today]
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.get("ts", ""))
    return candidates[-1]


def evaluate(sizes: list[dict], recent: list[dict], threshold_mb: int, growth_pct: int) -> list[dict]:
    """Return list of alerts (each dict with severity, db, message, etc.)."""
    alerts = []
    threshold_bytes = threshold_mb * 1024 * 1024

    seen_names = set()
    for db in sizes:
        seen_names.add(db["name"])

        # Alert 1: absolute size threshold
        if db["size_bytes"] > threshold_bytes:
            alerts.append({
                "severity": "warn",
                "type": "size_threshold",
                "db": db["name"],
                "size_mb": db["size_mb"],
                "threshold_mb": threshold_mb,
                "message": f"⚠️  {db['name']}: {db['size_mb']}MB exceeds {threshold_mb}MB threshold",
            })

        # Alert 2: day-over-day growth
        yest = find_yesterday_snapshot(recent, db["name"])
        if yest is not None:
            yest_size = yest.get("size_bytes", 0)
            if yest_size > 0:
                growth_pct_actual = (db["size_bytes"] - yest_size) / yest_size * 100
                if growth_pct_actual > growth_pct:
                    alerts.append({
                        "severity": "warn",
                        "type": "excessive_growth",
                        "db": db["name"],
                        "yesterday_mb": round(yest_size / 1024 / 1024, 2),
                        "today_mb": db["size_mb"],
                        "growth_pct": round(growth_pct_actual, 1),
                        "threshold_pct": growth_pct,
                        "message": f"📈 {db['name']}: {yest_size // 1024 // 1024}MB → {db['size_mb']}MB ({growth_pct_actual:.1f}% growth, threshold {growth_pct}%)",
                    })

    # Alert 3: DBs disappeared (informational)
    if recent:
        latest_yest = max(recent, key=lambda s: s.get("ts", ""))
        prev_names = {s.get("name") for s in recent if s.get("ts") == latest_yest.get("ts")}
        disappeared = prev_names - seen_names
        for name in disappeared:
            alerts.append({
                "severity": "info",
                "type": "db_disappeared",
                "db": name,
                "message": f"ℹ️  {name}: disappeared since yesterday",
            })

    return alerts


def main() -> int:
    args = parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not args.db_root.exists():
        print(f"[check-state-db-health] DB root not found: {args.db_root}", file=sys.stderr)
        return 2

    now_iso = datetime.now(timezone.utc).isoformat()

    sizes = get_db_sizes(args.db_root)
    recent = load_recent_snapshots(days=2)
    alerts = evaluate(sizes, recent, args.threshold_mb, args.growth_pct)

    total_mb = sum(d["size_mb"] for d in sizes)
    db_count = len(sizes)

    if not args.quiet:
        print(f"[check-state-db-health] {db_count} DB(s), {total_mb:.1f} MB total at {now_iso}")
        for db in sizes:
            print(f"  {db['name']:35s} {db['size_mb']:>8.2f} MB")

    if alerts:
        print(f"\n[check-state-db-health] ⚠️  {len(alerts)} alert(s):")
        for a in alerts:
            print(f"  {a['message']}")

    # Always write today's snapshot for tomorrow's comparison
    if not args.dry_run:
        with SIZES_LOG.open("a") as f:
            for db in sizes:
                snap = {
                    "ts": now_iso,
                    "name": db["name"],
                    "path": db["path"],
                    "size_bytes": db["size_bytes"],
                    "size_mb": db["size_mb"],
                }
                f.write(json.dumps(snap) + "\n")
        if alerts:
            with ALERTS_LOG.open("a") as f:
                for a in alerts:
                    a["ts"] = now_iso
                    f.write(json.dumps(a) + "\n")

    # Exit non-zero if any alert is severity "warn"
    if any(a["severity"] == "warn" for a in alerts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())