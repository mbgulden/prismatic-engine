"""Unified CLI for Prismatic Web Publisher (`pwp` / `pwb`)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import main as builder_main, print_status, run_pipeline, watch_epic
from .compiler import compile_tokens_to_css, get_tokens_for_tenant
from .oauth_credentials import get_provider_status, refresh_provider_credentials
from .theme_diff import check_theme_compatibility, diff_theme_packages
from .theme_validator import validate_theme_package


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="pwp",
        description="Prismatic Web Publisher / Builder CLI (`pwp` / `pwb`)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Pipeline / Builder commands
    p_run = subparsers.add_parser("run", help="Run full pipeline: ingest -> synthesize -> distill")
    p_run.add_argument("--client", required=True, help="Client slug (e.g. meridian-womens-defense)")
    p_run.add_argument("--skip-agy", action="store_true", help="Skip AGY calls in synthesis")
    p_run.add_argument("--dry-run", action="store_true", help="Dry run without file/issue side effects")

    p_watch = subparsers.add_parser("watch", help="Watch Linear epic progress")
    p_watch.add_argument("--epic", required=True, help="Epic UUID")
    p_watch.add_argument("--interval", type=int, default=60, help="Poll interval seconds")
    p_watch.add_argument("--max-runtime", type=int, default=86400, help="Max runtime seconds")

    p_status = subparsers.add_parser("status", help="Print epic status")
    p_status.add_argument("--epic", required=True, help="Epic UUID")

    # 2. Theme commands
    p_theme = subparsers.add_parser("theme", help="PWP theme operations")
    t_sub = p_theme.add_subparsers(dest="theme_command", required=True)

    t_val = t_sub.add_parser("validate", help="Validate PWP theme package against schemas")
    t_val.add_argument("theme_dir", help="Path to theme directory")
    t_val.add_argument("--json", action="store_true", help="Output JSON results")

    t_diff = t_sub.add_parser("diff", help="Semantic diff between two themes")
    t_diff.add_argument("--from", dest="from_theme", required=True, help="Source theme directory")
    t_diff.add_argument("--to", dest="to_theme", required=True, help="Target theme directory")
    t_diff.add_argument("--engine-version", help="Engine version semver")
    t_diff.add_argument("--json", action="store_true", help="Output JSON results")

    t_compat = t_sub.add_parser("check-compat", help="Check theme compatibility with PE semver")
    t_compat.add_argument("theme_dir", help="Path to theme directory")
    t_compat.add_argument("--engine-version", required=True, help="Target PE semver version")
    t_compat.add_argument("--json", action="store_true", help="Output JSON results")

    # 3. Credentials commands
    p_cred = subparsers.add_parser("credentials", help="OAuth credential operations")
    c_sub = p_cred.add_subparsers(dest="cred_command", required=True)

    c_stat = c_sub.add_parser("status", help="Get secret-redacted credential status")
    c_stat.add_argument("provider", help="Provider name (e.g., ubersuggest)")
    c_stat.add_argument("--verify", action="store_true", help="Perform live verification")

    c_ref = c_sub.add_parser("refresh", help="Rotate refresh token for provider")
    c_ref.add_argument("provider", help="Provider name")

    # 4. Integration commands
    p_integ = subparsers.add_parser("integration", help="Prismatic Engine integration operations")
    i_sub = p_integ.add_subparsers(dest="integ_command", required=True)
    i_sub.add_parser("status", help="Get PE connection status")
    i_sub.add_parser("connect", help="Mark PWP connected in PE")
    i_sub.add_parser("disconnect", help="Mark PWP disconnected in PE")
    i_sub.add_parser("refresh", help="Refresh PWP manifest & capabilities in PE")

    args = parser.parse_args(argv)

    if args.command == "run":
        res = run_pipeline(args.client, skip_agy=args.skip_agy, dry_run=args.dry_run)
        print(json.dumps(res, indent=2))
        return 0 if res.get("status") == "ok" else 1

    elif args.command == "watch":
        return watch_epic(args.epic, poll_interval=args.interval, max_runtime=args.max_runtime)

    elif args.command == "status":
        return print_status(args.epic)

    elif args.command == "theme":
        if args.theme_command == "validate":
            valid, errors = validate_theme_package(Path(args.theme_dir))
            out = {"valid": valid, "errors": errors}
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                print(f"Theme Valid: {valid}")
                for err in errors:
                    print(f" - {err}")
            return 0 if valid else 1

        elif args.theme_command == "diff":
            res = diff_theme_packages(Path(args.from_theme), Path(args.to_theme), engine_version=args.engine_version)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Theme Diff ({args.from_theme} -> {args.to_theme}):")
                print(json.dumps(res, indent=2))
            return 0

        elif args.theme_command == "check-compat":
            res = check_theme_compatibility(Path(args.theme_dir), args.engine_version)
            if args.json:
                print(json.dumps(res.as_dict(), indent=2))
            else:
                print(f"Engine Compatibility ({args.engine_version}):")
                print(json.dumps(res.as_dict(), indent=2))
            return 0 if res.ok else 1


    elif args.command == "credentials":
        if args.cred_command == "status":
            st = get_provider_status(args.provider, verify=args.verify)
            print(json.dumps(st, indent=2))
            return 0
        elif args.cred_command == "refresh":
            rf = refresh_provider_credentials(args.provider)
            print(json.dumps(rf, indent=2))
            return 0

    elif args.command == "integration":
        out = {
            "status": "connected",
            "plugin": "prismatic-web-publisher",
            "version": "0.1.0",
            "capabilities": ["ingest", "synthesize", "distill", "builder", "compiler", "theme_validator", "theme_diff", "oauth_credentials", "publish_kpi_tracker"],
            "command": args.integ_command
        }
        print(json.dumps(out, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
