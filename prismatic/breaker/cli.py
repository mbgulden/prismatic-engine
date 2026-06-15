#!/usr/bin/env python3
"""
prismatic/breaker/cli.py — Prismatic Breaker CLI

Human-in-the-loop intervention tool for the Prismatic Engine's circuit
breaker system. Operator inspects and clears tripped breakers via CLI.

Subcommands
-----------
  list        Show all breaker states (optionally only tripped).
  inspect     Full cycle history for a single issue.
  correct     HITL correction injection + breaker reset.
  clear       Clear a breaker without correction annotation.

Exit codes:
  0 — success
  1 — issue not found or validation error
  2 — database error

Usage examples
--------------
  prismatic-breaker list
  prismatic-breaker list --tripped
  prismatic-breaker inspect GRO-1234
  prismatic-breaker correct GRO-1234 --message "Reviewed logs — false positive"
  prismatic-breaker clear GRO-1234
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Database path ───────────────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# ── Display helpers ──────────────────────────────────────────────────

_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _db_connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection to the telemetry database (read-only by default)."""
    path = db_path or DEFAULT_DB_PATH
    if not Path(path).exists():
        print(f"ERROR: Database not found at {path}", file=sys.stderr)
        sys.exit(2)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_swarm_ledger(conn: sqlite3.Connection) -> None:
    """Ensure a durable operator-action ledger exists."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS swarm_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_swarm_ledger_issue ON swarm_ledger(issue_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_swarm_ledger_action ON swarm_ledger(action, created_at)"
    )


def _record_swarm_ledger(
    conn: sqlite3.Connection,
    *,
    issue_id: str,
    action: str,
    payload: dict,
    created_at: str,
) -> None:
    """Record a mutating CLI action in swarm_ledger."""
    _ensure_swarm_ledger(conn)
    conn.execute(
        """INSERT INTO swarm_ledger
           (created_at, actor, action, issue_id, source, payload_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            created_at,
            os.environ.get("USER", "human-operator"),
            action,
            issue_id,
            "cli",
            json.dumps(payload, sort_keys=True),
        ),
    )


def _format_ts(iso_str: str | None) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_str or "-"


def _format_issue_id(issue_id: str) -> str:
    """Trim UUID prefix if present; keep short identifier if display-friendly."""
    # Most issue IDs in this system are like "GRO-1234"
    return issue_id


# ═══════════════════════════════════════════════════════════════════
# Subcommands
# ═══════════════════════════════════════════════════════════════════


def cmd_list(args: argparse.Namespace) -> None:
    """List all breaker states as a table."""
    conn = _db_connect(args.db)
    try:
        query = "SELECT * FROM telemetry_circuit_breakers"
        params: list = []
        if args.tripped:
            query += " WHERE tripped = 1"
        query += " ORDER BY last_seen DESC"

        rows = conn.execute(query, params).fetchall()

        if not rows:
            if args.tripped:
                print("No tripped breakers found.")
            else:
                print("No breakers in database.  All circuits nominal.")
            return

        # Determine column widths
        max_issue = max(len(r["issue_id"]) for r in rows)
        max_agent = max(len(r["agent"]) for r in rows)
        issue_w = max(max_issue, 10)
        agent_w = max(max_agent, 10)

        header = (
            f"{'ISSUE':<{issue_w}}  {'AGENT':<{agent_w}}  "
            f"{'MICRO':>5}  {'MACRO':>5}  {'TRIPPED':>7}  "
            f"{'TRIPPED AT':<19}  {'LAST SEEN':<19}"
        )
        sep = "─" * len(header)
        print(f"\n{_BOLD}Circuit Breaker States{_RESET}")
        print(sep)
        print(header)
        print(sep)

        for r in rows:
            tripped_flag = (
                f"{_RED}YES{_RESET}" if r["tripped"] else f"{_GREEN}no{_RESET}"
            )
            print(
                f"{r['issue_id']:<{issue_w}}  {r['agent']:<{agent_w}}  "
                f"{r['micro_count']:>5}  {r['macro_count']:>5}  "
                f"{tripped_flag:>7}  "
                f"{_format_ts(r['tripped_at']):<19}  "
                f"{_format_ts(r['last_seen']):<19}"
            )
        print(sep)
        print(f"{len(rows)} breaker(s) total")
    finally:
        conn.close()


