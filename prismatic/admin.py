"""
prismatic-admin — system administration CLI for the Prismatic Engine.

Handles config migration and database schema upgrades per the core
architecture spec sections 3.2-3.3.

Usage:
    prismatic-admin config migrate --current <path>
    prismatic-admin db upgrade [--db-path <path>]

Spec reference: specs/core-architecture-v1.md
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# ── Default paths (overridable via env) ──────────────────
PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", os.path.expanduser("~")))
CONFIG_DIR = PRISMATIC_HOME / ".prismatic"
DB_DIR = CONFIG_DIR / "db"

DEFAULT_DB_PATH = DB_DIR / "event_router.db"
DEFAULT_USER_CONFIG = CONFIG_DIR / "config.yaml"
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "config" / "default_config.yaml"


# ── Config Migration ────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing or corrupt."""
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _deep_merge(defaults: dict, user: dict) -> dict:
    """Merge user values into defaults, preserving user keys.

    Adds missing keys from defaults; never overwrites existing user keys.
    Recursively merges nested dicts.
    """
    result = dict(user)  # Start with user config — preserves user values
    for key, default_value in defaults.items():
        if key not in result:
            result[key] = default_value
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            result[key] = _deep_merge(default_value, result[key])
        # If user has a non-dict value where default has a dict (or vice versa),
        # keep user's value — user explicitly set it.
    return result


