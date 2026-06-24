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
from typing import Any, Sequence

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

    providers = subparsers.add_parser("providers", help="Manage external providers")
    providers_subparsers = providers.add_subparsers(dest="providers_command", required=True)
    providers_subparsers.add_parser("list", help="List supported external providers")
    attach = providers_subparsers.add_parser("attach", help="Validate and store credentials for a provider")
    attach.add_argument("name", help="Provider name (e.g. github, linear, agy, telegram)")
    attach.add_argument("--token", default=None, help="Credential token to validate and store")
    attach.add_argument("--repository", default=None, help="Repository target for vcs providers")
    attach.add_argument("--config-path", default=None, help="Override user config path for the attach")

    journal = subparsers.add_parser("journal", help="Journal continuity commands")
    journal_subparsers = journal.add_subparsers(dest="journal_command")
    journal_subparsers.add_parser("snapshot", help="Create a journal snapshot")

    return parser


def _providers_attach(
    *,
    name: str,
    token: str | None,
    repository: str | None,
    config_path: str | None,
) -> Any:
    from prismatic.providers.registry import CredentialRegistry

    registry = CredentialRegistry(config_path=config_path) if config_path else CredentialRegistry()
    return registry.attach(name, token=token, repository=repository)


# Backward-compatible alias for tests importing the internal helper.
_registry_attach = _providers_attach


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

    if args.command == "providers" and getattr(args, "providers_command", None) == "attach":
        result = _providers_attach(
            name=args.name,
            token=args.token,
            repository=args.repository,
            config_path=args.config_path,
        )
        if result.ok:
            print(f"✓ Attached {args.name}: {result.credential_source}")
            if result.path:
                print(f"  Wrote: {result.path}")
            return 0
        print(f"✗ Failed to attach {args.name}: {result.error}")
        if result.remediation:
            print(f"  Remediation: {result.remediation}")
        return 1

    if args.command == "providers" and getattr(args, "providers_command", None) == "list":
        from prismatic.providers.registry import supported_providers

        print("Supported providers:")
        for provider in supported_providers():
            print(f"- {provider}")
        return 0

    if args.command == "journal" and args.journal_command == "snapshot":
        from prismatic.journal import cli_journal_snapshot

        return int(cli_journal_snapshot() or 0)

    parser.print_help()
    return 0


def main() -> None:
    sys.exit(run())


__all__ = ["run", "main", "doctor_cli_run"]
