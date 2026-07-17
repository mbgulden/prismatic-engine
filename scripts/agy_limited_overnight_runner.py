#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from prismatic.agy_limited_overnight_runner import (
    RunnerRequest,
    run_limited_overnight_dry_run,
    status_payload,
    stop_latest_run,
)


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded AGY limited overnight dry-run runner")
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--max-tasks", type=int, default=1)
        p.add_argument("--agent", default="agy")
        p.add_argument("--model", default="Gemini 3.5 Flash (Medium)")
        p.add_argument("--requested-by", default="fred")
        p.add_argument("--auto-merge", action="store_true")
        p.add_argument("--production-deploy", action="store_true")
        p.add_argument("--real-github-pr-create", action="store_true")
        p.add_argument("--bulk-dispatch", action="store_true")

    add_common(sub.add_parser("preflight", help="evaluate guard/model policy without launching AGY"))
    add_common(sub.add_parser("dry-run", help="launch exactly one guarded AGY dry-run task"))
    sub.add_parser("status", help="read persisted runner state")
    sub.add_parser("stop", help="persist operator stop for latest run")

    args = parser.parse_args(argv)
    command = args.command or "status"
    if command == "status":
        print_json(status_payload())
        return 0
    if command == "stop":
        print_json(stop_latest_run())
        return 0
    req = RunnerRequest.from_mapping({
        "max_tasks": args.max_tasks,
        "agent": args.agent,
        "allowed_agents": [args.agent],
        "model": args.model,
        "requested_by": args.requested_by,
        "auto_merge": args.auto_merge,
        "production_deploy": args.production_deploy,
        "real_github_pr_create": args.real_github_pr_create,
        "bulk_dispatch": args.bulk_dispatch,
        "stop_on_first_failure": True,
    })
    result = run_limited_overnight_dry_run(req, execute=(command == "dry-run"))
    print_json(result)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
