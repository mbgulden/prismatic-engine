"""Core worktree hygiene and janitor utilities for Prismatic Engine.

The janitor is intentionally harness-agnostic: it operates on any Git checkout,
uses only Git + Python stdlib, and archives dirty state before removal. It is
safe by default (`dry_run=True`) so API and cron integrations can report first
and mutate only when explicitly asked.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclasses.dataclass(slots=True)
class WorktreeRecord:
    path: str
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    exists: bool = True
    status_header: str = ""
    dirty: bool = False
    unmerged: bool = False
    merged_to_base: bool = False
    ahead_behind: str | None = None
    age_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(slots=True)
class JanitorResult:
    repo: str
    base_ref: str
    archive_dir: str
    dry_run: bool
    total: int
    canonical: str
    removable: list[dict[str, Any]]
    kept: list[dict[str, Any]]
    removed: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _run(args: Sequence[str], cwd: str | Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr}"
        )
    return result


def resolve_repo(repo: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the Git repo root used by PE worktree APIs/CLI/crons."""
    start = Path(repo or os.environ.get("PRISMATIC_REPO_DIR") or os.getcwd()).resolve()
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=start)
    if result.returncode != 0:
        raise RuntimeError(f"not a git repository: {start}")
    return Path(result.stdout.strip()).resolve()


def _parse_worktree_porcelain(raw: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    if current:
        records.append(current)
    return records


def list_worktrees(
    repo: str | os.PathLike[str] | None = None,
    *,
    base_ref: str = "origin/main",
) -> list[WorktreeRecord]:
    """Return registered Git worktrees with dirty/merge hygiene metadata."""
    repo_root = resolve_repo(repo)
    raw = _run(["git", "worktree", "list", "--porcelain"], cwd=repo_root, check=True).stdout
    out: list[WorktreeRecord] = []
    now = datetime.now(timezone.utc).timestamp()
    for item in _parse_worktree_porcelain(raw):
        path = Path(str(item["worktree"])).resolve()
        branch = item.get("branch")
        if isinstance(branch, str):
            branch = branch.replace("refs/heads/", "")
        record = WorktreeRecord(
            path=str(path),
            head=str(item.get("HEAD")) if item.get("HEAD") else None,
            branch=branch,
            detached=bool(item.get("detached")),
            exists=path.exists(),
        )
        if not path.exists():
            out.append(record)
            continue
        status = _run(["git", "status", "--short", "--branch"], cwd=path).stdout
        lines = status.splitlines()
        record.status_header = lines[0] if lines else ""
        record.dirty = any(line.strip() for line in lines[1:])
        record.unmerged = bool(
            _run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=path).stdout.strip()
        )
        ab = _run(["git", "rev-list", "--left-right", "--count", f"HEAD...{base_ref}"], cwd=path)
        record.ahead_behind = ab.stdout.strip() if ab.returncode == 0 else None
        mb = _run(["git", "merge-base", "--is-ancestor", "HEAD", base_ref], cwd=path)
        record.merged_to_base = mb.returncode == 0
        try:
            record.age_seconds = max(0.0, now - path.stat().st_mtime)
        except OSError:
            record.age_seconds = None
        out.append(record)
    return out


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip("/"))[:160] or "worktree"


def archive_worktree(path: str | os.PathLike[str], archive_root: str | os.PathLike[str]) -> Path:
    """Archive status, diffs, and untracked files for one worktree."""
    worktree = Path(path).resolve()
    archive_root_path = Path(archive_root).resolve()
    archive_root_path.mkdir(parents=True, exist_ok=True)
    dest = archive_root_path / _safe_slug(str(worktree))
    n = 1
    while dest.exists():
        n += 1
        dest = archive_root_path / f"{_safe_slug(str(worktree))}-{n}"
    dest.mkdir(parents=True)

    def write_cmd(name: str, args: Sequence[str]) -> str:
        result = _run(args, cwd=worktree)
        text = result.stdout
        if result.stderr:
            text += "\n--- stderr ---\n" + result.stderr
        (dest / name).write_text(text)
        return text

    write_cmd("head.txt", ["git", "rev-parse", "HEAD"])
    write_cmd("branch.txt", ["git", "branch", "--show-current"])
    write_cmd("status.txt", ["git", "status", "--short", "--branch"])
    write_cmd("diff.patch", ["git", "diff", "--binary", "HEAD"])
    write_cmd("staged.patch", ["git", "diff", "--binary", "--cached", "HEAD"])
    untracked_raw = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=worktree).stdout
    untracked = [p for p in untracked_raw.split("\0") if p]
    (dest / "untracked-files.txt").write_text("\n".join(untracked) + ("\n" if untracked else ""))
    if untracked:
        with tarfile.open(dest / "untracked.tar.gz", "w:gz") as tf:
            for rel in untracked:
                candidate = worktree / rel
                try:
                    if candidate.is_file() and candidate.stat().st_size <= 20_000_000:
                        tf.add(candidate, arcname=rel)
                except OSError:
                    continue
    return dest


