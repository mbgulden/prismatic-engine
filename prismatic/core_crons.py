"""Prismatic Engine core cron manifests.

Core crons are scheduler-neutral data plus renderer helpers. A workstation,
server, container, or Hermes profile can install the same commands without
copying Ned-specific scripts.
"""

from __future__ import annotations

import json
import subprocess
from typing import Sequence

from prismatic.worktree_janitor import crontab_lines, default_core_crons, resolve_repo


def emit(repo: str | None = None, *, fmt: str = "crontab") -> str:
    """Render built-in PE cron manifests."""
    if fmt == "json":
        return json.dumps(default_core_crons(), indent=2)
    if fmt == "crontab":
        return "\n".join(crontab_lines(repo)) + "\n"
    raise ValueError(f"unsupported cron format: {fmt}")


def install_user_crontab(repo: str | None = None, *, marker: str = "PRISMATIC_ENGINE_CORE_CRONS") -> str:
    """Install PE core crons into the current user's crontab.

    Existing managed block is replaced. Unmanaged crontab lines are preserved.
    """
    block = [f"# BEGIN {marker}", emit(repo, fmt="crontab").rstrip(), f"# END {marker}"]
    existing = subprocess.run(["crontab", "-l"], text=True, capture_output=True, check=False)
    current = existing.stdout if existing.returncode == 0 else ""
    lines = current.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == f"# BEGIN {marker}":
            skipping = True
            continue
        if line.strip() == f"# END {marker}":
            skipping = False
            continue
        if not skipping:
            out.append(line)
    if out and out[-1].strip():
        out.append("")
    out.extend(block)
    new_cron = "\n".join(out).rstrip() + "\n"
    subprocess.run(["crontab", "-"], input=new_cron, text=True, check=True)
    return new_cron


def cli(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="prismatic crons")
    sub = parser.add_subparsers(dest="command")
    emit_p = sub.add_parser("emit", help="Print core cron manifests")
    emit_p.add_argument("--repo", default=None)
    emit_p.add_argument("--format", choices=["crontab", "json"], default="crontab")
    install = sub.add_parser("install", help="Install core crons into user crontab")
    install.add_argument("--repo", default=None)
    install.add_argument("--yes", action="store_true", help="Required safety confirmation")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "emit":
        print(emit(args.repo, fmt=args.format), end="")
        return 0
    if args.command == "install":
        if not args.yes:
            parser.error("install mutates your user crontab; pass --yes")
        install_user_crontab(str(resolve_repo(args.repo)))
        print("Installed Prismatic Engine core crons into user crontab")
        return 0
    parser.print_help()
    return 0
