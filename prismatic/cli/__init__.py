"""
Unified user-facing CLI for Prismatic Engine.

The ``prismatic`` command is intentionally thin: it routes stable user verbs
(``status``, ``task create``) into engine modules without importing from any
agent harness. Legacy entry points remain available for compatibility.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from prismatic.cli.doctor import run as doctor_cli_run
from prismatic.local_tasks import LocalTaskQueue


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic",
        description="Prismatic Engine — local-first agent orchestration CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    status = subparsers.add_parser(
        "status", help="Show engine status and capability diagnostics"
    )
    status.add_argument("--provider", default=None, help="Check a specific provider")

    doctor = subparsers.add_parser(
        "doctor", help="Alias for status with the same diagnostics"
    )
    doctor.add_argument("--provider", default=None, help="Check a specific provider")

    init = subparsers.add_parser(
        "init", help="Initialize default Prismatic configuration"
    )
    init.add_argument(
        "--force", action="store_true", help="Overwrite existing configuration files"
    )

    serve = subparsers.add_parser("serve", help="Run the dispatcher event loop")
    serve.add_argument(
        "--once", action="store_true", help="Run one dispatcher cycle and exit"
    )
    serve.add_argument(
        "--interval", type=int, default=None, help="Polling interval in seconds"
    )
    serve.add_argument(
        "--setup-pipelines", action="store_true", help="Set up pipeline issues and exit"
    )

    task = subparsers.add_parser("task", help="Manage local tasks")
    task_subparsers = task.add_subparsers(dest="task_command")
    create = task_subparsers.add_parser(
        "create", help="Create a local task without Linear"
    )
    create.add_argument("title", help="Task description/title")
    create.add_argument(
        "--agent", default="agy", help="Agent name to dispatch to (default: agy)"
    )
    create.add_argument(
        "--workspace", default=".", help="Workspace path for the task (default: .)"
    )
    create.add_argument(
        "--db-path", default=None, help="Override SQLite DB path for local tasks"
    )

    subparsers.add_parser("skills", help="Delegate to prismatic-engine-skills")

    journal = subparsers.add_parser("journal", help="Journal continuity commands")
    journal_subparsers = journal.add_subparsers(dest="journal_command")
    journal_subparsers.add_parser("snapshot", help="Create a journal snapshot")

    visual_verify = subparsers.add_parser(
        "visual-verify",
        help="Capture multi-viewport screenshots and optionally run visual grading",
    )
    visual_verify.add_argument("args", nargs=argparse.REMAINDER)

    worktrees = subparsers.add_parser(
        "worktrees",
        help="Inspect/archive/remove stale Git worktrees",
    )
    worktrees.add_argument("args", nargs=argparse.REMAINDER)

    crons = subparsers.add_parser(
        "crons",
        help="Emit or install Prismatic Engine core cron manifests",
    )
    crons.add_argument("args", nargs=argparse.REMAINDER)

    agy = subparsers.add_parser(
        "agy", help="Canonical Google Antigravity CLI workflow and tmux transport"
    )
    agy.add_argument("args", nargs=argparse.REMAINDER)

    merge_factory = subparsers.add_parser(
        "merge-factory",
        help="Merge factory admission, leases, locks, and judge attestation commands",
    )
    merge_factory.add_argument("args", nargs=argparse.REMAINDER)

    verify = subparsers.add_parser(
        "verify",
        help="Run clean-room execution verification in an isolated temporary worktree",
    )
    verify.add_argument(
        "--clean-room",
        action="store_true",
        default=True,
        help="Spawn an ephemeral detached worktree stripped of ambient environment variables",
    )
    verify.add_argument(
        "--remote",
        default="origin",
        help="Remote name to verify reachability against (default: origin)",
    )
    verify.add_argument(
        "--branch",
        default=None,
        help="Branch name to verify reachability against (default: current branch)",
    )
    verify.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON verification payload",
    )

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command in {"status", "doctor"}:
        return doctor_cli_run(args)

    if args.command == "init":
        from prismatic.dispatcher import init_config

        init_config(force=args.force)
        return 0

    if args.command == "serve":
        from prismatic.dispatcher import main as dispatcher_main

        forwarded = ["prismatic-engine", "serve"]
        if args.once:
            forwarded.append("--once")
        if args.interval is not None:
            forwarded.extend(["--interval", str(args.interval)])
        if args.setup_pipelines:
            forwarded.append("--setup-pipelines")
        original = sys.argv[:]
        try:
            sys.argv = forwarded
            dispatcher_main()
        finally:
            sys.argv = original
        return 0

    if args.command == "task" and args.task_command == "create":
        queue = LocalTaskQueue(args.db_path)
        task = queue.create(
            title=args.title,
            agent=args.agent,
            workspace=args.workspace,
            metadata={"source": "prismatic-cli"},
        )
        print(f"Created local task {task.id} for agent:{task.agent}")
        print(f"  Workspace: {task.workspace}")
        print(f"  Status:    {task.status}")
        return 0

    if args.command == "skills":
        from prismatic.skills import cli_skills

        return int(cli_skills(sys.argv[2:]) or 0)

    if args.command == "journal" and args.journal_command == "snapshot":
        from prismatic.journal import cli_journal_snapshot

        return int(cli_journal_snapshot() or 0)

    if args.command == "visual-verify":
        from prismatic.cli.visual_verify import main as visual_verify_main

        return int(visual_verify_main(args.args) or 0)

    if args.command == "worktrees":
        from prismatic.worktree_janitor import cli as worktree_cli

        return int(worktree_cli(args.args) or 0)

    if args.command == "crons":
        from prismatic.core_crons import cli as crons_cli

        return int(crons_cli(args.args) or 0)

    if args.command == "agy":
        from prismatic.agy_cli import cli as agy_cli

        return int(agy_cli(args.args) or 0)

    if args.command == "merge-factory":
        from prismatic.cli.merge_factory import main as merge_factory_cli_main

        return int(merge_factory_cli_main(args.args) or 0)

    if args.command == "verify":
        import json
        from prismatic.quality.clean_room import CleanRoomRunner

        runner = CleanRoomRunner()
        res = runner.run_clean_room_verification(
            remote_name=args.remote,
            branch=args.branch,
            cleanup=True,
        )

        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print("=================== PRISMATIC CLEAN-ROOM VERIFICATION ===================")
            print(f"Marker:                 {res.marker}")
            print(f"Status:                 {res.status}")
            print(f"Candidate Head SHA:     {res.candidate_head}")
            print(f"Candidate Tree SHA:     {res.candidate_tree}")
            print(f"Remote Name:            {res.remote_name}")
            print(f"Remote Ref Matched:     {res.remote_ref_matched}")
            print(f"Environment Sanitized:  {res.environment_sanitized}")
            print(f"Tests Passed:           {res.tests_passed}")
            print(f"Tests Failed:           {res.tests_failed}")
            print(f"Deterministic SHA-256:  {res.deterministic_log_sha256}")
            print("=========================================================================")

        return 0 if res.status == "PASS" else 1

    parser.print_help()
    return 0


def main() -> None:
    sys.exit(run())


__all__ = ["run", "main", "doctor_cli_run"]