def plan_removals(
    records: Iterable[WorktreeRecord],
    *,
    canonical: str,
    include_dirty: bool = False,
    stale_seconds: int = 24 * 3600,
) -> tuple[list[WorktreeRecord], list[WorktreeRecord]]:
    """Split records into removable/kept sets.

    Clean worktrees are removable when merged to base or older than stale_seconds.
    Dirty worktrees require include_dirty=True and are always archived first.
    """
    removable: list[WorktreeRecord] = []
    kept: list[WorktreeRecord] = []
    canonical_path = str(Path(canonical).resolve())
    for record in records:
        if str(Path(record.path).resolve()) == canonical_path:
            kept.append(record)
            continue
        if record.unmerged:
            kept.append(record)
            continue
        stale = record.age_seconds is not None and record.age_seconds >= stale_seconds
        if record.dirty and not include_dirty:
            kept.append(record)
            continue
        if record.merged_to_base or stale or record.dirty:
            removable.append(record)
        else:
            kept.append(record)
    return removable, kept


def run_janitor(
    repo: str | os.PathLike[str] | None = None,
    *,
    base_ref: str = "origin/main",
    archive_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = True,
    include_dirty: bool = False,
    stale_seconds: int = 24 * 3600,
    prune: bool = True,
) -> JanitorResult:
    """Archive and optionally remove stale registered Git worktrees."""
    repo_root = resolve_repo(repo)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = Path(
        archive_dir
        or os.environ.get("PRISMATIC_WORKTREE_JANITOR_ARCHIVE")
        or (repo_root / ".prismatic" / "worktree-archives" / timestamp)
    ).resolve()
    records = list_worktrees(repo_root, base_ref=base_ref)
    removable, kept = plan_removals(
        records,
        canonical=str(repo_root),
        include_dirty=include_dirty,
        stale_seconds=stale_seconds,
    )
    removed: list[dict[str, Any]] = []
    for record in removable:
        archived_to = None
        if record.dirty or include_dirty:
            archived_to = str(archive_worktree(record.path, archive_root))
        entry = record.to_dict() | {"archive": archived_to}
        if not dry_run:
            rm = _run(["git", "worktree", "remove", "--force", record.path], cwd=repo_root)
            entry["remove_returncode"] = rm.returncode
            entry["remove_stderr"] = rm.stderr[-1000:]
        removed.append(entry)
    if not dry_run and prune:
        _run(["git", "worktree", "prune"], cwd=repo_root)
    return JanitorResult(
        repo=str(repo_root),
        base_ref=base_ref,
        archive_dir=str(archive_root),
        dry_run=dry_run,
        total=len(records),
        canonical=str(repo_root),
        removable=[r.to_dict() for r in removable],
        kept=[r.to_dict() for r in kept],
        removed=removed,
    )


def default_core_crons() -> list[dict[str, Any]]:
    """Return PE-native cron manifests that any scheduler can install."""
    return [
        {
            "id": "prismatic.worktree-janitor.hourly",
            "name": "Prismatic Worktree Janitor",
            "schedule": "17 * * * *",
            "command": "prismatic worktrees janitor --repo ${PRISMATIC_REPO_DIR:-.} --include-dirty --quiet",
            "description": "Archives dirty/stale Git worktrees and prunes registered worktree metadata.",
            "silent_when_clean": True,
        }
    ]


def crontab_lines(repo: str | os.PathLike[str] | None = None) -> list[str]:
    """Render portable POSIX crontab lines for PE core cron manifests."""
    repo_root = resolve_repo(repo)
    lines: list[str] = [
        "# Prismatic Engine core crons — generated by `prismatic crons emit`",
        f"PRISMATIC_REPO_DIR={repo_root}",
    ]
    for item in default_core_crons():
        lines.append(f"# {item['id']} — {item['description']}")
        lines.append(f"{item['schedule']} cd {repo_root} && {item['command']}")
    return lines


def cli(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="prismatic worktrees")
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status", help="List registered worktrees as JSON")
    status.add_argument("--repo", default=None)
    status.add_argument("--base-ref", default="origin/main")
    janitor = sub.add_parser("janitor", help="Archive/remove stale worktrees")
    janitor.add_argument("--repo", default=None)
    janitor.add_argument("--base-ref", default="origin/main")
    janitor.add_argument("--archive-dir", default=None)
    janitor.add_argument("--apply", action="store_true", help="Actually remove planned worktrees")
    janitor.add_argument("--include-dirty", action="store_true", help="Archive and remove dirty worktrees too")
    janitor.add_argument("--stale-hours", type=float, default=24.0)
    janitor.add_argument("--quiet", action="store_true", help="Emit nothing when no removal occurred")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "status":
        print(json.dumps([r.to_dict() for r in list_worktrees(args.repo, base_ref=args.base_ref)], indent=2))
        return 0
    if args.command == "janitor":
        result = run_janitor(
            args.repo,
            base_ref=args.base_ref,
            archive_dir=args.archive_dir,
            dry_run=not args.apply,
            include_dirty=args.include_dirty,
            stale_seconds=int(args.stale_hours * 3600),
        )
        payload = result.to_dict()
        if args.quiet and not payload["removed"] and not payload["removable"]:
            return 0
        print(json.dumps(payload, indent=2))
        return 0
    parser.print_help()
    return 0
