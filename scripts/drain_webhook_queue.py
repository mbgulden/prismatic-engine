#!/usr/bin/env python3
"""
Prismatic Engine — Webhook Queue Drainer

Drains pending events from linear_webhook_queue.db. Called by the
prismatic-webhook-drain.timer every 30s. The webhook handler in
prismatic/gateway/server.py queues events that don't match a live
agent dispatch path (e.g. non-Issue types, Comment events, or events
where the live dispatch path was rate-limited or errored). This drain
ensures they get a second chance.

Logic:
1. Read up to N pending events ordered by received_at ASC.
2. For each event with a non-empty identifier + Issue type + agent:* label,
   call dispatch_issue_by_identifier (the same single-issue fast path the
   live webhook uses).
3. Update dispatch_status to 'dispatched' / 'no_op' / 'failed' / 'stale'.
4. Mark events older than STALE_AFTER_SECONDS as 'stale' so we don't
   accidentally replay ancient events after a long outage.

Idempotency:
- dispatch_issue_by_identifier checks dedup DB before launching agents
- Same event_id PRIMARY KEY prevents double-insert
- Updating dispatch_status on every row provides a visible audit trail

Args (env vars):
  PRISMATIC_STATE_DIR (default: ./prismatic_state)
  DRAIN_BATCH_SIZE (default: 25)
  DRAIN_STALE_AFTER_SECONDS (default: 86400 = 24h)

CLI flags:
  --dry-run     Print what would be drained, do not mutate DB
  --max N       Cap total events processed this run (default 100)
  --stale-only  Only mark stale events (no live dispatch)
  --backfill    Replay pending + stale + failed events without aging them out
  --since TS    Only drain/replay rows with received_at >= TS (unix seconds)
  --until TS    Only drain/replay rows with received_at <= TS (unix seconds)
  --reset       Set all 'pending' events back to 'pending' (debug only)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# Allow running as a script from anywhere
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(db_path))


def mark_stale(conn: sqlite3.Connection, stale_after_seconds: int) -> int:
    """Mark events older than stale_after_seconds as 'stale'. Idempotent."""
    cur = conn.cursor()
    cutoff = time.time() - stale_after_seconds
    cur.execute(
        """
        UPDATE linear_webhook_queue
        SET dispatch_status = 'stale'
        WHERE dispatch_status = 'pending' AND received_at < ?
        """,
        (cutoff,),
    )
    return cur.rowcount


def pending_events(
    conn: sqlite3.Connection,
    limit: int,
    *,
    include_replayable: bool = False,
    since: float | None = None,
    until: float | None = None,
) -> list[dict]:
    """Return drainable events in deterministic oldest-first order.

    Normal drain mode only consumes ``pending`` rows. Backfill/replay mode also
    includes rows previously marked ``stale`` or ``failed:*`` so missed bus
    events and failed dispatch attempts can be safely retried. Optional
    ``since``/``until`` bounds apply to ``received_at`` and make replay windows
    explicit instead of all-or-nothing.
    """
    cur = conn.cursor()
    clauses = ["identifier != ''"]
    params: list[float | int] = []
    if include_replayable:
        clauses.append(
            "(dispatch_status IN ('pending', 'stale') OR dispatch_status LIKE 'failed%')"
        )
    else:
        clauses.append("dispatch_status = 'pending'")
    if since is not None:
        clauses.append("received_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("received_at <= ?")
        params.append(until)
    params.append(limit)
    cur.execute(
        f"""
        SELECT event_id, identifier, event_type, action, received_at, raw_json
        FROM linear_webhook_queue
        WHERE {" AND ".join(clauses)}
        ORDER BY received_at ASC
        LIMIT ?
        """,
        tuple(params),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_status(
    conn: sqlite3.Connection, event_id: str, status: str, note: str = ""
) -> None:
    try:
        from prismatic.ingestion_queue import update_event_status

        update_event_status(event_id, status)
        return
    except Exception:
        pass
    cur = conn.cursor()
    if note:
        # Keep raw_json untouched, append a note into a side table if you want
        # a per-event trail. For now we just rewrite status — the audit log
        # in the webhook handler captures the detailed reason.
        pass
    cur.execute(
        """
        UPDATE linear_webhook_queue
        SET dispatch_status = ?
        WHERE event_id = ?
        """,
        (status, event_id),
    )


def reset_all_pending(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE linear_webhook_queue
        SET dispatch_status = 'pending'
        WHERE dispatch_status IN ('stale', 'failed')
        """
    )
    return cur.rowcount


