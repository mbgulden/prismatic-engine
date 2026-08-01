#!/usr/bin/env python3
"""Dry-run evaluator for the limited AGY overnight readiness guard.

This CLI never launches AGY tasks. It evaluates/persists guard decisions and toggles
operator pause state only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.agy_overnight_guard import (
    AGY_OVERNIGHT_READINESS_GUARD_MARKER,
    AgyOvernightGuardStore,
    evaluate_overnight_readiness,
    list_overnight_run_attempts,
    record_guard_decision,
    set_operator_pause,
)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the limited AGY overnight readiness guard")
    parser.add_argument("--db", dest="db_path", help="Override guard state DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Evaluate current guard status and list persisted runs")
    status.add_argument("--limit", type=int, default=10)

    evaluate = sub.add_parser("evaluate", help="Evaluate and persist a dry-run guard decision")
    evaluate.add_argument("--max-tasks", type=int, default=1)
    evaluate.add_argument("--agent", action="append", dest="agents", default=None)
    evaluate.add_argument("--requested-by", default="fred")
    evaluate.add_argument("--auto-merge", action="store_true")
    evaluate.add_argument("--production-deploy", action="store_true")
    evaluate.add_argument("--real-github-pr-create", action="store_true")
    evaluate.add_argument("--bulk-dispatch", action="store_true")

    sub.add_parser("pause", help="Persist operator pause=true")
    sub.add_parser("resume", help="Persist operator pause=false")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = AgyOvernightGuardStore(args.db_path)

    if args.command == "pause":
        return emit({"status": "paused", **set_operator_pause(True, db_path=args.db_path)})
    if args.command == "resume":
        return emit({"status": "resumed", **set_operator_pause(False, db_path=args.db_path)})
    if args.command == "status":
        decision = evaluate_overnight_readiness(db_path=args.db_path)
        persisted = record_guard_decision(decision, db_path=args.db_path)
        return emit(
            {
                "marker": AGY_OVERNIGHT_READINESS_GUARD_MARKER,
                "guard": decision.as_dict(),
                "persisted_decision": persisted.as_dict(),
                "recent_runs": [run.as_dict() for run in list_overnight_run_attempts(db_path=args.db_path, limit=args.limit)],
                "operator_pause": store.operator_pause(),
                "non_claims": {
                    "overnight_autopilot_active": False,
                    "auto_merge_enabled": False,
                    "bulk_agy_dispatch": False,
                    "production_deploy": False,
                    "real_github_pr_created": False,
                },
            }
        )
    if args.command == "evaluate":
        decision = evaluate_overnight_readiness(
            db_path=args.db_path,
            requested_by=args.requested_by,
            allowed_agents=args.agents or ["agy"],
            max_tasks=args.max_tasks,
            auto_merge=args.auto_merge,
            production_deploy=args.production_deploy,
            real_github_pr_create=args.real_github_pr_create,
            bulk_dispatch=args.bulk_dispatch,
        )
        persisted = record_guard_decision(decision, db_path=args.db_path)
        return emit(
            {
                "marker": AGY_OVERNIGHT_READINESS_GUARD_MARKER,
                "guard": decision.as_dict(),
                "persisted_decision": persisted.as_dict(),
                "dry_run": True,
                "tasks_launched": 0,
                "non_claims": {
                    "overnight_autopilot_active": False,
                    "auto_merge_enabled": False,
                    "bulk_agy_dispatch": False,
                    "production_deploy": False,
                    "real_github_pr_created": False,
                },
            }
        )
    parser.error(f"unsupported command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
