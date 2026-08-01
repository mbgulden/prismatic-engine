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
import subprocess
import tarfile
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    changed_paths: int = 0
    untracked_paths: int = 0
    safety_class: str = "unknown"
    safety_reasons: list[str] = dataclasses.field(default_factory=list)
    value_class: str = "unknown"
    value_score: int = 0
    value_signals: list[str] = dataclasses.field(default_factory=list)
    proof_gaps: list[str] = dataclasses.field(default_factory=list)
    promotion_recommendation: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(slots=True)
class JanitorResult:
    repo: str
    base_ref: str
    archive_dir: str
    dry_run: bool
    manifest_path: str
    dirty_confirm_token: str
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
    stale_seconds: int = 24 * 3600,
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
            out.append(enrich_value_proof(record, path))
            continue
        status = _run(["git", "status", "--short", "--branch"], cwd=path).stdout
        lines = status.splitlines()
        record.status_header = lines[0] if lines else ""
        status_entries = [line for line in lines[1:] if line.strip()]
        record.dirty = bool(status_entries)
        record.changed_paths = len(status_entries)
        record.untracked_paths = sum(1 for line in status_entries if line.startswith("??"))
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
        classified = classify_worktree(record, canonical=str(repo_root), stale_seconds=stale_seconds)
        out.append(enrich_value_proof(classified, path))
    return out


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip("/"))[:160] or "worktree"


ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|\bgro-\d+\b", re.IGNORECASE)
PROOF_FILES = (
    ".prismatic/worktree-proof.json",
    "prismatic-worktree-proof.json",
    ".worktree-proof.json",
)


def _ahead_count(ahead_behind: str | None) -> int:
    if not ahead_behind:
        return 0
    try:
        left, _right = ahead_behind.split()
        return int(left)
    except (ValueError, AttributeError):
        return 0


def _load_proof_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    for rel in PROOF_FILES:
        candidate = path / rel
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text()), rel
        except (OSError, json.JSONDecodeError) as exc:
            return {"_invalid": str(exc)}, rel
    return None, None


def enrich_value_proof(record: WorktreeRecord, path: Path | None = None) -> WorktreeRecord:
    """Attach portable usefulness/provenance signals to a worktree record.

    This does not make deletion more aggressive. Missing proof creates a gap and
    preserves work for review; it never converts ambiguous work into trash.
    """
    wt = path or Path(record.path)
    score = 0
    signals: list[str] = []
    gaps: list[str] = []
    recommendation = "none"

    if record.branch and ISSUE_RE.search(record.branch):
        score += 15
        signals.append(f"branch links to issue-like id: {record.branch}")
    elif record.branch and record.branch not in {"main", "master"}:
        gaps.append("branch has no issue-like id")

    ahead = _ahead_count(record.ahead_behind)
    if ahead > 0:
        score += 45
        signals.append(f"{ahead} commit(s) ahead of base")
        recommendation = "open-or-update-pr"
    if record.dirty:
        score += 35
        signals.append(f"dirty working tree: {record.changed_paths} changed path(s)")
        recommendation = "capture-proof-or-promote"
    if record.untracked_paths:
        score += 10
        signals.append(f"{record.untracked_paths} untracked path(s)")
    if record.unmerged:
        score += 25
        signals.append("conflicted/unmerged work requires human resolution")
        recommendation = "manual-conflict-review"

    proof, proof_rel = _load_proof_file(wt) if record.exists else (None, None)
    if proof_rel:
        if proof and proof.get("_invalid"):
            gaps.append(f"invalid proof file {proof_rel}: {proof['_invalid']}")
        else:
            score += 35
            signals.append(f"proof file present: {proof_rel}")
            verdict = str((proof or {}).get("verdict") or (proof or {}).get("status") or "").lower()
            if verdict in {"indispensable", "useful", "promote", "keep"}:
                score += 35
                signals.append(f"proof verdict: {verdict}")
                recommendation = "promote"
            elif verdict in {"broken", "abandon", "superseded", "trash"}:
                signals.append(f"proof verdict: {verdict}")
                recommendation = "manual-disposal-review"
            if (proof or {}).get("verification"):
                score += 15
                signals.append("verification evidence recorded")
            else:
                gaps.append("proof file lacks verification evidence")
    elif record.dirty or ahead > 0 or record.unmerged:
        gaps.append("missing portable worktree proof file")

    if record.safety_class == "safe-remove" and score == 0:
        value_class = "disposable"
        recommendation = "safe-remove"
    elif recommendation == "manual-disposal-review":
        value_class = "broken-review"
    elif score >= 70:
        value_class = "indispensable"
    elif score > 0 or gaps:
        value_class = "preserve-needs-proof"
    else:
        value_class = "unknown"

    if gaps and recommendation == "none":
        recommendation = "capture-proof"
    record.value_score = score
    record.value_signals = signals
    record.proof_gaps = gaps
    record.value_class = value_class
    record.promotion_recommendation = recommendation
    return record