def _linear_dispatch_allowed() -> tuple[bool, str]:
    """Return whether a drain dispatch attempt may spend Linear budget."""
    try:
        from prismatic import linear_rate_limit

        linear_rate_limit.ensure_linear_circuit_closed(source="webhook_drain.dispatch")
        return True, "ok"
    except Exception as exc:
        if exc.__class__.__name__ == "LinearRateLimitCircuitOpen":
            return False, str(exc)
        # Fail closed on circuit-breaker read errors; this drain is a safety net.
        return False, f"linear circuit unavailable: {exc}"


def has_agent_label(raw_json: str) -> bool:
    """Best-effort check: parse the raw payload for agent:* labels."""
    import json

    try:
        payload = json.loads(raw_json)
    except Exception:
        return False
    data = payload.get("data", {}) or {}
    labels = data.get("labels", {}) or {}
    nodes = labels.get("nodes", []) if isinstance(labels, dict) else []
    for n in nodes:
        if isinstance(n, dict):
            name = n.get("name", "")
        else:
            name = str(n)
        if name.startswith("agent:"):
            return True
    return False


def drain(args: argparse.Namespace, dispatch_fn=None) -> int:
    state_dir = Path(
        os.environ.get("PRISMATIC_STATE_DIR", str(Path.home() / ".prismatic" / "db"))
    )
    db_path = state_dir / "linear_webhook_queue.db"
    if not db_path.exists():
        print(f"[drain] No queue at {db_path} — nothing to do")
        return 0

    stale_after = int(os.environ.get("DRAIN_STALE_AFTER_SECONDS", "86400"))
    batch_size = int(os.environ.get("DRAIN_BATCH_SIZE", "25"))

    conn = _connect(db_path)
    try:
        # 1. Mark stale events (skip if dry-run or backfill — don't mutate)
        n_stale = 0
        if args.backfill:
            print(
                "[drain] Backfill mode enabled — replaying pending/stale/failed events without reclassifying them"
            )
        elif not args.dry_run:
            n_stale = mark_stale(conn, stale_after)
            conn.commit()
            if n_stale:
                print(f"[drain] Marked {n_stale} events stale (>{stale_after}s old)")

        # 2. Reset flag — debug only
        if args.reset:
            n_reset = reset_all_pending(conn)
            conn.commit()
            print(f"[drain] --reset: {n_reset} events restored to pending")
            return 0

        # 3. Drain pending Issue events
        events = pending_events(
            conn,
            min(args.max, batch_size),
            include_replayable=args.backfill,
            since=args.since,
            until=args.until,
        )
        if not events:
            if not args.dry_run:
                try:
                    from prismatic.ingestion_queue import record_drain_result

                    record_drain_result("no_op", {"processed": 0, "dispatched": 0, "no_op": 0, "failed": 0, "stale": n_stale, "dry_run": False})
                except Exception as exc:
                    print(f"[drain] WARN: could not record drain result: {exc}")
            print(f"[drain] No pending events (db at {db_path})")
            return 0

        print(f"[drain] Processing {len(events)} pending events")

        # Import here so script works even if prismatic package not on path
        if dispatch_fn is None:
            try:
                from prismatic.dispatcher import (
                    dispatch_issue_by_identifier as dispatch_fn,
                )
            except Exception as exc:
                print(f"[drain] FATAL: cannot import dispatcher: {exc}")
                return 2

        dispatched = 0
        no_op = 0
        failed = 0
        deferred = 0
        manual_review = 0
        blocked_preflight = 0
        stale = n_stale
        for ev in events:
            eid = ev["event_id"]
            ident = ev["identifier"]
            etype = ev["event_type"]
            action = ev["action"]

            # Filter decisions — only mutate DB when not dry-run
            if etype != "Issue" or action not in ("create", "update"):
                if not args.dry_run:
                    update_status(conn, eid, "no_op")
                    no_op += 1
                continue

            if args.dry_run:
                print(f"[drain]   DRY: would dispatch {ident} ({action})")
                continue

            allowed, block_reason = _linear_dispatch_allowed()
            if not allowed:
                update_status(conn, eid, "deferred_rate_limit")
                deferred += 1
                print(f"[drain]   ⏸ {ident} deferred: {block_reason}")
                continue

            try:
                result = dispatch_fn(identifier=ident)
                result_status = "dispatched" if result else "no_op"
                if isinstance(result, dict):
                    result_status = str(result.get("status") or ("dispatched" if result.get("ok") else "no_op"))
                if result_status == "dispatched":
                    update_status(conn, eid, "dispatched")
                    dispatched += 1
                    print(f"[drain]   ✓ {ident} dispatched")
                elif result_status == "deferred_rate_limit":
                    update_status(conn, eid, "deferred_rate_limit")
                    deferred += 1
                    print(f"[drain]   ⏸ {ident} deferred by assigned-agent preflight")
                elif result_status == "needs_manual_review":
                    update_status(conn, eid, "needs_manual_review")
                    manual_review += 1
                    print(f"[drain]   ? {ident} needs manual review")
                elif result_status in {"blocked_preflight", "preflight_failed"}:
                    update_status(conn, eid, "blocked_preflight")
                    blocked_preflight += 1
                    print(f"[drain]   ⛔ {ident} blocked by preflight")
                elif result_status == "failed":
                    update_status(conn, eid, "failed")
                    failed += 1
                    print(f"[drain]   ✗ {ident} failed")
                else:
                    update_status(conn, eid, "no_op")
                    no_op += 1
                    print(f"[drain]   · {ident} no-op (no agent match or gate)")
            except Exception as exc:
                update_status(conn, eid, f"failed: {str(exc)[:80]}")
                failed += 1
                print(f"[drain]   ✗ {ident} failed: {exc}")

            # Gentle pacing — avoid hammering Linear API
            time.sleep(0.2)

        if not args.dry_run:
            conn.commit()
        result = "failed" if failed else ("deferred_rate_limit" if deferred else ("blocked_preflight" if blocked_preflight else ("needs_manual_review" if manual_review else "ok")))
        counts = {"dispatched": dispatched, "no_op": no_op, "failed": failed, "deferred": deferred, "needs_manual_review": manual_review, "blocked_preflight": blocked_preflight, "stale": stale, "dry_run": bool(args.dry_run), "processed": len(events)}
        if not args.dry_run:
            try:
                from prismatic.ingestion_queue import record_drain_result

                record_drain_result(result, counts)
            except Exception as exc:
                print(f"[drain] WARN: could not record drain result: {exc}")
        print(
            f"[drain] Done: dispatched={dispatched} no_op={no_op} "
            f"failed={failed} deferred={deferred} manual_review={manual_review} blocked_preflight={blocked_preflight} stale={stale} dry_run={args.dry_run}"
        )
        return 0 if failed == 0 else 1
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Drain prismatic webhook queue")
    p.add_argument("--dry-run", action="store_true", help="Don't mutate DB")
    p.add_argument("--max", type=int, default=100, help="Max events to process")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--stale-only", action="store_true", help="Only mark stale")
    mode.add_argument(
        "--backfill",
        action="store_true",
        help="Replay pending + stale + failed events without aging them out",
    )
    mode.add_argument(
        "--reset", action="store_true", help="Restore stale/failed to pending"
    )
    p.add_argument(
        "--since",
        type=float,
        default=None,
        help="Only process rows with received_at >= this unix timestamp",
    )
    p.add_argument(
        "--until",
        type=float,
        default=None,
        help="Only process rows with received_at <= this unix timestamp",
    )
    args = p.parse_args()

    if args.stale_only:
        state_dir = Path(
            os.environ.get("PRISMATIC_STATE_DIR", str(Path.home() / ".prismatic" / "db"))
        )
        db_path = state_dir / "linear_webhook_queue.db"
        if not db_path.exists():
            return 0
        conn = _connect(db_path)
        try:
            stale_after = int(os.environ.get("DRAIN_STALE_AFTER_SECONDS", "86400"))
            n = mark_stale(conn, stale_after)
            conn.commit()
            print(f"[drain] stale-only: marked {n} events stale")
        finally:
            conn.close()
        return 0

    return drain(args)


if __name__ == "__main__":
    sys.exit(main())
