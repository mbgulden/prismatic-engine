"""`prismatic deploy ...` — guided multi-repo deploy onboarding.

Thin presentation layer over :mod:`pe.deploy.onboard`: formats results,
prints exactly once, maps failures to exit codes. No business logic here.
"""

from __future__ import annotations

import argparse
import sys

from pe.deploy import onboard


def register_deploy_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``deploy`` subcommand group."""
    add_p = subparsers.add_parser(
        "add-repo",
        help="Onboard a repo onto this receiver's deploy registry",
        description=(
            "Validate owner/repo, check it is reachable, register it in the "
            "deploy registry file, clone the mirror, generate and store a "
            "per-repo HMAC secret, then print the exact GitHub-side steps "
            "(workflow, runner, secrets) still needing a human. "
            "Never deploys anything."
        ),
    )
    add_p.add_argument("full_name", help="Repo to onboard, as owner/repo")
    add_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Probe reachability and report the plan; change nothing",
    )
    add_p.add_argument(
        "--no-mirror",
        action="store_true",
        help="Skip the git mirror clone (register + secret only)",
    )
    add_p.add_argument(
        "--repo-url",
        default="",
        help="Mirror source override (default https://github.com/<owner/repo>.git)",
    )
    add_p.add_argument(
        "--target-service",
        default="",
        help="Override the systemd target service (default: registry default)",
    )
    add_p.add_argument(
        "--release-prefix",
        default="",
        help="Release-dir/live-link prefix (default: derived from owner/repo)",
    )
    add_p.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP port the repo's service listens on (default: 9000)",
    )
    add_p.add_argument(
        "--extras",
        default=None,
        help="pip extras to install, or empty string for none "
        "(default: registry default)",
    )
    add_p.add_argument(
        "--smoke-import",
        default="",
        help="Python import used as the post-restart smoke test "
        "(default: prismatic.gateway.server)",
    )
    add_p.add_argument(
        "--registry-file",
        default="",
        help="Registry file override for PRISMATIC_DEPLOY_REPOS_FILE",
    )
    add_p.set_defaults(deploy_command="add-repo")

    subparsers.add_parser(
        "list-repos",
        help="Show every repo in the deploy registry and its readiness",
    ).set_defaults(deploy_command="list-repos")

    val_p = subparsers.add_parser(
        "validate-repo",
        help="Dry-run proof that a repo would deploy (no side effects)",
    )
    val_p.add_argument("full_name", help="Repo to validate, as owner/repo")
    val_p.set_defaults(deploy_command="validate-repo")


def _print_steps(steps: list[onboard.RepoStep]) -> None:
    glyph = {"ok": "ok", "would-do": "would-do", "skipped": "skipped"}
    for step in steps:
        print(f"  [{glyph.get(step.status, step.status)}] {step.name}: {step.detail}")


def run_deploy(args: argparse.Namespace) -> int:
    """Dispatch ``prismatic deploy ...``. Returns the process exit code."""
    command = getattr(args, "deploy_command", None)
    if command == "add-repo":
        options = onboard.AddRepoOptions(
            full_name=args.full_name,
            dry_run=args.dry_run,
            no_mirror=args.no_mirror,
            repo_url=args.repo_url,
            target_service=args.target_service,
            registry_file=args.registry_file,
            release_prefix=args.release_prefix,
            port=args.port,
            extras=args.extras,
            smoke_import=args.smoke_import,
        )
        try:
            result = onboard.add_repo(options)
        except onboard.OnboardError as exc:
            print(f"[Deploy] add-repo FAILED: {exc}", file=sys.stderr)
            return 1
        dry = " (dry run — nothing changed)" if args.dry_run else ""
        print(f"[Deploy] onboarded {result.full_name}{dry}")
        _print_steps(result.steps)
        if result.secret_value:
            # Shown ONCE, here. Never logged, never written anywhere else.
            print()
            print(f"  Receiver secret var : {result.secret_env_var}")
            print(f"  One-time secret     : {result.secret_value}")
            print("  Store the one-time secret as the repo's Actions secret,")
            print("  then it cannot be recovered from here again.")
        print(result.checklist)
        return 0

    if command == "list-repos":
        rows = onboard.list_repos()
        print(f"[Deploy] deploy registry: {len(rows)} repo(s)")
        for row in rows:
            mirror = "mirror-ok" if row.mirror_present else "mirror-missing"
            print(
                f"  {row.full_name}  service={row.target_service} "
                f"node={row.target_node}  {mirror}  secret={row.secret}"
            )
        return 0

    if command == "validate-repo":
        result = onboard.validate_repo(args.full_name)
        status = "VALID" if result.ok else "INVALID"
        print(f"[Deploy] {result.full_name}: {status}")
        for check in result.checks:
            glyph = "ok" if check.ok else "FAIL"
            print(f"  [{glyph}] {check.name}: {check.detail}")
        return 0 if result.ok else 1

    print("[Deploy] unknown deploy command", file=sys.stderr)
    return 2
