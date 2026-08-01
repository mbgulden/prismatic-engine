#!/usr/bin/env python3
"""Inspect and replay Prismatic dead-letter events.

Examples:
  python3 scripts/prismatic_dead_letter.py list --db /tmp/dead.db
  python3 scripts/prismatic_dead_letter.py replay --db /tmp/dead.db --dry-run

Production replay handlers are intentionally caller-owned.  The CLI provides a
safe inventory/dry-run path and a generic replay hook that marks events replayed
when ``--mark-replayed`` is supplied after an operator has re-enqueued them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from prismatic.dead_letter import DeadLetterStore


def _print_event(event) -> None:
    print(
        json.dumps(
            {
                "id": event.id,
                "event_id": event.event_id,
                "source": event.source,
                "event_type": event.event_type,
                "status": event.status,
                "attempts": event.attempts,
                "max_attempts": event.max_attempts,
                "error": event.error,
                "next_retry_at": event.next_retry_at,
                "replayed_at": event.replayed_at,
                "payload": event.payload,
            },
            sort_keys=True,
        )
    )


def cmd_list(args: argparse.Namespace) -> int:
    store = DeadLetterStore(args.db)
    statuses = args.status or None
    for event in store.list_events(statuses=statuses, limit=args.limit):
        _print_event(event)
    return 0


def cmd_due(args: argparse.Namespace) -> int:
    store = DeadLetterStore(args.db)
    for event in store.due_for_replay(limit=args.limit):
        _print_event(event)
    return 0


def cmd_mark_replayed(args: argparse.Namespace) -> int:
    store = DeadLetterStore(args.db)
    store.mark_replayed(row_id=args.row_id, note=args.note)
    print(f"marked row {args.row_id} replayed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prismatic dead-letter queue utility")
    parser.add_argument("--db", help="dead-letter SQLite DB path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List retained failed events")
    p_list.add_argument("--status", action="append", help="Filter status; repeatable")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_due = sub.add_parser("due", help="List events due for replay now")
    p_due.add_argument("--limit", type=int, default=50)
    p_due.set_defaults(func=cmd_due)

    p_mark = sub.add_parser("mark-replayed", help="Mark a retained event as replayed")
    p_mark.add_argument("row_id", type=int)
    p_mark.add_argument("--note", default="operator replay confirmed")
    p_mark.set_defaults(func=cmd_mark_replayed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