def cmd_inspect(args: argparse.Namespace) -> None:
    """Show full cycle history for a single issue."""
    conn = _db_connect(args.db)
    try:
        row = conn.execute(
            "SELECT * FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (args.issue_id,),
        ).fetchone()

        if not row:
            print(
                f"Breaker state not found for {_BOLD}{args.issue_id}{_RESET}. "
                "No cycles recorded."
            )
            sys.exit(1)

        tripped_str = (
            f"{_RED}YES (tripped at {_format_ts(row['tripped_at'])}){_RESET}"
            if row["tripped"]
            else f"{_GREEN}No{_RESET}"
        )

        print(f"\n{_BOLD}═══ Breaker Inspection: {args.issue_id} ═══{_RESET}")
        print(f"  Agent:         {row['agent']}")
        print(f"  Micro-cycles:  {row['micro_count']}")
        print(f"  Macro-cycles:  {row['macro_count']}")
        print(f"  Tripped:       {tripped_str}")
        print(f"  Last seen:     {_format_ts(row['last_seen'])}")

        # Query cycle history from loop events
        loop_rows = conn.execute(
            "SELECT id, agent, loop_type, created_at, trigger "
            "FROM telemetry_loop_events "
            "WHERE issue_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (args.issue_id,),
        ).fetchall()

        if loop_rows:
            print(f"\n{_BOLD}Recent Loop Events (last 20):{_RESET}")
            print(
                f"  {'ID':<6} {'AGENT':<12} {'TYPE':<18} "
                f"{'CREATED AT':<19} TRIGGER"
            )
            print("  " + "─" * 80)
            for lr in loop_rows:
                trigger = (lr["trigger"] or "")[:50]
                print(
                    f"  {lr['id']:<6} {lr['agent']:<12} {lr['loop_type']:<18} "
                    f"{_format_ts(lr['created_at']):<19} {trigger}"
                )
        else:
            print(f"\n  No loop events recorded for {args.issue_id}.")

        # Query run records
        run_rows = conn.execute(
            "SELECT run_id, agent, status, start_time, end_time "
            "FROM telemetry_agent_runs "
            "WHERE run_id LIKE ? "
            "ORDER BY start_time DESC LIMIT 10",
            (f"%{args.issue_id}%",),
        ).fetchall()

        if run_rows:
            print(f"\n{_BOLD}Recent Agent Runs (last 10):{_RESET}")
            print(
                f"  {'RUN ID':<8} {'AGENT':<12} {'STATUS':<10} "
                f"{'START TIME':<19} {'END TIME':<19}"
            )
            print("  " + "─" * 70)
            for rr in run_rows:
                print(
                    f"  {rr['run_id']:<8} {rr['agent']:<12} {rr['status']:<10} "
                    f"{_format_ts(rr['start_time']):<19} "
                    f"{_format_ts(rr['end_time']):<19}"
                )
        else:
            print(f"\n  No agent runs recorded for {args.issue_id}.")
    finally:
        conn.close()


