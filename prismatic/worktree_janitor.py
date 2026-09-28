"""Core worktree hygiene and janitor utilities for Prismatic Engine.

The janitor is intentionally harness-agnostic: it operates on any Git checkout,
uses only Git + Python stdlib, and archives dirty state before removal. It is
safe by default (`dry_run=True`) so API and cron integrations can report first
and mutate only when explicitly asked.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import re
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
        },
        # WI-5: branch GC, seeded report-only (never deletes unless a human
        # re-runs with --mode apply). Flags pinned explicitly so a future
        # default change cannot silently turn a seeded cron destructive.
        {
            "id": "prismatic.worktree-janitor-gc.weekly",
            "name": "Prismatic Worktree Janitor GC (branch GC)",
            "schedule": "31 3 * * 0",
            "command": "prismatic worktrees janitor-gc --repo ${PRISMATIC_REPO_DIR:-.} --mode report-only --grace-days 7",
            "description": "Report-only branch GC: classifies merged, aged local branches and logs decisions; never deletes in this mode.",
            "silent_when_clean": True,
        },
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
    gc = sub.add_parser("janitor-gc", help="Branch GC: delete merged, aged local branches (Phase 5)")
    gc.add_argument("--repo", default=None)
    gc.add_argument(
        "--mode",
        default=Mode.ReportOnly.value,
        choices=[m.value for m in Mode],
        help="report-only (default): decide and log, never delete; apply: delete eligible branches",
    )
    gc.add_argument("--grace-days", type=int, default=7, help="Merged branches younger than this are kept")
    gc.add_argument(
        "--ledger",
        action="store_true",
        help="Enable the trust-ledger gate (lazy import; degrades to git-only checks when unavailable)",
    )
    gc.add_argument(
        "--protect",
        action="append",
        default=[],
        help="Extra protected branch name (repeatable)",
    )
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
    if args.command == "janitor-gc":
        # Earned-autonomy Phase 5: branch GC. Runs janitor_main and exits 0.
        return janitor_main(
            [
                "--mode", args.mode,
                "--grace-days", str(args.grace_days),
            ]
            + (["--ledger"] if args.ledger else [])
            + [p for protect in (args.protect or []) for p in ("--protect", protect)]
            + ((["--repo", args.repo] if args.repo else [])),
        )
    parser.print_help()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Earned-autonomy Phase 5: branch GC
# ─────────────────────────────────────────────────────────────────────────────
# Deletes *local branches* (not worktrees) that are fully merged into
# origin/main and older than a grace period. Two modes:
#   - report-only (default): classify every branch, log decisions, delete nothing
#   - apply: really delete eligible branches with `git branch -d` (never -D)
#
# Safety properties (fail-closed throughout):
#   - main, the current HEAD branch, and release/* branches are never eligible
#   - branches with an open PR are never eligible
#   - if open-PR status cannot be determined (no gh / no auth / API failure),
#     NO branch is eligible ("open-PR status unknown (fail-closed)")
#   - a branch whose tip is not an ancestor of origin/main is never eligible
#   - a branch checked out in a dirty worktree is never eligible
#   - when a trust ledger is supplied, a branch with no clean-merge ledger
#     record is never eligible
#   - deletion uses `git branch -d` only: the merge check already passed, and
#     -d is the second guard (git refuses -d for unmerged branches)
#
# The trust ledger is an optional, lazy dependency: any trust-ledger reference
# is imported inside functions, and every import failure degrades to the
# git-only checks (documented below). This keeps Phase 5 independent of the
# open earned-autonomy PRs #550-#553.
#
# NO cron/scheduler wiring is installed here. The janitor cron block is
# Gate B / T1 activation and requires Michael's explicit approval.


class Mode(enum.Enum):
    """Janitor GC operating mode."""

    ReportOnly = "report-only"
    Apply = "apply"


@dataclasses.dataclass(frozen=True, slots=True)
class BranchCandidate:
    """One local branch classified for branch GC."""

    branch: str
    eligible: bool
    reason: str
    tip_sha: str = ""
    tip_age_days: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class JanitorManifestStore:
    """Append-only JSONL decision log for branch GC.

    Path: ``<repo>/.prismatic/janitor/<repo-name>-<mode>.jsonl``.

    Entry schema (required keys, stable): ``ts``, ``repo``, ``branch``,
    ``action``, ``mode``. ``action`` is ``"gc-deleted"`` or ``"gc-skipped"``.
    Additive optional keys: ``reason``, ``tip_sha``, ``tip_age_days``,
    ``delete_returncode``, ``delete_stderr``. Readers must tolerate unknown
    keys; writers must keep the required keys.
    """

    def __init__(self, repo_root: str | os.PathLike[str]) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.dir = self.repo_root / ".prismatic" / "janitor"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, mode: Mode) -> Path:
        slug = _safe_slug(self.repo_root.name)
        return self.dir / f"{slug}-{mode.value}.jsonl"

    def record(
        self,
        *,
        repo: str,
        branch: str,
        action: str,
        mode: str,
        reason: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "repo": repo,
            "branch": branch,
            "action": action,
            "mode": mode,
            "reason": reason,
        }
        entry.update(extra)
        target = self.path_for(Mode(mode))
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry


def _load_ignored_branches(repo_root: str | os.PathLike[str]) -> set[str]:
    """Branch names the janitor must never touch.

    Sources (unioned, best-effort):
      - ``<repo>/.prismatic/janitor/ignored-branches.txt`` — one branch per
        line; blank lines and ``#`` comments are ignored.
      - ``PRISMATIC_JANITOR_IGNORED`` env var — comma-separated branch names.
    A missing/unreadable file yields an empty set (documented, not an error).
    """
    ignored: set[str] = set()
    root = Path(repo_root)
    ignore_file = root / ".prismatic" / "janitor" / "ignored-branches.txt"
    try:
        text = ignore_file.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ignored.add(line)
    for name in os.environ.get("PRISMATIC_JANITOR_IGNORED", "").split(","):
        name = name.strip()
        if name:
            ignored.add(name)
    return ignored


def is_release_branch(branch: str) -> bool:
    """True for the release line: ``release`` or ``release/*``."""
    return branch == "release" or branch.startswith("release/")


def _open_pr_branch_names(repo_root: str | os.PathLike[str]) -> set[str] | None:
    """Head branch names with an open PR, or None when unknown (fail-closed).

    Best-effort ``gh pr list``. Returns None on ANY failure — missing ``gh``,
    no auth, non-zero exit, unparseable output — so callers treat every
    branch as ineligible rather than risk deleting a branch under review.
    """
    try:
        result = _run(
            [
                "gh",
                "pr",
                "list",
                "--json",
                "headRefName",
                "--state",
                "open",
                "--limit",
                "100",
            ],
            cwd=repo_root,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        items = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    try:
        return {str(item["headRefName"]) for item in items if item.get("headRefName")}
    except (TypeError, AttributeError):
        return None


def _worktree_branch_map(repo_root: str | os.PathLike[str]) -> dict[str, str] | None:
    """Map branch name -> worktree path, or None if the listing is unusable.

    A None return is fail-closed: callers skip branches rather than guess.
    """
    result = _run(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        return None
    try:
        mapping: dict[str, str] = {}
        current_path: str | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = line[len("worktree ") :].strip()
            elif line.startswith("branch ") and current_path is not None:
                ref = line[len("branch ") :].strip()
                name = (
                    ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
                )
                mapping[name] = current_path
            elif not line.strip():
                current_path = None
        return mapping
    except Exception:
        return None


def _open_default_trust_ledger() -> Any | None:
    """Best-effort handle to the review-factory trust ledger.

    The ``prismatic.review_factory`` import happens lazily INSIDE this
    function. Any failure (module absent because phases 1-3 are unmerged,
    missing opener, constructor error) returns None: the GC then runs the
    git-only checks. Documented degradation, never an exception.
    """
    try:
        from prismatic.review_factory import trust as trust_mod
    except Exception:
        return None
    opener = (
        getattr(trust_mod, "open_ledger", None)
        or getattr(trust_mod, "get_ledger", None)
        or getattr(trust_mod, "TrustLedger", None)
    )
    if opener is None:
        return None
    try:
        return opener()
    except Exception:
        return None


def _ledger_has_clean_merge(trust_ledger: Any, branch: str) -> bool:
    """True when the ledger records a clean merge of ``branch``.

    Primary rule: an event with ``event_type == "merge"`` whose
    ``event_data.artifact_id`` equals the branch name. Lenient fallback:
    any event whose ``event_data`` mentions the branch name (covers
    phase-3 ``record_merge_outcome`` shapes). Works with dict- or
    object-shaped events; any ``events()`` failure counts as no record.
    """
    try:
        events = trust_ledger.events()
    except Exception:
        return False
    for event in events or []:
        if isinstance(event, dict):
            event_type = event.get("event_type")
            data = event.get("event_data")
        else:
            event_type = getattr(event, "event_type", None)
            data = getattr(event, "event_data", None)
        if event_type == "merge":
            artifact = (
                data.get("artifact_id")
                if isinstance(data, dict)
                else getattr(data, "artifact_id", None)
            )
            if artifact == branch:
                return True
        try:
            if branch and branch in json.dumps(data, default=str):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _tip_info(repo_root: Path, branch: str) -> tuple[str, float] | None:
    """(tip_sha, tip_age_days) for a branch, or None when undeterminable."""
    sha = _run(["git", "rev-parse", branch], cwd=repo_root)
    ts = _run(["git", "log", "-1", "--format=%ct", branch], cwd=repo_root)
    if sha.returncode != 0 or ts.returncode != 0:
        return None
    try:
        age_days = (
            datetime.now(timezone.utc).timestamp() - int(ts.stdout.strip())
        ) / 86400.0
    except (ValueError, OverflowError):
        return None
    return sha.stdout.strip(), max(0.0, age_days)


def collect_gc_candidates(
    repo_root: str | os.PathLike[str],
    *,
    grace_days: int = 7,
    trust_ledger: Any | None = None,
    protected_branches: frozenset[str] = frozenset(),
) -> list[BranchCandidate]:
    """Classify every local branch for branch GC. Pure collection: no deletions.

    Skip reasons are evaluated in order; the first match wins and the branch
    is ineligible. ``trust_ledger`` may be a ledger-like object with
    ``events()``, the string ``"auto"`` (open the default ledger lazily),
    or None (skip the ledger gate: git-only checks).
    """
    root = resolve_repo(repo_root)
    branches = [
        line
        for line in _run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
            cwd=root,
            check=True,
        ).stdout.splitlines()
        if line.strip()
    ]
    current = _run(["git", "branch", "--show-current"], cwd=root).stdout.strip()
    ignored = set(_load_ignored_branches(root)) | set(protected_branches)
    open_prs = _open_pr_branch_names(root)
    # Best-effort refresh of origin/main. Failure degrades to the local
    # origin/main ref; if that is missing/unrelated, merge-base fails and the
    # branch is skipped as unmerged — never treated as eligible.
    _run(["git", "fetch", "origin", "main", "--quiet"], cwd=root)
    worktree_map = _worktree_branch_map(root)

    ledger: Any | None = None
    if trust_ledger is not None:
        ledger = (
            _open_default_trust_ledger() if trust_ledger == "auto" else trust_ledger
        )

    candidates: list[BranchCandidate] = []
    for branch in branches:
        tip = _tip_info(root, branch)
        tip_sha = tip[0] if tip else ""
        tip_age = tip[1] if tip else 0.0
        reason: str | None = None
        if branch == "main":
            reason = "protected: main"
        elif branch == current:
            reason = "protected: current branch"
        elif is_release_branch(branch):
            reason = "protected: release branch"
        elif branch in ignored:
            reason = "protected: ignored"
        elif open_prs is None:
            reason = "open-PR status unknown (fail-closed)"
        elif branch in open_prs:
            reason = "protected: open PR"
        elif (
            _run(
                ["git", "merge-base", "--is-ancestor", branch, "origin/main"],
                cwd=root,
            ).returncode
            != 0
        ):
            reason = "unmerged into origin/main"
        elif tip is None:
            reason = "tip age unknown (fail-closed)"
        elif tip_age < grace_days:
            reason = f"merged but inside {grace_days}-day grace"
        elif worktree_map is None:
            reason = "worktree status unknown"
        elif branch in worktree_map:
            wt_status = _run(
                ["git", "-C", worktree_map[branch], "status", "--porcelain"],
                cwd=root,
            )
            if wt_status.returncode != 0:
                reason = "worktree status unknown"
            elif wt_status.stdout.strip():
                reason = "dirty worktree"
        if (
            reason is None
            and ledger is not None
            and not _ledger_has_clean_merge(ledger, branch)
        ):
            reason = "no ledger record of clean merge"
        candidates.append(
            BranchCandidate(
                branch=branch,
                eligible=reason is None,
                reason=reason or "eligible: merged, aged, clean",
                tip_sha=tip_sha,
                tip_age_days=tip_age,
            )
        )
    return candidates


def _delete_gc_branch(repo_root: Path, branch: str) -> tuple[bool, str]:
    """Really delete one branch with `git branch -d`. Never -D.

    -d is the second guard: git refuses to delete a branch whose tip is not
    merged into its upstream/HEAD. Returns (ok, detail).
    """
    result = _run(["git", "branch", "-d", branch], cwd=repo_root)
    detail = (result.stderr.strip() or result.stdout.strip())[:500]
    return result.returncode == 0, detail


def execute_janitor(
    mode: Mode | str,
    repo_root: str | os.PathLike[str] | None = None,
    *,
    grace_days: int = 7,
    trust_ledger: Any | None = None,
    protected_branches: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run branch GC in ``mode`` and log every decision to the manifest.

    Report-only: classify + log, never delete. Apply: delete branches with
    ``eligible=True`` via ``git branch -d``; every other branch is logged as
    ``gc-skipped`` with its reason. Returns a JSON-serializable summary.
    """
    mode = Mode(mode)
    root = resolve_repo(repo_root)
    candidates = collect_gc_candidates(
        root,
        grace_days=grace_days,
        trust_ledger=trust_ledger,
        protected_branches=protected_branches,
    )
    store = JanitorManifestStore(root)
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    for candidate in candidates:
        if mode is Mode.Apply and candidate.eligible:
            ok, detail = _delete_gc_branch(root, candidate.branch)
            if ok:
                action = "gc-deleted"
                reason = f"deleted via git branch -d ({detail})"
                deleted.append(candidate.branch)
            else:
                action = "gc-skipped"
                reason = f"delete failed: {detail}"
                skipped.append({"branch": candidate.branch, "reason": reason})
        else:
            action = "gc-skipped"
            reason = (
                candidate.reason
                if not candidate.eligible
                else "report-only mode: no deletions"
            )
            skipped.append({"branch": candidate.branch, "reason": reason})
        store.record(
            repo=str(root),
            branch=candidate.branch,
            action=action,
            mode=mode.value,
            reason=reason,
            tip_sha=candidate.tip_sha,
            tip_age_days=round(candidate.tip_age_days, 2),
        )
    return {
        "mode": mode.value,
        "repo": str(root),
        "grace_days": grace_days,
        "manifest_path": str(store.path_for(mode)),
        "deleted": deleted,
        "skipped": skipped,
        "candidates": [c.to_dict() for c in candidates],
    }


def janitor_main(
    args: Sequence[str] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> int:
    """CLI-style entry point for branch GC. Returns a process exit code."""
    import argparse

    parser = argparse.ArgumentParser(prog="prismatic janitor gc")
    parser.add_argument(
        "--mode",
        default=Mode.ReportOnly.value,
        choices=[m.value for m in Mode],
        help="report-only (default): decide and log, never delete; apply: delete eligible branches",
    )
    parser.add_argument("--repo", default=None)
    parser.add_argument(
        "--grace-days",
        type=int,
        default=7,
        help="Merged branches younger than this many days are kept",
    )
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="Enable the trust-ledger gate (lazy import; degrades to git-only when unavailable)",
    )
    parser.add_argument(
        "--protect",
        action="append",
        default=[],
        help="Extra protected branch name (repeatable)",
    )
    ns = parser.parse_args(list(args) if args is not None else None)
    root = resolve_repo(repo_root or ns.repo)
    ledger = _open_default_trust_ledger() if ns.ledger else None
    result = execute_janitor(
        Mode(ns.mode),
        root,
        grace_days=ns.grace_days,
        trust_ledger=ledger,
        protected_branches=frozenset(ns.protect),
    )
    print(json.dumps(result, indent=2))
    return 0
