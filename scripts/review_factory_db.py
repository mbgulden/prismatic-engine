#!/usr/bin/env python3
"""Operator CLI for Review Factory DB durability (Phase 5).

Manual trigger only -- nothing here runs on a schedule.

    python scripts/review_factory_db.py backup [--label X] [--dest-dir D]
    python scripts/review_factory_db.py restore <backup.db> --yes [--safety-dir D]
    python scripts/review_factory_db.py prune [--keep N] [--dest-dir D]
    python scripts/review_factory_db.py list [--dest-dir D]

Restore requires --yes and the daemon to be stopped (or the queue quiesced);
see prismatic.review_factory.durability for the safety contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.durability import (
    backup_database,
    list_backups,
    prune_backups,
    restore_database,
)


def cmd_backup(args: argparse.Namespace) -> int:
    # Ensure the DB exists (creates an empty one on a fresh install) so
    # `backup` never fails just because the daemon hasn't run yet.
    db = ReviewFactoryDB()
    db.ensure_tables()
    db.close()
    out = backup_database(
        dest_dir=Path(args.dest_dir) if args.dest_dir else None,
        label=args.label or "",
    )
    print(json.dumps({"backup": str(out)}))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "Refusing: restore is an operator action. Stop the daemon (or "
            "quiesce the queue), then re-run with --yes.",
            file=sys.stderr,
        )
        return 2
    db = ReviewFactoryDB()
    db.ensure_tables()
    try:
        report = restore_database(
            db,
            Path(args.backup),
            safety_dir=Path(args.safety_dir) if args.safety_dir else None,
        )
    finally:
        db.close()
    print(json.dumps(report, indent=2))
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    pruned = prune_backups(
        Path(args.dest_dir) if args.dest_dir else None,
        keep=args.keep,
    )
    print(json.dumps({"pruned": [str(p) for p in pruned], "keep": args.keep}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            list_backups(Path(args.dest_dir) if args.dest_dir else None),
            indent=2,
        )
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Review Factory DB backup/restore (Phase 5)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help="Take a consistent DB snapshot")
    b.add_argument("--label", default="")
    b.add_argument("--dest-dir", default=None)
    b.set_defaults(func=cmd_backup)

    r = sub.add_parser("restore", help="Restore the DB from a backup (operator action)")
    r.add_argument("backup")
    r.add_argument("--yes", action="store_true")
    r.add_argument("--safety-dir", default=None)
    r.set_defaults(func=cmd_restore)

    p = sub.add_parser("prune", help="Prune old backups, keeping the newest N")
    p.add_argument("--keep", type=int, default=7)
    p.add_argument("--dest-dir", default=None)
    p.set_defaults(func=cmd_prune)

    li = sub.add_parser("list", help="List backups newest-first with validity")
    li.add_argument("--dest-dir", default=None)
    li.set_defaults(func=cmd_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