def worktree_proof_template(issue: str | None = None, summary: str = "") -> dict[str, Any]:
    """Return the portable proof bundle agents should leave in worktrees."""
    return {
        "schema": "prismatic.worktree-proof.v1",
        "issue": issue,
        "summary": summary,
        "verdict": "useful",  # useful | indispensable | promote | broken | superseded
        "agent": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verification": [],  # e.g. [{"command": "pytest ...", "result": "passed"}]
        "artifacts": [],
        "handoff": "What should the next agent/human do with this work?",
    }


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



def classify_worktree(
    record: WorktreeRecord,
    *,
    canonical: str,
    stale_seconds: int,
) -> WorktreeRecord:
    """Classify a worktree using enforceable safety gates.

    ``safe-remove`` is deliberately narrow: the worktree must be clean,
    conflict-free, merged to the base ref, stale enough, and not the canonical
    checkout. Everything else is ``keep`` or ``manual-review``.
    """
    canonical_path = str(Path(canonical).resolve())
    reasons: list[str] = []
    if str(Path(record.path).resolve()) == canonical_path:
        record.safety_class = "keep"
        record.safety_reasons = ["canonical checkout"]
        return record
    if not record.exists:
        record.safety_class = "safe-prune-metadata"
        record.safety_reasons = ["registered worktree path missing"]
        return record
    if record.unmerged:
        record.safety_class = "manual-review"
        record.safety_reasons = ["unmerged/conflicted files"]
        return record
    if record.dirty:
        record.safety_class = "manual-review"
        record.safety_reasons = [
            f"dirty checkout ({record.changed_paths} changed paths, {record.untracked_paths} untracked)",
            "dirty work is never removed by cron or ordinary --apply",
        ]
        return record
    if not record.merged_to_base:
        record.safety_class = "keep"
        record.safety_reasons = ["HEAD is not an ancestor of base ref"]
        return record
    stale = record.age_seconds is not None and record.age_seconds >= stale_seconds
    if not stale:
        record.safety_class = "keep"
        record.safety_reasons = ["clean+merged but not stale yet"]
        return record
    record.safety_class = "safe-remove"
    reasons.append("clean checkout")
    reasons.append("HEAD merged to base ref")
    reasons.append(f"older than stale threshold ({stale_seconds}s)")
    record.safety_reasons = reasons
    return record


def plan_removals(
    records: Iterable[WorktreeRecord],
    *,
    canonical: str,
    include_dirty: bool = False,
    stale_seconds: int = 24 * 3600,
) -> tuple[list[WorktreeRecord], list[WorktreeRecord]]:
    """Split records into removable/kept sets using code-level safety classes."""
    removable: list[WorktreeRecord] = []
    kept: list[WorktreeRecord] = []
    for raw_record in records:
        record = classify_worktree(raw_record, canonical=canonical, stale_seconds=stale_seconds)
        if record.safety_class == "safe-remove":
            removable.append(record)
        elif include_dirty and record.dirty and not record.unmerged:
            # Include dirty non-conflicted worktrees in the manifest for human
            # review, but still require the explicit dirty confirmation token
            # before deletion in run_janitor. Conflicts are never planned.
            removable.append(record)
        else:
            kept.append(record)
    return removable, kept



