"""
Unified user-facing CLI for Prismatic Engine.

The ``prismatic`` command is intentionally thin: it routes stable user verbs
(``status``, ``task create``) into engine modules without importing from any
agent harness. Legacy entry points remain available for compatibility.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from prismatic.cli.doctor import run as doctor_cli_run
from prismatic.local_tasks import LocalTaskQueue


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic",
        description="Prismatic Engine — local-first agent orchestration CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    status = subparsers.add_parser("status", help="Show engine status and capability diagnostics")
    status.add_argument("--provider", default=None, help="Check a specific provider")

    doctor = subparsers.add_parser("doctor", help="Alias for status with the same diagnostics")
    doctor.add_argument("--provider", default=None, help="Check a specific provider")

    init = subparsers.add_parser("init", help="Initialize default Prismatic configuration")
    init.add_argument("--force", action="store_true", help="Overwrite existing configuration files")

    serve = subparsers.add_parser("serve", help="Run the dispatcher event loop")
    serve.add_argument("--once", action="store_true", help="Run one dispatcher cycle and exit")
    serve.add_argument("--interval", type=int, default=None, help="Polling interval in seconds")
    serve.add_argument("--setup-pipelines", action="store_true", help="Set up pipeline issues and exit")

    task = subparsers.add_parser("task", help="Manage local tasks")
    task_subparsers = task.add_subparsers(dest="task_command")
    create = task_subparsers.add_parser("create", help="Create a local task without Linear")
    create.add_argument("title", help="Task description/title")
    create.add_argument("--agent", default="agy", help="Agent name to dispatch to (default: agy)")
    create.add_argument("--workspace", default=".", help="Workspace path for the task (default: .)")
    create.add_argument("--db-path", default=None, help="Override SQLite DB path for local tasks")

    subparsers.add_parser("skills", help="Delegate to prismatic-engine-skills")

    journal = subparsers.add_parser("journal", help="Journal continuity commands")
    journal_subparsers = journal.add_subparsers(dest="journal_command")
    journal_subparsers.add_parser("snapshot", help="Create a journal snapshot")

    fw = subparsers.add_parser(
        "fleet-watchdog",
        help="Run the standalone engine fleet health watchdog (checks + auto-actions)",
    )
    fw.add_argument(
        "--dry-run", action="store_true",
        help="Don't take auto-actions, just report"
    )
    fw.add_argument(
        "--json", action="store_true",
        help="Machine-readable JSON output (no actions)"
    )

    # lock — central file-lock registry (multi-agent coordination)
    lock = subparsers.add_parser(
        "lock", help="Manage the centralized file-lock registry",
    )
    lock_subparsers = lock.add_subparsers(dest="lock_command", required=True)
    p_lock = lock_subparsers.add_parser("lock", help="Claim a file for an agent")
    p_lock.add_argument("file", help="File path (repo-relative or absolute)")
    p_lock.add_argument("agent", help="Agent identifier (e.g., fred, kai, ned)")
    p_unlock = lock_subparsers.add_parser("unlock", help="Release a file lock")
    p_unlock.add_argument("file", help="File path")
    p_unlock.add_argument("agent", help="Agent identifier")
    lock_subparsers.add_parser("status", help="Show all active locks")
    p_hb = lock_subparsers.add_parser("heartbeat", help="Refresh lock heartbeat")
    p_hb.add_argument("file", help="File path")
    p_hb.add_argument("agent", help="Agent identifier")

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    argv_list = list(argv) if argv is not None else None
    args = parser.parse_args(argv_list)

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

    if args.command == "fleet-watchdog":
        from prismatic.fleet_watchdog import main as fw_main
        # Re-invoke with our args by patching argv
        original = sys.argv[:]
        try:
            forwarded = ["prismatic-fleet-watchdog"]
            if args.dry_run:
                forwarded.append("--dry-run")
            if args.json:
                forwarded.append("--json")
            sys.argv = forwarded
            return int(fw_main() or 0)
        finally:
            sys.argv = original

    if args.command == "lock":
        # Forward to prismatic.lock.main() — it has its own argparse for the
        # subcommands (lock/unlock/status/heartbeat). We strip our outer "lock"
        # and pass the subcommand + args through.
        from prismatic.lock import main as lock_main
        original_argv = sys.argv[:]
        try:
            # Build forwarded argv from argv_list (what was passed to run()).
            # When invoked as `prismatic lock status`, argv_list = ["lock", "status"].
            # When invoked as `prismatic lock lock foo.py fred`,
            # argv_list = ["lock", "lock", "foo.py", "fred"].
            # We strip the outer "lock" (first element) and keep the rest.
            if argv_list is None:
                # Fall back to sys.argv for tests/programs that didn't pass argv
                argv_list = sys.argv[1:]
            forwarded = ["prismatic-lock"] + argv_list[1:]
            sys.argv = forwarded
            lock_main()  # calls sys.exit() with cmd_* return code
            return 0  # unreachable — lock_main sys.exits
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0
        finally:
            sys.argv = original_argv

    parser.print_help()
    return 0


def main() -> None:
    sys.exit(run())


__all__ = ["run", "main", "doctor_cli_run"]
