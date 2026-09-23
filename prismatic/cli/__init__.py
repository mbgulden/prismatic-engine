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
from prismatic.cli.deploy import register_deploy_commands, run_deploy
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

    deploy = subparsers.add_parser(
        "deploy", help="Onboard and validate repos on the post-merge deploy registry"
    )
    deploy_subparsers = deploy.add_subparsers(dest="deploy_command")
    register_deploy_commands(deploy_subparsers)

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

    skills = subparsers.add_parser("skills", help="Manage and sync Prismatic Engine skills")
    skills_subparsers = skills.add_subparsers(dest="skills_command")
    skills_sync = skills_subparsers.add_parser("sync", help="Bootstrap and sync skills into workspace")
    skills_sync.add_argument("--target", default=None, help="Target workspace path (default: cwd)")
    skills_sync.add_argument("--force", action="store_true", help="Force overwrite existing skills")
    skills_sync.add_argument("--json", action="store_true", help="Emit machine-readable JSON payload")

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

    progress = subparsers.add_parser(
        "progress",
        help="Show Jev level-up progress meter (read-only)",
    )
    progress.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output"
    )
    progress.add_argument(
        "--audit-dir",
        default=None,
        help="Override the audit directory (default: ~/.prismatic/audit)",
    )

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

    verify_packet = subparsers.add_parser(
        "verify-packet",
        help="Replay machine verification packet and assert Deterministic Log Digest (DLD) hash match",
    )
    verify_packet.add_argument(
        "packet_file",
        help="Path to result-packet.json machine artifact",
    )
    verify_packet.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON verification payload",
    )

    receipt_run = subparsers.add_parser(
        "receipt-run",
        help="Provider-neutral clean-room verification receipt runner (GRO-4203)",
    )
    receipt_run.add_argument("args", nargs=argparse.REMAINDER)

    exec_cmd = subparsers.add_parser(
        "exec",
        help="Execute command wrapped inside a fail-safe SwarmLock lease and telemetry envelope",
    )
    exec_cmd.add_argument("args", nargs=argparse.REMAINDER)

    worker = subparsers.add_parser(
        "worker",
        help="Run headless distributed worker daemon to poll and execute tasks",
    )
    worker.add_argument(
        "--gateway",
        default=None,
        help="Prismatic Gateway URL (e.g. http://localhost:9000 or http://100.x.y.z:9000)",
    )
    worker.add_argument(
        "--node-id",
        default=None,
        help="Unique node identifier for this worker instance (default: hostname)",
    )
    worker.add_argument(
        "--tags",
        default="general",
        help="Comma-separated capability tags (e.g. general,gpu,linux,python)",
    )
    worker.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Queue polling interval in seconds (default: 2.0)",
    )
    worker.add_argument(
        "--once",
        action="store_true",
        help="Poll and execute at most one job, then exit",
    )
    worker.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Exit after executing this many jobs",
    )
    worker.add_argument(
        "--token",
        default=None,
        help="Control plane auth token (or PRISMATIC_WORKER_TOKEN)",
    )

    fleet = subparsers.add_parser(
        "fleet", help="Hermes agent fleet management and automated session hygiene"
    )
    fleet_subparsers = fleet.add_subparsers(dest="fleet_command")

    fleet_status = fleet_subparsers.add_parser(
        "status", help="Inspect health, session token sizes, and services across fleet"
    )
    fleet_status.add_argument(
        "--dynamic", action="store_true", help="Dynamically query upstream vLLM models"
    )
    fleet_status.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output"
    )

    fleet_sync = fleet_subparsers.add_parser(
        "sync", help="Zero-touch sync of compression limits, telemetry, and systemd fleet template"
    )
    fleet_sync.add_argument(
        "--threshold-tokens",
        type=int,
        default=None,
        help="Token threshold cap for compression override (default: dynamic 75%% of context window)",
    )
    fleet_sync.add_argument(
        "--context-window",
        type=int,
        default=None,
        help="Model context window length override (default: detected or 65536)",
    )
    fleet_sync.add_argument(
        "--dynamic",
        action="store_true",
        help="Dynamically query upstream vLLM for registered models to detect context window",
    )
    fleet_sync.add_argument(
        "--threshold-messages",
        type=int,
        default=40,
        help="Message count threshold for session hygiene (default: 40)",
    )
    fleet_sync.add_argument(
        "--no-reset",
        action="store_true",
        help="Skip resetting currently bloated or degenerate sessions",
    )
    fleet_sync.add_argument(
        "--no-service",
        action="store_true",
        help="Skip installing systemd fleet template service",
    )
    fleet_sync.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output"
    )

    fleet_reset = fleet_subparsers.add_parser(
        "reset", help="Reset/rotate active session for a specific Hermes profile"
    )
    fleet_reset.add_argument("profile", help="Profile name (e.g. orchestrator, george, kai)")
    fleet_reset.add_argument(
        "--session-key",
        default=None,
        help="Specific session key to reset (defaults to active routing session)",
    )
    fleet_reset.add_argument(
        "--no-restart",
        action="store_true",
        help="Skip restarting systemd gateway service after reset",
    )
    fleet_reset.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output"
    )

    fleet_hygiene = fleet_subparsers.add_parser(
        "auto-hygiene", help="Autonomous background hygiene pass to rotate bloated sessions"
    )
    fleet_hygiene.add_argument(
        "--threshold-tokens",
        type=int,
        default=None,
        help="Token threshold cap for hygiene override (default: dynamic 75%% of context window)",
    )
    fleet_hygiene.add_argument(
        "--context-window",
        type=int,
        default=None,
        help="Model context window length override (default: detected or 65536)",
    )
    fleet_hygiene.add_argument(
        "--dynamic",
        action="store_true",
        help="Dynamically query upstream vLLM for registered models to detect context window",
    )
    fleet_hygiene.add_argument(
        "--threshold-messages",
        type=int,
        default=40,
        help="Message count threshold for hygiene (default: 40)",
    )
    fleet_hygiene.add_argument(
        "--dry-run", action="store_true", help="Inspect without modifying state.db"
    )
    fleet_hygiene.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output"
    )

    fleet_compress = fleet_subparsers.add_parser(
        "compress",
        help="Trigger state-preserving operational context compression for a Hermes profile",
    )
    fleet_compress.add_argument(
        "profile", help="Profile name (e.g. orchestrator, george, kai)"
    )
    fleet_compress.add_argument(
        "--session-key",
        default=None,
        help="Specific session key to compress (defaults to active routing session)",
    )
    fleet_compress.add_argument(
        "--threshold-tokens",
        type=int,
        default=None,
        help="Token threshold cap for compression override (default: dynamic 75%% of context window)",
    )
    fleet_compress.add_argument(
        "--context-window",
        type=int,
        default=None,
        help="Model context window length override (default: detected or 65536)",
    )
    fleet_compress.add_argument(
        "--dynamic",
        action="store_true",
        help="Dynamically query upstream vLLM for registered models to detect context window",
    )
    fleet_compress.add_argument(
        "--force",
        action="store_true",
        help="Force compression even if token count is below 75%% threshold",
    )
    fleet_compress.add_argument(
        "--dry-run", action="store_true", help="Inspect without modifying state.db"
    )
    fleet_compress.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON output"
    )

    fleet_chat = fleet_subparsers.add_parser(
        "chat",
        help="Launch interactive Hermes chat session with automatic daemon conflict prevention (HTTP 409)",
    )
    fleet_chat.add_argument(
        "--profile",
        default="default",
        help="Hermes profile to chat with (e.g. orchestrator, george, kai, ned)",
    )
    fleet_chat.add_argument(
        "--auto-pause",
        action="store_true",
        default=True,
        help="Automatically pause running systemd gateway service and restore on exit (default: True)",
    )
    fleet_chat.add_argument(
        "--no-auto-pause",
        dest="auto_pause",
        action="store_false",
        help="Do not pause running systemd service; warn operator instead",
    )
    fleet_chat.add_argument("args", nargs=argparse.REMAINDER)

    chat = subparsers.add_parser(
        "chat",
        help="Interactive Hermes chat session with automatic daemon conflict prevention (HTTP 409)",
    )
    chat.add_argument(
        "--profile",
        default="default",
        help="Hermes profile to chat with (e.g. orchestrator, george, kai, ned)",
    )
    chat.add_argument(
        "--auto-pause",
        action="store_true",
        default=True,
        help="Automatically pause running systemd gateway service and restore on exit (default: True)",
    )
    chat.add_argument(
        "--no-auto-pause",
        dest="auto_pause",
        action="store_false",
        help="Do not pause running systemd service; warn operator instead",
    )
    chat.add_argument("args", nargs=argparse.REMAINDER)

    return parser





