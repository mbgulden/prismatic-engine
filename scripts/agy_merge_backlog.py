#!/usr/bin/env python3
"""Inspect AGY completed-work rows as dry-run merge backlog / PR plans."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.agy_merge_backlog import (  # noqa: E402
    AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
    get_merge_backlog_item,
    list_merge_backlog,
    verify_merge_backlog_item,
)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_list(args: argparse.Namespace) -> int:
    items = [item.as_dict() for item in list_merge_backlog(db_path=args.db, limit=args.limit)]
    _print(
        {
            "status": "ok",
            "marker": AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
            "count": len(items),
            "merge_backlog": items,
            "non_claims": _non_claims(),
        }
    )
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    item = get_merge_backlog_item(args.completed_work_id, db_path=args.db)
    _print(
        {
            "status": "ok",
            "marker": AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
            "classification": item.classification,
            "recommended_action": item.recommended_action,
            "verification_gate": item.verification_gate,
            "merge_backlog": item.as_dict(),
            "non_claims": _non_claims(),
        }
    )
    return 0


def cmd_plan_pr(args: argparse.Namespace) -> int:
    item = get_merge_backlog_item(args.completed_work_id, db_path=args.db)
    _print(
        {
            "status": "ok",
            "marker": AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
            "dry_run": True,
            "github_pr_created": False,
            "merge_backlog": item.as_dict(),
            "pr_plan": {
                "recommended_action": item.recommended_action,
                "branch": item.pr_branch,
                "title": item.pr_title,
                "body": item.pr_body,
                "eligible_for_auto_merge": False,
            },
            "non_claims": _non_claims(),
        }
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    _print(verify_merge_backlog_item(args.completed_work_id, db_path=args.db))
    return 0


def cmd_open_pr(args: argparse.Namespace) -> int:
    item = get_merge_backlog_item(args.completed_work_id, db_path=args.db)
    if not args.apply:
        _print(
            {
                "status": "dry_run",
                "marker": AGY_CLEAN_PR_CREATE_UPDATE_MARKER,
                "message": "open-pr defaults to dry-run; no GitHub PR was created",
                "github_pr_created": False,
                "merge_backlog": item.as_dict(),
                "non_claims": _non_claims(),
            }
        )
        return 0
    _print(
        {
            "status": "blocked",
            "marker": "AGY_CLEAN_PR_VERIFICATION_GATE_BLOCKED",
            "message": "--apply is intentionally not implemented in this slice; Michael must explicitly authorize real PR creation in a later slice",
            "github_pr_created": False,
            "merge_backlog": item.as_dict(),
            "non_claims": _non_claims(),
        }
    )
    return 2


def _non_claims() -> dict[str, bool]:
    return {
        "auto_merge": False,
        "bulk_agy_dispatch": False,
        "overnight_autopilot_ready": False,
        "production_deploy": False,
        "github_pr_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List dry-run merge backlog items")
    list_parser.add_argument("--db", default=None)
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=cmd_list)

    for name, func, help_text in (
        ("classify", cmd_classify, "Classify a completed-work row"),
        ("plan-pr", cmd_plan_pr, "Build a dry-run PR create/update plan"),
        ("verify", cmd_verify, "Evaluate the lane verification gate"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("completed_work_id")
        p.add_argument("--db", default=None)
        p.set_defaults(func=func)

    open_parser = sub.add_parser("open-pr", help="Dry-run PR opening plan; no side effects by default")
    open_parser.add_argument("completed_work_id")
    open_parser.add_argument("--db", default=None)
    open_parser.add_argument("--dry-run", action="store_true", default=True)
    open_parser.add_argument("--apply", action="store_true", help="Reserved for a future explicitly-authorized slice")
    open_parser.set_defaults(func=cmd_open_pr)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyError as exc:
        _print({"status": "not_found", "detail": f"completed work row not found: {exc.args[0]}"})
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