def cmd_correct(args: argparse.Namespace) -> None:
    """Inject a HITL correction and reset the breaker."""
    conn = _db_connect(args.db)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (args.issue_id,),
        ).fetchone()

        if not row:
            print(
                f"Breaker state not found for {_BOLD}{args.issue_id}{_RESET}. "
                "Nothing to correct."
            )
            conn.rollback()
            sys.exit(1)

        was_tripped = row["tripped"]
        micro_count = row["micro_count"]
        macro_count = row["macro_count"]

        # Delete breaker state (reset)
        conn.execute(
            "DELETE FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (args.issue_id,),
        )

        # Record the HITL correction as a loop event
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO telemetry_loop_events "
            "(run_id, issue_id, agent, loop_type, trigger, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"breaker-{args.issue_id}",
                args.issue_id,
                "human-operator",
                "circuit_breaker_correction",
                f"HITL correction: {args.message} "
                f"(was tripped={was_tripped}, micro={micro_count}, macro={macro_count})",
                now_iso,
            ),
        )
        _record_swarm_ledger(
            conn,
            issue_id=args.issue_id,
            action="breaker.correct",
            created_at=now_iso,
            payload={
                "message": args.message,
                "was_tripped": bool(was_tripped),
                "micro_count": micro_count,
                "macro_count": macro_count,
            },
        )

        conn.commit()

        was_tripped_display = f"{_RED}YES{_RESET}" if was_tripped else f"{_GREEN}no{_RESET}"
        correction_msg = (
            f"  Message: {args.message}\n"
            f"  Cycles reset: micro={micro_count}, macro={macro_count}\n"
            f"  Was tripped: {was_tripped_display}"
        )

        print(
            f"\n{_GREEN}✓{_RESET} Breaker corrected and reset for "
            f"{_BOLD}{args.issue_id}{_RESET}"
        )
        print(correction_msg)
        print(f"  Correction recorded in loop_events.")

        # Also reset in-memory state via telemetry's reset_breaker
        # (handled by the next telemetry poll since we deleted from DB)

    except Exception as e:
        conn.rollback()
        print(f"ERROR: Correction failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def cmd_clear(args: argparse.Namespace) -> None:
    """Clear a breaker without correction annotation."""
    conn = _db_connect(args.db)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (args.issue_id,),
        ).fetchone()

        if not row:
            print(
                f"Breaker state not found for {_BOLD}{args.issue_id}{_RESET}. "
                "Nothing to clear."
            )
            conn.rollback()
            sys.exit(1)

        micro_count = row["micro_count"]
        macro_count = row["macro_count"]
        was_tripped = row["tripped"]

        # Record the clear action
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO telemetry_loop_events "
            "(run_id, issue_id, agent, loop_type, trigger, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"breaker-{args.issue_id}",
                args.issue_id,
                "human-operator",
                "circuit_breaker_clear",
                f"Breaker cleared manually "
                f"(tripped={was_tripped}, micro={micro_count}, macro={macro_count})",
                now_iso,
            ),
        )
        _record_swarm_ledger(
            conn,
            issue_id=args.issue_id,
            action="breaker.clear",
            created_at=now_iso,
            payload={
                "was_tripped": bool(was_tripped),
                "micro_count": micro_count,
                "macro_count": macro_count,
            },
        )

        # Delete breaker state
        conn.execute(
            "DELETE FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (args.issue_id,),
        )

        conn.commit()

        print(
            f"\n{_GREEN}✓{_RESET} Breaker cleared for "
            f"{_BOLD}{args.issue_id}{_RESET}"
        )
        print(f"  Previous state: micro={micro_count}, macro={macro_count}, "
              f"tripped={was_tripped}")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: Clear failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (also used by api.py for help text)."""
    parser = argparse.ArgumentParser(
        prog="prismatic-breaker",
        description="Human-in-the-loop circuit breaker intervention tool "
                    "for the Prismatic Engine.",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to telemetry database (default: {DEFAULT_DB_PATH})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # --- list ---
    p_list = sub.add_parser("list", help="List all breaker states")
    p_list.add_argument(
        "--tripped",
        action="store_true",
        help="Show only tripped breakers",
    )
    p_list.set_defaults(func=cmd_list)

    # --- inspect ---
    p_inspect = sub.add_parser("inspect", help="Full cycle history for an issue")
    p_inspect.add_argument("issue_id", help="Issue identifier (e.g., GRO-1234)")
    p_inspect.set_defaults(func=cmd_inspect)

    # --- correct ---
    p_correct = sub.add_parser(
        "correct",
        help="HITL correction injection + breaker reset",
    )
    p_correct.add_argument("issue_id", help="Issue identifier (e.g., GRO-1234)")
    p_correct.add_argument(
        "--message", "-m",
        required=True,
        help="Correction description from human operator",
    )
    p_correct.set_defaults(func=cmd_correct)

    # --- clear ---
    p_clear = sub.add_parser("clear", help="Clear a breaker without correction")
    p_clear.add_argument("issue_id", help="Issue identifier (e.g., GRO-1234)")
    p_clear.set_defaults(func=cmd_clear)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