def _save_yaml(path: Path, data: dict) -> None:
    """Save dict as YAML, creating parent directories as needed."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def cmd_config_migrate(current_path: str | None = None) -> int:
    """Migrate user config: merge missing defaults, preserve user settings.

    Args:
        current_path: Path to the current user config file.
                      Defaults to $PRISMATIC_HOME/.prismatic/config.yaml

    Returns:
        0 on success, 1 on failure.
    """
    user_config_path = Path(current_path) if current_path else DEFAULT_USER_CONFIG
    template_path = DEFAULT_TEMPLATE

    if not template_path.exists():
        print(
            f"prismatic-admin: warning — default config template not found at "
            f"{template_path}. Migration skipped (no defaults to merge)."
        )
        return 1

    defaults = _load_yaml(template_path)
    user = _load_yaml(user_config_path)

    if not user:
        # No existing user config — copy template as-is
        print(
            f"prismatic-admin: no existing config at {user_config_path}. "
            f"Creating from template."
        )
        shutil.copy2(template_path, user_config_path)
        print(f"  Created: {user_config_path}")
        return 0

    # Backup the existing config
    backup_path = user_config_path.with_suffix(".yaml.bak")
    shutil.copy2(user_config_path, backup_path)
    print(f"prismatic-admin: backed up existing config to {backup_path}")

    # Merge defaults into user config (user values preserved)
    merged = _deep_merge(defaults, user)
    _save_yaml(user_config_path, merged)

    # Report what was added
    added = [k for k in defaults if k not in user]
    if added:
        print(f"  Added {len(added)} new default keys: {', '.join(sorted(added))}")
    else:
        print("  No new keys to add — config is up to date.")

    return 0


# ── Database Upgrade ────────────────────────────────────

# Track the current schema version. Increment when adding new tables/columns.
CURRENT_SCHEMA_VERSION = 1

SCHEMA_MIGRATIONS: dict[int, str] = {
    1: """
    -- Schema v1: Initial event router schema
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS processed_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        processed_at TEXT NOT NULL DEFAULT (datetime('now')),
        source_agent TEXT,
        target_agent TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_dedup_key ON processed_events(dedup_key);
    CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_events(processed_at);
    """,
}


def _get_db_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version from the DB, or 0 if unversioned."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def cmd_db_upgrade(db_path: str | None = None) -> int:
    """Run pending database schema migrations.

    Args:
        db_path: Path to the SQLite database file.
                 Defaults to $PRISMATIC_HOME/.prismatic/db/event_router.db

    Returns:
        0 on success, 1 on failure.
    """
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # Ensure directory exists
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path))
    try:
        current_version = _get_db_version(conn)

        if current_version >= CURRENT_SCHEMA_VERSION:
            print(
                f"prismatic-admin: db schema is current "
                f"(v{current_version}, latest v{CURRENT_SCHEMA_VERSION})."
            )
            return 0

        print(
            f"prismatic-admin: upgrading db from v{current_version} "
            f"to v{CURRENT_SCHEMA_VERSION}..."
        )

        for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
            if version not in SCHEMA_MIGRATIONS:
                print(
                    f"prismatic-admin: error — no migration defined for v{version}"
                )
                return 1

            print(f"  Applying migration v{version}...")
            conn.executescript(SCHEMA_MIGRATIONS[version])

        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        conn.commit()

        print(
            f"prismatic-admin: db upgrade complete. "
            f"Now at v{CURRENT_SCHEMA_VERSION}."
        )
        return 0

    except Exception as e:
        print(f"prismatic-admin: db upgrade failed: {e}", file=sys.stderr)
        conn.rollback()
        return 1
    finally:
        conn.close()


# ── CLI Entry Point ─────────────────────────────────────
# ── Telemetry Dashboard ───────────────────────────────────

# Color thresholds for terminal output
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[36m"


def cmd_telemetry_dashboard(hours: int = 24, db_path: str | None = None) -> int:
    """Render a telemetry dashboard from the collector database.

    Args:
        hours: Time window in hours (default: 24).
        db_path: Path to the SQLite database file.
                 Defaults to $PRISMATIC_STATE_DIR/event_router.db

    Returns:
        0 on success, 1 on failure.
    """
    try:
        from .telemetry import get_collector  # noqa: F401 — verify module importable
    except ImportError as exc:
        print(f"prismatic-admin: cannot import telemetry module: {exc}", file=sys.stderr)
        print("  Ensure prismatic-engine is installed or run from the repo root.", file=sys.stderr)
        return 1

    from datetime import datetime, timezone, timedelta
    import sqlite3

    target_path = Path(db_path) if db_path else Path(
        os.environ.get("PRISMATIC_STATE_DIR",
                       str(PRISMATIC_HOME / ".prismatic"))
    ) / "event_router.db"

    if not target_path.exists():
        print(f"prismatic-admin: no telemetry database found at {target_path}")
        print("  The collector creates this on first use — start the dispatcher first.")
        return 1

    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    try:
        print()
        print(f"{BOLD}{'='*70}{RESET}")
        print(f"{BOLD}  Prismatic Telemetry Dashboard — Last {hours}h{RESET}")
        print(f"{BOLD}  DB: {target_path}{RESET}")
        print(f"{BOLD}{'='*70}{RESET}")
        print()

        # 1. Agent Throughput
        print(f"{BOLD}▸ Agent Throughput (runs/hour){RESET}")
        print(f"  {'Agent':<12} {'Runs':<8} {'Completed':<12} {'Failed':<8} {'Rate/h':<10}")
        print(f"  {'─'*12} {'─'*8} {'─'*12} {'─'*8} {'─'*10}")

        rows = conn.execute(
            """SELECT agent, COUNT(*) as total,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
               FROM telemetry_agent_runs
               WHERE start_time >= ?
               GROUP BY agent ORDER BY total DESC""",
            (cutoff,)
        ).fetchall()

        if rows:
            for r in rows:
                rate = r["total"] / max(hours, 1)
                status_color = RED if r["failed"] > 0 else GREEN
                print(
                    f"  {r['agent']:<12} {r['total']:<8} "
                    f"{GREEN}{r['completed']:<12}{RESET} "
                    f"{status_color}{r['failed']:<8}{RESET} "
                    f"{rate:.1f}/h"
                )
        else:
            print(f"  {YELLOW}(no agent run data in window){RESET}")
        print()

        # 2. Credit Burn Rate
        print(f"{BOLD}▸ Credit Burn{RESET}")
        print(f"  {'Agent':<12} {'Credits':<12} {'Rate/h':<10} {'Ops':<6}")
        print(f"  {'─'*12} {'─'*12} {'─'*10} {'─'*6}")

        credit_rows = conn.execute(
            """SELECT agent, SUM(credits_spent) as total_credits,
                      COUNT(*) as ops
               FROM telemetry_credit_ledger
               WHERE recorded_at >= ?
               GROUP BY agent ORDER BY total_credits DESC""",
            (cutoff,)
        ).fetchall()

        total_credits = 0
        if credit_rows:
            for r in credit_rows:
                rate = r["total_credits"] / max(hours, 1)
                total_credits += r["total_credits"]
                color = RED if rate > 50 else (YELLOW if rate > 20 else GREEN)
                print(
                    f"  {r['agent']:<12} "
                    f"{color}{r['total_credits']:<12}{RESET} "
                    f"{rate:.1f}/h{'':<5} "
                    f"{r['ops']:<6}"
                )
            print(f"  {'─'*12} {'─'*12} {'─'*10} {'─'*6}")
            print(
                f"  {'TOTAL':<12} {BOLD}{total_credits:<12}{RESET} "
                f"{total_credits / max(hours, 1):.1f}/h"
            )
        else:
            print(f"  {YELLOW}(no credit data in window){RESET}")
        print()

        # 3. Token Metrics
        print(f"{BOLD}▸ Token Metrics{RESET}")
        print(f"  {'Agent':<12} {'Tokens':<10} {'TPS':<8} {'Context%':<10} {'VRAM(MB)':<10}")
        print(f"  {'─'*12} {'─'*10} {'─'*8} {'─'*10} {'─'*10}")

        token_rows = conn.execute(
            """SELECT agent, SUM(prompt_tokens + completion_tokens) as total_tokens,
                      AVG(tps) as avg_tps, AVG(context_pct) as avg_context,
                      MAX(vram_mb) as max_vram
               FROM telemetry_token_metrics
               WHERE recorded_at >= ?
               GROUP BY agent ORDER BY total_tokens DESC""",
            (cutoff,)
        ).fetchall()

        if token_rows:
            for r in token_rows:
                tps = r["avg_tps"] or 0
                ctx = r["avg_context"] or 0
                tps_color = RED if tps < 5 else (YELLOW if tps < 20 else GREEN)
                ctx_color = RED if ctx > 80 else (YELLOW if ctx > 60 else GREEN)
                print(
                    f"  {r['agent']:<12} "
                    f"{r['total_tokens']:<10} "
                    f"{tps_color}{tps:.1f}{'':<4}{RESET} "
                    f"{ctx_color}{ctx:.1f}%{'':<5}{RESET} "
                    f"{r['max_vram'] or 0:<10}"
                )
        else:
            print(f"  {YELLOW}(no token data in window){RESET}")
        print()

        # 4. Failure Rate
        print(f"{BOLD}▸ Failure Rate{RESET}")
        total_runs = conn.execute(
            "SELECT COUNT(*) as cnt FROM telemetry_agent_runs WHERE start_time >= ?",
            (cutoff,)
        ).fetchone()["cnt"]
        failed_runs = conn.execute(
            "SELECT COUNT(*) as cnt FROM telemetry_agent_runs "
            "WHERE start_time >= ? AND status = 'failed'",
            (cutoff,)
        ).fetchone()["cnt"]

        if total_runs > 0:
            fail_rate = (failed_runs / total_runs) * 100
            color = RED if fail_rate > 20 else (YELLOW if fail_rate > 5 else GREEN)
            print(f"  Total runs: {total_runs}")
            print(f"  Failed:     {color}{failed_runs}{RESET}")
            print(f"  Rate:       {color}{fail_rate:.1f}%{RESET}")
        else:
            print(f"  {YELLOW}(no run data in window){RESET}")
        print()

        # 5. Active Loop Events
        print(f"{BOLD}▸ Loop Events{RESET}")
        loop_rows = conn.execute(
            """SELECT loop_type, COUNT(*) as cnt
               FROM telemetry_loop_events
               WHERE created_at >= ?
               GROUP BY loop_type ORDER BY cnt DESC""",
            (cutoff,)
        ).fetchall()

        if loop_rows:
            for r in loop_rows:
                loop_type = r["loop_type"] or "unknown"
                print(f"  {loop_type:<20} {r['cnt']}")
        else:
            print(f"  {YELLOW}(no loop events in window){RESET}")
        print()

        # 6. Validation Events
        print(f"{BOLD}▸ Validation{RESET}")
        val = conn.execute(
            """SELECT SUM(total_tests) as total, SUM(passed) as passed,
                      SUM(failed) as failed, SUM(rollback) as rollbacks
               FROM telemetry_validation_events
               WHERE created_at >= ?""",
            (cutoff,)
        ).fetchone()

        if val and val["total"]:
            total_v = val["total"]
            passed_v = val["passed"] or 0
            failed_v = val["failed"] or 0
            rollbacks_v = val["rollbacks"] or 0
            pass_rate = (passed_v / max(total_v, 1)) * 100
            color = RED if pass_rate < 80 else (YELLOW if pass_rate < 95 else GREEN)
            print(f"  Tests:     {total_v}")
            print(f"  Passed:    {GREEN}{passed_v}{RESET}")
            print(f"  Failed:    {RED if failed_v > 0 else GREEN}{failed_v}{RESET}")
            print(f"  Rollbacks: {YELLOW if rollbacks_v > 0 else GREEN}{rollbacks_v}{RESET}")
            print(f"  Pass Rate: {color}{pass_rate:.1f}%{RESET}")
        else:
            print(f"  {YELLOW}(no validation data in window){RESET}")
        print()

        # 7. Circuit Breakers
        print(f"{BOLD}▸ Circuit Breakers{RESET}")
        breaker_rows = conn.execute(
            """SELECT issue_id, agent, micro_count, macro_count, tripped, last_seen
               FROM telemetry_circuit_breakers
               WHERE tripped = 1 OR last_seen >= ?
               ORDER BY tripped DESC, last_seen DESC""",
            (cutoff,)
        ).fetchall()

        if breaker_rows:
            for r in breaker_rows:
                state = f"{RED}TRIPPED{RESET}" if r["tripped"] else f"{GREEN}ok{RESET}"
                print(
                    f"  [{state}] {r['issue_id']} ({r['agent']}) — "
                    f"micro={r['micro_count']}, macro={r['macro_count']}"
                )
        else:
            print(f"  {GREEN}(no breaker activity in window){RESET}")
        print()

        print(f"{BOLD}{'='*70}{RESET}")
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"  Snapshot: {now_str}")
        print(f"{BOLD}{'='*70}{RESET}")
        print()

        return 0

    except sqlite3.OperationalError as exc:
        print(f"prismatic-admin: database error: {exc}", file=sys.stderr)
        print("  The telemetry tables may not exist yet. Run the dispatcher once first.", file=sys.stderr)
        return 1
    finally:
        conn.close()




def cmd_telemetry_alerts(
    hours: int | None = None,
    db_path: str | None = None,
    post_comments: bool = False,
) -> int:
    """Evaluate alert rules against recent telemetry data.

    Prints triggered alerts to stdout. With --post, also comments
    on affected Linear issues (requires LINEAR_API_KEY env var).
    """
    import os as _os

    # Resolve DB path
    if db_path:
        target_path = db_path
    else:
        state_dir = _os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
        target_path = _os.path.join(state_dir, "event_router.db")

    if not _os.path.exists(target_path):
        print(f"prismatic-admin: no telemetry database found at {target_path}")
        return 1

    # Import telemetry and create collector
    try:
        from .telemetry import TelemetryCollector
    except ImportError as exc:
        print(f"prismatic-admin: cannot import telemetry module: {exc}",
              file=sys.stderr)
        return 1

    collector = TelemetryCollector(db_path=target_path)
    # Flush any queued events before checking
    import time as _time
    _time.sleep(0.5)

    # Determine API key for posting
    api_key = None
    if post_comments:
        api_key = _os.environ.get("LINEAR_API_KEY", "")
        if not api_key:
            print("prismatic-admin: --post requires LINEAR_API_KEY env var",
                  file=sys.stderr)
            return 1

    alerts = collector.check_alerts(hours=hours, linear_api_key=api_key)

    if not alerts:
        print("prismatic-admin: no alerts triggered")
        return 0

    print(f"prismatic-admin: {len(alerts)} alert(s) triggered:\n")
    for a in alerts:
        print(f"  [{a['severity'].upper()}] {a['rule']}")
        print(f"    {a['message']}")
        if post_comments and api_key:
            print(f"    (alert comment posted to affected Linear issue)")
        print()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic-admin",
        description="System administration CLI for the Prismatic Engine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # config migrate
    config_parser = sub.add_parser(
        "config", help="Manage configuration"
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)

    migrate_parser = config_sub.add_parser(
        "migrate", help="Migrate user config with new defaults"
    )
    migrate_parser.add_argument(
        "--current",
        dest="current_path",
        help="Path to current user config (default: $PRISMATIC_HOME/.prismatic/config.yaml)",
    )

    # db upgrade
    db_parser = sub.add_parser("db", help="Manage database")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)

    upgrade_parser = db_sub.add_parser(
        "upgrade", help="Run pending schema migrations"
    )
    upgrade_parser.add_argument(
        "--db-path",
        help="Path to database (default: $PRISMATIC_HOME/.prismatic/db/event_router.db)",
    )

    # telemetry dashboard
    telemetry_parser = sub.add_parser("telemetry", help="View telemetry data")
    telemetry_sub = telemetry_parser.add_subparsers(dest="telemetry_command", required=True)

    dashboard_parser = telemetry_sub.add_parser(
        "dashboard", help="Render telemetry dashboard"
    )
    dashboard_parser.add_argument(
        "--hours", type=int, default=24,
        help="Time window in hours (default: 24)"
    )
    dashboard_parser.add_argument(
        "--db-path", dest="db_path_arg",
        help="Path to database (default: $PRISMATIC_STATE_DIR/event_router.db)"
    )

    # telemetry alerts
    alerts_parser = telemetry_sub.add_parser(
        "alerts", help="Evaluate alert rules against recent telemetry"
    )
    alerts_parser.add_argument(
        "--hours", type=int,
        help="Lookback window in hours (default: PRISMATIC_ALERT_WINDOW_HOURS env or 1)"
    )
    alerts_parser.add_argument(
        "--db-path", dest="db_path_arg",
        help="Path to database (default: $PRISMATIC_STATE_DIR/event_router.db)"
    )
    alerts_parser.add_argument(
        "--post", action="store_true",
        help="Post alert comments to affected Linear issues (requires LINEAR_API_KEY)"
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "config" and args.config_command == "migrate":
        sys.exit(cmd_config_migrate(args.current_path))

    elif args.command == "db" and args.db_command == "upgrade":
        sys.exit(cmd_db_upgrade(getattr(args, "db_path", None)))

    elif args.command == "telemetry" and args.telemetry_command == "dashboard":
        sys.exit(cmd_telemetry_dashboard(
            hours=getattr(args, "hours", 24),
            db_path=getattr(args, "db_path_arg", None),
        ))

    elif args.command == "telemetry" and args.telemetry_command == "alerts":
        sys.exit(cmd_telemetry_alerts(
            hours=getattr(args, "hours", None),
            db_path=getattr(args, "db_path_arg", None),
            post_comments=getattr(args, "post", False),
        ))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
