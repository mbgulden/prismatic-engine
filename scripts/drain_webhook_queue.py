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

# The production gateway service stores Linear's token as LINEAR_OAUTH_TOKEN.
# The dispatcher helper expects LINEAR_API_KEY, so bridge it before imports.
if not os.environ.get("LINEAR_API_KEY") and os.environ.get("LINEAR_OAUTH_TOKEN"):
    os.environ["LINEAR_API_KEY"] = os.environ["LINEAR_OAUTH_TOKEN"]


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


def pending_events(conn: sqlite3.Connection, limit: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT event_id, identifier, event_type, action, received_at, raw_json
        FROM linear_webhook_queue
        WHERE dispatch_status = 'pending' AND identifier != ''
        ORDER BY received_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_status(
    conn: sqlite3.Connection, event_id: str, status: str, note: str = ""
) -> None:
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


def _get_issue_by_identifier(identifier: str) -> dict | None:
    """Fetch one Linear issue by identifier via the dispatcher's GraphQL helper."""
    from prismatic import dispatcher as d

    query = """
    query IssueByIdentifier($id: String!) {
      issue(id: $id) {
        id identifier title description url
        state { name type }
        labels { nodes { id name } }
      }
    }
    """
    data = d.gql(query, {"id": identifier})
    return data.get("issue")


def _dispatch_issue_by_identifier(identifier: str) -> bool:
    """Dispatch one Linear issue without requiring prismatic.dispatcher to expose
    dispatch_issue_by_identifier(). Mirrors the live dispatcher gates, but keeps
    the queue drainer compatible with branches where the helper has not landed.
    """
    from datetime import datetime, timezone
    from prismatic import dispatcher as d

    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    dedup = d.EventRouterDedup()
    issue = _get_issue_by_identifier(identifier)
    if not issue:
        print(f"[drain] {identifier}: issue not found")
        return False

    issue_id = issue.get("id", "")
    labels = issue.get("labels", {}).get("nodes", [])
    label_names = [label.get("name", "") for label in labels if isinstance(label, dict)]

    agent_name = None
    for ln in label_names:
        canonical = ln.replace("::", ":")
        if canonical.startswith("agent:"):
            candidate = canonical.split(":", 1)[1]
            if candidate in d.AGENT_CONFIG:
                agent_name = candidate
                break
    if not agent_name:
        return False

    label = f"agent:{agent_name}"
    if dedup.is_processed(issue_id, label, cycle_id):
        return False

    is_escalation = False
    try:
        cursor = dedup._conn.cursor()
        cursor.execute(
            "SELECT escalated FROM agy_stall_tracker WHERE issue_id = ?", (issue_id,)
        )
        row = cursor.fetchone()
        if isinstance(row, (tuple, list)) and row and row[0]:
            is_escalation = True
    except Exception:
        pass

    if agent_name == "agy" and is_escalation:
        print(f"[drain] {identifier}: AGY issue escalated/stalled; not relaunching")
        return False

    from_state, to_state = d._get_transition_states(agent_name)
    if not d.evaluate_transition_approval(
        issue_id=issue_id,
        from_state=from_state,
        to_state=to_state,
        is_escalation=is_escalation,
        reason="Agent launch transition (webhook queue drain)",
    ):
        print(f"[drain] {identifier}: transition paused for {agent_name}")
        return False

    decision = d.evaluate_agent_launch(label, issue_id, operation="code_generation")
    identifier_str = issue.get("identifier", identifier)
    if decision.action == d.PolicyAction.DENY:
        print(f"[drain] {identifier}: credit policy blocked: {decision.reason}")
        try:
            d.add_comment(
                issue_id,
                f"🚫 **Credit policy blocked**: {decision.reason}\n"
                f"Estimated cost: {decision.estimated_cost} credits.",
            )
        except Exception:
            pass
        return False
    if decision.action == d.PolicyAction.ASK_USER:
        print(
            f"[drain] {identifier}: credit policy requires user approval: {decision.reason}"
        )
        dedup.mark_processed(issue_id, label, cycle_id)
        return False
    if decision.action == d.PolicyAction.WARN:
        print(f"[drain] {identifier}: credit warning: {decision.reason}")

    try:
        collector = d.get_collector()
        collector.record_credit(
            run_id=f"{cycle_id}-{agent_name}-{identifier_str}",
            agent=agent_name,
            provider=d.AGENT_PROVIDER_MAP.get(agent_name, ""),
            credits_spent=decision.estimated_cost,
            operation="code_generation",
        )
    except Exception:
        pass

    launcher = d.AGENT_LAUNCHERS.get(agent_name)
    if not launcher:
        return False

    try:
        os.environ["PRISMATIC_CURRENT_AGENT_NAME"] = f"dispatcher.agent_{agent_name}"
        result = launcher(issue_id, title=issue.get("title", ""))
        if not result:
            return False
        dedup.mark_processed(issue_id, label, cycle_id)
        print(f"[drain] {identifier_str}: dispatched to {agent_name}")
        try:
            collector = d.get_collector()
            collector.record_agent_run(
                run_id=f"{cycle_id}-{agent_name}-{identifier_str}",
                agent=agent_name,
                issue_id=identifier_str,
                provider=d.AGENT_PROVIDER_MAP.get(agent_name, ""),
                status="dispatched",
            )
        except Exception:
            pass
        try:
            d._emit_agent_event(
                "agent_launched", agent_name, identifier_str, cycle_id=cycle_id
            )
        except Exception:
            pass
        try:
            d.add_comment(
                issue_id,
                f"🤖 **{agent_name.capitalize()}** picked up this issue from webhook queue drain (cycle {cycle_id})",
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        print(f"[drain] {identifier}: dispatch failed: {exc}")
        return False


def drain(args: argparse.Namespace) -> int:
    state_dir = Path(
        os.environ.get("PRISMATIC_STATE_DIR", REPO_ROOT / "prismatic_state")
    )
    db_path = state_dir / "linear_webhook_queue.db"
    if not db_path.exists():
        print(f"[drain] No queue at {db_path} — nothing to do")
        return 0

    stale_after = int(os.environ.get("DRAIN_STALE_AFTER_SECONDS", "86400"))
    batch_size = int(os.environ.get("DRAIN_BATCH_SIZE", "25"))

    conn = _connect(db_path)
    try:
        # 1. Mark stale events (skip if dry-run — don't mutate)
        n_stale = 0
        if not args.dry_run:
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
        events = pending_events(conn, min(args.max, batch_size))
        if not events:
            print(f"[drain] No pending events (db at {db_path})")
            return 0

        print(f"[drain] Processing {len(events)} pending events")

        dispatched = 0
        no_op = 0
        failed = 0
        for ev in events:
            eid = ev["event_id"]
            ident = ev["identifier"]
            etype = ev["event_type"]
            action = ev["action"]

            # Filter decisions — only mutate DB when not dry-run
            if etype != "Issue" or action not in ("create", "update"):
                if not args.dry_run:
                    update_status(conn, eid, "skipped_non_issue")
                continue

            if not has_agent_label(ev["raw_json"]):
                if not args.dry_run:
                    update_status(conn, eid, "skipped_no_agent_label")
                continue

            if args.dry_run:
                print(f"[drain]   DRY: would dispatch {ident} ({action})")
                continue

            try:
                result = _dispatch_issue_by_identifier(identifier=ident)
                if result:
                    update_status(conn, eid, "dispatched")
                    dispatched += 1
                    print(f"[drain]   ✓ {ident} dispatched")
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
        print(
            f"[drain] Done: dispatched={dispatched} no_op={no_op} "
            f"failed={failed} stale={n_stale} dry_run={args.dry_run}"
        )
        return 0 if failed == 0 else 1
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Drain prismatic webhook queue")
    p.add_argument("--dry-run", action="store_true", help="Don't mutate DB")
    p.add_argument("--max", type=int, default=100, help="Max events to process")
    p.add_argument("--stale-only", action="store_true", help="Only mark stale")
    p.add_argument(
        "--reset", action="store_true", help="Restore stale/failed to pending"
    )
    args = p.parse_args()

    if args.stale_only:
        state_dir = Path(
            os.environ.get("PRISMATIC_STATE_DIR", REPO_ROOT / "prismatic_state")
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
