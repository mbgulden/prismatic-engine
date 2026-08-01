#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from prismatic.agy_unattended_window import (
    UnattendedWindowRequest,
    approve_window,
    evaluate_unattended_window,
    request_approval,
    set_pause,
    status_payload,
)


def emit(payload: dict) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") not in {"blocked", "failed"} else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate guarded AGY max_tasks=2 unattended-window control plane. No task launch.")
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--agent", default="agy")
        p.add_argument("--max-tasks", type=int, default=2)
        p.add_argument("--requested-by", default="fred")
        p.add_argument("--operator-approved", action="store_true")
        p.add_argument("--accept-nonzero-queue", action="store_true")
        p.add_argument("--auto-merge", action="store_true")
        p.add_argument("--production-deploy", action="store_true")
        p.add_argument("--real-github-pr-create", action="store_true")
        p.add_argument("--bulk-dispatch", action="store_true")
        p.add_argument("--live-linear-mutations", action="store_true")

    add_common(sub.add_parser("evaluate", help="Evaluate gate; no task launch."))
    add_common(sub.add_parser("request-approval", help="Persist approval request; no task launch."))
    add_common(sub.add_parser("approve", help="Evaluate with operator_approved=true; no task launch."))
    sub.add_parser("status", help="Read current state.")
    sub.add_parser("pause", help="Pause the unattended-window guard.")
    sub.add_parser("resume", help="Resume the unattended-window guard.")
    return parser


def request_from_args(args: argparse.Namespace) -> UnattendedWindowRequest:
    return UnattendedWindowRequest.from_mapping({
        "agent": args.agent,
        "max_tasks": args.max_tasks,
        "requested_by": args.requested_by,
        "operator_approved": getattr(args, "operator_approved", False),
        "accept_nonzero_queue": getattr(args, "accept_nonzero_queue", False),
        "auto_merge": getattr(args, "auto_merge", False),
        "production_deploy": getattr(args, "production_deploy", False),
        "real_github_pr_create": getattr(args, "real_github_pr_create", False),
        "bulk_dispatch": getattr(args, "bulk_dispatch", False),
        "live_linear_mutations": getattr(args, "live_linear_mutations", False),
    })


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "status"
    if command == "status":
        return emit(status_payload())
    if command == "evaluate":
        return emit(evaluate_unattended_window(request_from_args(args)))
    if command == "request-approval":
        return emit(request_approval(request_from_args(args)))
    if command == "approve":
        req = request_from_args(args)
        return emit(approve_window(req))
    if command == "pause":
        return emit(set_pause(True))
    if command == "resume":
        return emit(set_pause(False))
    parser.error(f"unknown command {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