def write_manifest(
    *,
    repo_root: Path,
    archive_root: Path,
    dry_run: bool,
    dirty_confirm_token: str,
    removable: list[WorktreeRecord],
    kept: list[WorktreeRecord],
) -> Path:
    """Write a machine-readable decision manifest before any removal."""
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = archive_root / "manifest.json"
    payload = {
        "repo": str(repo_root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "dirty_confirm_token": dirty_confirm_token,
        "policy": {
            "safe_remove_requires": [
                "not canonical checkout",
                "exists",
                "no unmerged files",
                "clean git status",
                "HEAD is ancestor of base ref",
                "older than stale threshold",
            ],
            "dirty_requires_explicit_token": True,
            "usefulness_policy": "missing proof preserves work; it never makes work disposable",
            "proof_files": list(PROOF_FILES),
        },
        "removable": [r.to_dict() for r in removable],
        "kept": [r.to_dict() for r in kept],
    }
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return manifest


def run_janitor(
    repo: str | os.PathLike[str] | None = None,
    *,
    base_ref: str = "origin/main",
    archive_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = True,
    include_dirty: bool = False,
    confirm_dirty_token: str | None = None,
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
    dirty_confirm_token = f"DELETE-DIRTY-WORKTREES:{base_ref}"
    records = list_worktrees(repo_root, base_ref=base_ref)
    removable, kept = plan_removals(
        records,
        canonical=str(repo_root),
        include_dirty=include_dirty,
        stale_seconds=stale_seconds,
    )
    manifest_path = write_manifest(
        repo_root=repo_root,
        archive_root=archive_root,
        dry_run=dry_run,
        dirty_confirm_token=dirty_confirm_token,
        removable=removable,
        kept=kept,
    )
    removed: list[dict[str, Any]] = []
    for record in removable:
        archived_to = None
        if record.dirty:
            archived_to = str(archive_worktree(record.path, archive_root))
            if not dry_run and confirm_dirty_token != dirty_confirm_token:
                entry = record.to_dict() | {
                    "archive": archived_to,
                    "remove_returncode": None,
                    "remove_stderr": "dirty worktree archived but not removed; pass exact confirm_dirty_token",
                }
                removed.append(entry)
                continue
        entry = record.to_dict() | {"archive": archived_to}
        if not dry_run:
            cmd = ["git", "worktree", "remove", record.path]
            if record.dirty:
                cmd.insert(3, "--force")
            rm = _run(cmd, cwd=repo_root)
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
        manifest_path=str(manifest_path),
        dirty_confirm_token=dirty_confirm_token,
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
            "command": "prismatic worktrees janitor --repo ${PRISMATIC_REPO_DIR:-.} --quiet",
            "description": "Removes only clean+merged stale worktrees; dirty work is reported/manifested, never deleted by cron.",
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
    status.add_argument("--stale-hours", type=float, default=24.0)
    proof = sub.add_parser("proof-template", help="Emit a portable worktree proof JSON template")
    proof.add_argument("--issue", default=None)
    proof.add_argument("--summary", default="")
    janitor = sub.add_parser("janitor", help="Archive/remove stale worktrees")
    janitor.add_argument("--repo", default=None)
    janitor.add_argument("--base-ref", default="origin/main")
    janitor.add_argument("--archive-dir", default=None)
    janitor.add_argument("--apply", action="store_true", help="Actually remove planned worktrees")
    janitor.add_argument("--include-dirty", action="store_true", help="Include dirty worktrees in the manifest; deletion still requires --confirm-dirty-token")
    janitor.add_argument("--confirm-dirty-token", default=None, help="Exact token printed in manifest; required to delete dirty worktrees")
    janitor.add_argument("--stale-hours", type=float, default=24.0)
    janitor.add_argument("--quiet", action="store_true", help="Emit nothing when no removal occurred")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "status":
        print(json.dumps([r.to_dict() for r in list_worktrees(args.repo, base_ref=args.base_ref, stale_seconds=int(args.stale_hours * 3600))], indent=2))
        return 0
    if args.command == "proof-template":
        print(json.dumps(worktree_proof_template(issue=args.issue, summary=args.summary), indent=2))
        return 0
    if args.command == "janitor":
        result = run_janitor(
            args.repo,
            base_ref=args.base_ref,
            archive_dir=args.archive_dir,
            dry_run=not args.apply,
            include_dirty=args.include_dirty,
            confirm_dirty_token=args.confirm_dirty_token,
            stale_seconds=int(args.stale_hours * 3600),
        )
        payload = result.to_dict()
        if args.quiet and not payload["removed"] and not payload["removable"]:
            return 0
        print(json.dumps(payload, indent=2))
        return 0
    parser.print_help()
    return 0
