#!/usr/bin/env python3
"""
Prismatic Engine — State DB Retention Policy (GRO-2060)

Runs retention cleanup on prismatic_state/*.db. Policies per table:

| Table             | Retention  | Why |
|-------------------|------------|-----|
| dedup_log         | 14 days    | cycle_id includes timestamp; older cycle rows are stale |
| label_snapshots   | NONE (see note) | had_label() queries are unbounded; deleting breaks dedup |
| durable_events    | 30 days    | event-style data; older is reference-only |
| agy_stall_tracker | 30 days    | stall alerts; older is reference-only |
| telemetry_*       | 90 days    | general telemetry; configurable |

label_snapshots retention is intentionally NOT implemented in this script.
The table's queries (had_label, had_labels) check "ever had a label" with
NO time filter. Truncating would break dispatcher dedup. Options:

  a) Add time-bounded queries (invasive, requires dispatcher.py changes)
  b) Archive to cold storage instead of delete
  c) Accept growth and rely on VACUUM (GRO-2059) to keep file size bounded

GRO-2060 ships (c) as the default. Future work (b) if file exceeds threshold.

Usage:
    python3 purge-retention.py [--dry-run] [--db PATH] [--verbose]

Exit codes:
    0 = clean (or dry-run with no errors)
    1 = error during delete
    2 = misconfiguration
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Default location; can be overridden via --db
DEFAULT_DB = Path("/home/ubuntu/.prismatic/db/event_router.db")

# Retention policy: table -> (days_to_keep, timestamp_column)
# Empty dict entry means "do not touch" (intentional).
RETENTION_POLICIES = {
    # Stale dedup entries (cycle_id-keyed, old ones are noise)
    "dedup_log": (14, "processed_at"),
    # Time-bounded event-style tables
    "durable_events": (30, "timestamp"),
    "agy_stall_tracker": (30, "last_seen"),
    # Telemetry (when populated)
    # "telemetry_loop_events": (90, "ts"),
    # "telemetry_circuit_breakers": (90, "ts"),
    # "telemetry_token_metrics": (90, "ts"),
    # "telemetry_validation_events": (90, "ts"),
    # "telemetry_agent_runs": (90, "ts"),
    # "telemetry_credit_ledger": (90, "ts"),
    # "telemetry_media_artifacts": (90, "ts"),
    # "telemetry_plugin_metrics": (90, "ts"),
    # "gcp_vertex_billing_ledger": (90, "ts"),
    # "gcp_vertex_quota_snapshots": (90, "ts"),
    # "gcp_vertex_balance_checkpoints": (90, "ts"),
    # "gcp_vertex_spend_events": (90, "ts"),
    # "label_snapshots": (None, None),  # INTENTIONAL: see header docstring
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Report what would be deleted without modifying")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"Path to SQLite DB (default: {DEFAULT_DB})")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return p.parse_args()


def get_retention_cutoff_iso(days: int) -> str:
    """ISO 8601 timestamp string for `days` ago (UTC)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat()


def list_tables_to_purge(conn: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """Return list of (table, days_to_keep, ts_column) for tables with retention policy."""
    out = []
    for table, (days, ts_col) in RETENTION_POLICIES.items():
        if days is None or ts_col is None:
            continue
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if cur.fetchone() is None:
            continue  # Table doesn't exist in this DB
        out.append((table, days, ts_col))
    return out


def count_rows_to_delete(conn: sqlite3.Connection, table: str, ts_col: str, cutoff: str) -> int:
    """Count rows older than cutoff."""
    cur = conn.cursor()
    # Safe column-name quoting (sanity check; we control the list)
    cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{ts_col}" < ?', (cutoff,))
    return cur.fetchone()[0]


def delete_old_rows(conn: sqlite3.Connection, table: str, ts_col: str, cutoff: str) -> int:
    """Delete rows older than cutoff. Returns rows deleted."""
    cur = conn.cursor()
    cur.execute(f'DELETE FROM "{table}" WHERE "{ts_col}" < ?', (cutoff,))
    conn.commit()
    return cur.rowcount


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"[purge-retention] DB not found: {args.db}", file=sys.stderr)
        return 2

    log_path = Path("/home/ubuntu/.prismatic/logs/retention-cron.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts}] {msg}"
        print(line)
        with log_path.open("a") as f:
            f.write(line + "\n")

    log(f"=== purge-retention start (dry_run={args.dry_run}) ===")
    log(f"db={args.db}")

    # JSONL report for trend tracking
    report_path = Path("/home/ubuntu/.prismatic/logs/retention-report.jsonl")
    report_lines = []

    try:
        conn = sqlite3.connect(str(args.db))
    except Exception as e:
        log(f"FAILED to open {args.db}: {e}")
        return 1

    try:
        tables = list_tables_to_purge(conn)
        log(f"found {len(tables)} table(s) with retention policy")
        total_deleted = 0
        for table, days, ts_col in tables:
            cutoff = get_retention_cutoff_iso(days)
            try:
                count = count_rows_to_delete(conn, table, ts_col, cutoff)
            except Exception as e:
                log(f"  {table}: COUNT failed — {e}")
                continue
            if count == 0:
                if args.verbose:
                    log(f"  {table}: 0 rows older than {days}d (cutoff={cutoff})")
                report_lines.append(json.dumps({
                    "table": table, "days": days, "ts_col": ts_col,
                    "cutoff": cutoff, "deleted": 0, "dry_run": args.dry_run,
                }))
                continue
            if args.dry_run:
                log(f"  {table}: would delete {count} rows (cutoff={cutoff}, days={days}d)")
            else:
                try:
                    deleted = delete_old_rows(conn, table, ts_col, cutoff)
                    log(f"  {table}: deleted {deleted} rows (cutoff={cutoff}, days={days}d)")
                    total_deleted += deleted
                except Exception as e:
                    log(f"  {table}: DELETE failed — {e}")
                    continue
            report_lines.append(json.dumps({
                "table": table, "days": days, "ts_col": ts_col,
                "cutoff": cutoff, "deleted": count if args.dry_run else total_deleted,
                "dry_run": args.dry_run,
            }))

        log(f"=== purge-retention complete (total deleted: {total_deleted}) ===")
    finally:
        conn.close()

    # Write report
    with report_path.open("a") as f:
        for line in report_lines:
            f.write(line + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())