def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args, extra = parser.parse_known_args(list(argv) if argv is not None else None)

    if args.command in {"status", "doctor"}:
        return doctor_cli_run(args)

    if args.command == "deploy":
        return run_deploy(args)

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
        if getattr(args, "skills_command", None) == "sync":
            import json
            from pathlib import Path
            from prismatic.skills.bootstrapper import SkillBootstrapper

            bootstrapper = SkillBootstrapper()
            target_path = Path(args.target) if args.target else None
            res = bootstrapper.sync_skills(target_workspace=target_path, force=args.force)

            if args.json:
                print(json.dumps(res.to_dict(), indent=2))
            else:
                print("=================== PRISMATIC SKILLS SYNC ===================")
                print(f"Marker:                 {res.marker}")
                print(f"Status:                 {res.status}")
                print(f"Target Workspace:       {res.target_workspace}")
                print(f"Rules Synced:           {res.agents_rules_synced}")
                print(f"Skills Synced Count:    {res.skills_synced_count}")
                print(f"Dual-Tree Matched:      {res.dual_tree_matched}")
                print(f"Skill Tree SHA-256:     {res.skill_tree_sha256}")
                print("=============================================================")

            return 0 if res.status == "PASS" else 1

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

    if args.command == "progress":
        from prismatic.cli.progress import main as progress_cli_main

        forwarded: list[str] = []
        if args.json:
            forwarded.append("--json")
        if args.audit_dir:
            forwarded.extend(["--audit-dir", args.audit_dir])
        return int(progress_cli_main(forwarded) or 0)

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

    if args.command == "verify-packet":
        import json
        from pathlib import Path
        from prismatic.quality.log_normalizer import verify_packet_file

        res = verify_packet_file(Path(args.packet_file))
        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print("=================== PRISMATIC PACKET REPLAY VERIFICATION ===================")
            print(f"Marker:                 {res.marker}")
            print(f"Status:                 {res.status}")
            print(f"Packet ID:              {res.packet_id}")
            print(f"Candidate Head SHA:     {res.candidate_head}")
            print(f"Candidate Tree SHA:     {res.candidate_tree}")
            print(f"Claimed DLD SHA-256:    {res.claimed_dld_sha256}")
            print(f"Recomputed DLD SHA-256: {res.recomputed_dld_sha256}")
            print(f"DLD Bytes Matched:      {res.dld_bytes_matched}")
            print("==========================================================================")

        return 0 if res.status == "PASS" else 1

    if args.command == "receipt-run":
        from prismatic.verification.receipt_runner import cli as receipt_runner_cli

        raw_argv = list(argv) if argv is not None else sys.argv[1:]
        try:
            cmd_idx = raw_argv.index("receipt-run")
            forwarded = raw_argv[cmd_idx + 1 :]
        except ValueError:
            forwarded = list(args.args or []) + extra
        return int(receipt_runner_cli(forwarded) or 0)

    if args.command == "exec":
        from prismatic.client.exec import run_exec_cli

        raw_argv = list(argv) if argv is not None else sys.argv[1:]
        try:
            cmd_idx = raw_argv.index("exec")
            forwarded = raw_argv[cmd_idx + 1 :]
        except ValueError:
            forwarded = list(args.args or []) + extra
        return int(run_exec_cli(forwarded) or 0)

    if args.command == "worker":
        from prismatic.worker.daemon import run_worker_daemon

        tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
        return run_worker_daemon(
            gateway_url=args.gateway,
            node_id=args.node_id,
            tags=tag_list,
            poll_interval=args.poll_interval,
            once=args.once,
            max_jobs=args.max_jobs,
            token=args.token,
        )

    if args.command == "fleet":
        import json
        from prismatic.fleet import PrismaticFleetManager

        mgr = PrismaticFleetManager()

        if args.fleet_command == "status":
            profiles = mgr.discover_profiles(dynamic=args.dynamic)
            if args.json:
                print(json.dumps([p.to_dict() for p in profiles], indent=2))
                return 0

            print("================================ HERMES FLEET STATUS ================================")
            print(f"{'Profile':<15} {'Context':<9} {'Thresh(75%)':<12} {'Headroom':<10} {'Tokens':<9} {'Util %':<8} {'Active':<7} {'Health':<10}")
            print("-" * 88)
            for p in profiles:
                svc_status = "UP" if p.systemd_active else "DOWN"
                active_sessions = p.sessions
                ctx_str = f"{p.context_window:,}" if p.context_window else "-"
                thresh_str = f"{p.compression_threshold_tokens:,}" if p.compression_threshold_tokens else "-"
                headroom_str = f"{p.headroom_tokens:,}" if p.headroom_tokens else "-"
                if not active_sessions:
                    print(f"{p.name:<15} {ctx_str:<9} {thresh_str:<12} {headroom_str:<10} {'-':<9} {'-':<8} {svc_status:<7} {'IDLE':<10}")
                else:
                    for s in active_sessions:
                        tok_str = f"{s.last_prompt_tokens:,}"
                        util_str = f"{(s.last_prompt_tokens / p.context_window * 100):.1f}%" if p.context_window else "-"
                        print(f"{p.name:<15} {ctx_str:<9} {thresh_str:<12} {headroom_str:<10} {tok_str:<9} {util_str:<8} {svc_status:<7} {s.health.value:<10}")
                        if s.health.value != "HEALTHY":
                            print(f"  └─ Issue: {s.health_reason}")
            print("=====================================================================================")
            return 0

        if args.fleet_command == "sync":
            res = mgr.sync_fleet(
                threshold_tokens=args.threshold_tokens,
                threshold_messages=args.threshold_messages,
                context_window=args.context_window,
                dynamic=args.dynamic,
                reset_bloated=not args.no_reset,
                install_service=not args.no_service,
            )
            if args.json:
                print(json.dumps(res, indent=2))
                return 0

            print("======================== HERMES FLEET SYNC =========================")
            print(f"Global Plugins:     {res['global_plugins']['status']}")
            if 'systemd_template' in res:
                print(f"Systemd Template:   {res['systemd_template']['status']} ({res['systemd_template'].get('template_path', '')})")
            if 'database_wal_migration' in res:
                wal_info = res['database_wal_migration']
                print(f"WAL DBs Migrated:   {wal_info.get('migrated_count', 0)} ({wal_info.get('errors_count', 0)} errors)")
            print(f"Profiles Synced:    {len(res['config_sync'])}")
            if 'hygiene_actions' in res:
                actions = res['hygiene_actions']['actions_taken']
                print(f"Sessions Reset:     {len(actions)}")
                for act in actions:
                    print(f"  └─ Reset {act['profile']} [{act['health']} - {act['tokens']:,} tokens]: {act.get('reset_result', {}).get('status')}")
            print("====================================================================")
            return 0

        if args.fleet_command == "reset":
            res = mgr.reset_profile_session(
                profile=args.profile,
                session_key=args.session_key,
                reason="manual_cli_reset",
                restart_gateway=not args.no_restart,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Profile: {args.profile}")
                print(f"Status:  {res.get('status')}")
                if "resets" in res:
                    for r in res["resets"]:
                        print(f"  Rotated session: {r['old_session_id']} -> {r['new_session_id']}")
                if res.get("restarted_service"):
                    print(f"  Restarted service: {res['restarted_service']}")
            return 0 if res.get("status") == "SUCCESS" else 1

        if args.fleet_command == "auto-hygiene":
            res = mgr.run_auto_hygiene(
                threshold_tokens=args.threshold_tokens,
                threshold_messages=args.threshold_messages,
                context_window=args.context_window,
                dynamic=args.dynamic,
                dry_run=args.dry_run,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Profiles Scanned: {res['profiles_scanned']}")
                print(f"Actions Taken:    {len(res['actions_taken'])}")
                for act in res["actions_taken"]:
                    print(f"  - [{act['health']}] {act['profile']} ({act['tokens']:,} tokens, {act['messages']} msgs): {act.get('reset_result')}")
            return 0

        if args.fleet_command == "compress":
            res = mgr.check_and_compress_profile(
                profile=args.profile,
                session_key=args.session_key,
                threshold_tokens=args.threshold_tokens,
                context_window=args.context_window,
                dynamic=args.dynamic,
                force=args.force,
                dry_run=args.dry_run,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Profile:    {args.profile}")
                print(f"Status:     {res.get('status')}")
                if res.get("compressed"):
                    print(f"Tokens:     {res.get('pre_tokens'):,} -> {res.get('post_tokens'):,} tokens")
                    preserved = res.get("state_preserved", {})
                    print(f"Task ID:    {preserved.get('task_id')}")
                    print(f"Held Locks: {preserved.get('held_locks')}")
                    print(f"Git HEAD:   {preserved.get('git_head')}")
                    print(f"Next Step:  {preserved.get('next_step')}")
                    print(f"Gate:       {'Programmatically Prepended' if res.get('programmatically_prepended') else 'Preserved by Summarizer'}")
                else:
                    print(f"Reason:     {res.get('reason')}")
            return 0 if res.get("status") in ("COMPRESSED", "HEALTHY", "DRY_RUN") else 1

    if args.command == "chat" or (args.command == "fleet" and getattr(args, "fleet_command", None) == "chat"):
        from prismatic.fleet.telegram import run_hermes_chat_with_guard

        raw_argv = list(argv) if argv is not None else sys.argv[1:]
        try:
            cmd_idx = raw_argv.index("chat")
            remaining = raw_argv[cmd_idx + 1:]
        except ValueError:
            remaining = list(args.args or []) + extra

        forwarded: list[str] = []
        skip_next = False
        for a in remaining:
            if skip_next:
                skip_next = False
                continue
            if a in {"--auto-pause", "--no-auto-pause"}:
                continue
            if a == "--profile":
                skip_next = True
                continue
            if a.startswith("--profile="):
                continue
            forwarded.append(a)

        return run_hermes_chat_with_guard(
            profile=args.profile,
            extra_args=forwarded,
            auto_pause=args.auto_pause,
        )

    parser.print_help()

    return 0


def main() -> None:
    sys.exit(run())


from prismatic.fleet.telegram import (
    check_hermes_daemon_collision,
    daemon_collision_guard,
    run_hermes_chat_with_guard,
)

__all__ = [
    "run",
    "main",
    "doctor_cli_run",
    "check_hermes_daemon_collision",
    "daemon_collision_guard",
    "run_hermes_chat_with_guard",
]
