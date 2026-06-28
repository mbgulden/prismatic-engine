"""Loop-branch detector for the Prismatic Engine publish audit.

Classifies a git branch as a "loop-noise" branch when its commits are
infrastructure/triage notes rather than feature work intended for the default
branch. Lets `post_publish_audit_v2.py` exclude these branches from its
"unintegrated work" denominator.

A branch is classified as loop-noise if ANY of these are true:
  1. ALL commits on the branch match the loop-noise regex
     `^\[(?P<agent>\w+)\] (?P<issue>GRO-\d+): (triage note|infra findings|witness|status)`
  2. The diff vs the merge-base contains ZERO source files (only .md / .txt)
  3. Three or more commits have identical first 50 chars of message
     (signature of a sweeper loop)

Usage:
    from prismatic.quality.loop_branch_detector import (
        is_loop_branch,
        LoopBranchVerdict,
    )
    verdict = is_loop_branch(repo_path, branch_name)
    if verdict.is_loop_noise:
        # exclude from publish-audit denominator
        ...
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopBranchVerdict:
    branch: str
    is_loop_noise: bool
    reasons: tuple[str, ...]
    commit_count: int
    source_files_changed: int

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        flag = "LOOP" if self.is_loop_noise else "WORK"
        return f"[{flag}] {self.branch}: {self.commit_count} commits, {self.source_files_changed} source files changed; reasons={list(self.reasons)}"


# Loop-noise commit message pattern. Captures agent + Linear issue + a
# small set of well-known loop-noise verb phrases.
_LOOP_NOISE_RE = re.compile(
    r"^\[(?P<agent>[A-Za-z][\w-]+)\] (?P<issue>GRO-\d+):\s+"
    r"(?P<verb>triage note|infra findings|witness|status|routine check|re-verification)",
    re.IGNORECASE,
)

# File extensions that count as "source" for the diff-vs-source check.
# Markdown, text, and lockfiles are NOT considered source — pure-doc changes
# are loop artifacts, not feature work.
_SOURCE_EXTENSIONS = frozenset(
    {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
     ".c", ".cc", ".cpp", ".h", ".hpp", ".sh", ".bash", ".zsh",
     ".yaml", ".yml", ".toml", ".json", ".sql", ".html", ".css", ".scss"}
)


def is_loop_branch(repo_path: str, branch: str, default_branch: str = "origin/main") -> LoopBranchVerdict:
    """Classify ``branch`` against ``default_branch`` in the repo at ``repo_path``."""

    commits = _list_commits(repo_path, branch, default_branch)
    if not commits:
        # No commits ahead — not loop-noise, just no work.
        return LoopBranchVerdict(
            branch=branch, is_loop_noise=False,
            reasons=("no-commits-ahead",),
            commit_count=0, source_files_changed=0,
        )

    reasons: list[str] = []

    # Check 1: every commit matches the loop-noise regex.
    if all(_LOOP_NOISE_RE.match(c) for c in commits):
        reasons.append("all-messages-match-loop-regex")

    # Check 2: diff vs default branch contains zero source files.
    source_files = _list_source_files_changed(repo_path, branch, default_branch)
    if commits and source_files == 0:
        reasons.append("zero-source-files-changed")

    # Check 3: three or more commits with identical first 50 chars of message.
    prefixes: dict[str, int] = {}
    for c in commits:
        prefix = c[:50].strip().lower()
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    dup_prefixes = {p: n for p, n in prefixes.items() if n >= 3}
    if dup_prefixes:
        reasons.append(f"duplicate-message-prefixes({len(dup_prefixes)})")

    is_noise = bool(reasons)
    return LoopBranchVerdict(
        branch=branch,
        is_loop_noise=is_noise,
        reasons=tuple(reasons),
        commit_count=len(commits),
        source_files_changed=len(source_files),
    )


def classify_all_branches(
    repo_path: str,
    default_branch: str = "origin/main",
    branches: list[str] | None = None,
) -> list[LoopBranchVerdict]:
    """Classify every local branch (or ``branches`` if given) against ``default_branch``."""

    if branches is None:
        branches = _list_local_branches(repo_path)

    verdicts = [is_loop_branch(repo_path, b, default_branch) for b in branches]
    # Stable order: loop-noise first, then by branch name.
    verdicts.sort(key=lambda v: (not v.is_loop_noise, v.branch))
    return verdicts


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run_git(repo_path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _list_local_branches(repo_path: str) -> list[str]:
    out = _run_git(repo_path, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _list_commits(repo_path: str, branch: str, default_branch: str) -> list[str]:
    """Return commit subject lines on ``branch`` but not on ``default_branch``."""
    try:
        out = _run_git(repo_path, "log", "--no-merges", "--format=%s",
                       f"{default_branch}..{branch}")
    except subprocess.CalledProcessError:
        # Branch may not have a clean merge-base with default (e.g. shallow clone).
        # Fall back to listing the branch's own commits.
        out = _run_git(repo_path, "log", "--no-merges", "--format=%s", branch, "-50")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _list_source_files_changed(repo_path: str, branch: str, default_branch: str) -> set[str]:
    """Return paths of source-language files changed between ``branch`` and ``default_branch``."""
    try:
        out = _run_git(repo_path, "diff", "--name-only", "--diff-filter=ACMRT",
                       f"{default_branch}..{branch}")
    except subprocess.CalledProcessError:
        return set()
    paths = {line.strip() for line in out.splitlines() if line.strip()}
    return {p for p in paths if PurePosixPath(p).suffix in _SOURCE_EXTENSIONS}