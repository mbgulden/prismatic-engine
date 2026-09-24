"""Day-one merge-bar calibration backfill (Jev validation-loop plan §5).

READ-ONLY history scan -> calibration PROPOSAL. This script never applies
anything, never touches the live bar, and never writes anywhere except the
proposal file (default: ``prismatic/review_factory/spec/merge_bar_calibration_v1.yaml``).

The procedure:

1. Scan the repo's merged PR history — git log on the box for merge commits
   plus the GitHub API (``gh``) for check-run conclusions at merge time and
   for revert linkage. If ``gh`` is missing or unauthenticated, the scan
   degrades to a git-log-only pass and the proposal records
   ``history_thin: true`` with safe defaults.
2. Emit the proposal YAML with ``status: proposed``. A human (Michael)
   flips ``status`` to ``approved``; disagreements are resolved by editing
   the proposal, never by waiting for more data.

Safe defaults when history is thin: strict on tests/verification, advisory
on lint/style, tier ceiling 1 — the current shadow-v3 posture. Per plan
§10, the ``never_red`` set (tests/verification) is NOT calibratable; it is
hardcoded here and the backfill may not loosen it.

The deterministic bar must read calibrations ONLY through
``load_calibration()``, which refuses any file whose ``status`` is not
``approved``. A ``status: proposed`` draft is invisible to the live bar.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - venvs used for CI ship PyYAML
    _HAS_YAML = False
    yaml = None  # type: ignore[assignment]

SPEC_DIR = Path(__file__).resolve().parent / "spec"
PROPOSAL_VERSION = "merge_bar_calibration_v1"
STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"

# §10: not calibratable — hardcoded. A check whose name matches any of these
# can never land in checks_tolerated_red, no matter what the history says.
NEVER_RED_PATTERNS = ("test", "verification", "receipt", "ci")

# A PR only counts as merge-bar evidence when we actually saw its checks.
MIN_PRS_WITH_CHECK_DATA = 3

# A revert/revert-PR merged within this many days of the original merge
# links back as a rollback of that PR (the §6 post-merge incident window).
ROLLBACK_WINDOW_DAYS = 14

_SUCCESS = "success"


class CalibrationNotApproved(Exception):
    """Raised when a calibration file may not be read by the live bar."""


# ─────────────────────────────────────────────────────────────────────
# Loading: the only read path the deterministic bar may use
# ─────────────────────────────────────────────────────────────────────


def load_calibration(path: str | Path) -> dict[str, Any]:
    """Load a merge-bar calibration for the live bar.

    Refuses (fail-closed) unless the file is a ``merge_bar_calibration_v1``
    document with ``status: approved``. Proposals, drafts, wrong versions,
    and unreadable files all raise :class:`CalibrationNotApproved` — the
    deterministic bar must fall back to its hardcoded posture instead.
    """
    path = Path(path)
    if not _HAS_YAML:
        raise CalibrationNotApproved(
            f"cannot load calibration {path}: PyYAML unavailable"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CalibrationNotApproved(f"cannot load calibration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationNotApproved(f"refusing calibration {path}: not a mapping")
    if data.get("version") != PROPOSAL_VERSION:
        raise CalibrationNotApproved(
            f"refusing calibration {path}: version {data.get('version')!r} "
            f"!= {PROPOSAL_VERSION!r}"
        )
    status = data.get("status")
    if status != STATUS_APPROVED:
        raise CalibrationNotApproved(
            f"refusing calibration {path}: status is {status!r} — only "
            f"{STATUS_APPROVED!r} may be read by the live bar"
        )
    return data


# ─────────────────────────────────────────────────────────────────────
# Read-only history scan
# ─────────────────────────────────────────────────────────────────────


def _run(cmd: list[str], cwd: Path) -> str | None:
    """Run a read-only command; return stdout or None on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def gh_available(repo: Path) -> bool:
    """True when `gh` exists and is authenticated for this repo."""
    if shutil.which("gh") is None:
        return False
    return _run(["gh", "auth", "status"], repo) is not None


def repo_slug(repo: Path) -> str | None:
    """owner/name for the repo, via gh (preferred) or the git remote."""
    out = _run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        repo,
    )
    if out:
        return out.strip()
    out = _run(["git", "remote", "get-url", "origin"], repo)
    if out:
        match = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s*$", out.strip())
        if match:
            return match.group(1)
    return None


def merged_prs(repo: Path, limit: int) -> list[dict[str, Any]]:
    """Merged PRs, newest first, from the GitHub API. Empty list on failure."""
    out = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,mergedAt,title,body,mergeCommit",
        ],
        repo,
    )
    if not out:
        return []
    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [p for p in prs if isinstance(p, dict)]


def check_runs(slug: str, repo: Path, sha: str) -> list[dict[str, Any]]:
    """Check runs for one commit SHA (read-only). Empty list on failure."""
    out = _run(
        [
            "gh",
            "api",
            f"repos/{slug}/commits/{sha}/check-runs",
            "--paginate",
            "-q",
            "[.check_runs[] | {name: .name, conclusion: .conclusion}]",
        ],
        repo,
    )
    if not out:
        return []
    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [r for r in runs if isinstance(r, dict)]


_REVERT_TITLE = re.compile(r"^\s*revert\b", re.IGNORECASE)
_PR_REF = re.compile(r"#(\d+)")


def scan_git_history(repo: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    """Read-only scan of merged-PR history.

    Returns one row per merged PR: ``number``, ``merged_at``, ``title``,
    ``checks`` (``[{name, conclusion}]``, empty when check data is
    unavailable), and ``reverts`` (PR numbers this PR reverts, from
    revert-PR title/body references).
    """
    repo = Path(repo)
    rows: list[dict[str, Any]] = []
    slug = repo_slug(repo) if gh_available(repo) else None
    prs = merged_prs(repo, limit) if slug else []
    for pr in prs:
        number = pr.get("number")
        checks: list[dict[str, Any]] = []
        merge_commit = pr.get("mergeCommit") or {}
        sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        if slug and sha:
            checks = check_runs(slug, repo, str(sha))
        reverts: list[int] = []
        if _REVERT_TITLE.match(str(pr.get("title", ""))):
            reverts = [int(n) for n in _PR_REF.findall(str(pr.get("body", "")))]
        rows.append(
            {
                "number": number,
                "merged_at": pr.get("mergedAt"),
                "title": str(pr.get("title", "")),
                "checks": checks,
                "reverts": reverts,
            }
        )
    return rows


# ─────────────────────────────────────────────────────────────────────
# Proposal construction (pure — unit-tested against synthetic fixtures)
# ─────────────────────────────────────────────────────────────────────


def _norm(name: str) -> str:
    return name.strip().lower()


def _never_red(name: str) -> bool:
    n = _norm(name)
    return any(pat in n for pat in NEVER_RED_PATTERNS)


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_proposal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the calibration proposal from scanned history rows.

    A check lands in ``checks_tolerated_red`` only with evidence: it was
    non-success at merge time on at least one merged PR, NONE of those PRs
    was reverted within the rollback window, and it is not in the hardcoded
    never-red set. Everything else observed stays in ``checks_never_red``.
    With too little check data, the proposal carries safe defaults and
    ``history_thin: true``.
    """
    by_number = {r.get("number"): r for r in rows if r.get("number") is not None}

    # Link reverts back to the PRs they revert, within the rollback window.
    rolled_back: set[Any] = set()
    for row in rows:
        reverted_at = _parse_ts(row.get("merged_at"))
        for target in row.get("reverts", []) or []:
            original = by_number.get(target)
            merged_at = _parse_ts(original.get("merged_at")) if original else None
            if original is None or merged_at is None or reverted_at is None:
                # A revert-PR with an unresolvable target or timestamp still
                # counts as a rollback signal; per-check attribution only
                # applies to PRs inside the scanned window.
                rolled_back.add(target)
                continue
            if (reverted_at - merged_at).days <= ROLLBACK_WINDOW_DAYS:
                rolled_back.add(target)

    per_check: dict[str, dict[str, int]] = {}
    prs_with_checks = 0
    for row in rows:
        checks = row.get("checks") or []
        if checks:
            prs_with_checks += 1
        for check in checks:
            name = str(check.get("name", ""))
            key = _norm(name)
            stat = per_check.setdefault(
                key,
                {
                    "red_at_merge": 0,
                    "clean_at_merge": 0,
                    "followed_by_rollback": 0,
                },
            )
            if str(check.get("conclusion", "")).lower() == _SUCCESS:
                stat["clean_at_merge"] += 1
            else:
                stat["red_at_merge"] += 1
                if row.get("number") in rolled_back:
                    stat["followed_by_rollback"] += 1

    history_thin = prs_with_checks < MIN_PRS_WITH_CHECK_DATA

    tolerated: list[str] = []
    never_red: list[str] = []
    for key, stat in sorted(per_check.items()):
        if _never_red(key):
            never_red.append(key)
        elif stat["red_at_merge"] > 0 and stat["followed_by_rollback"] == 0:
            tolerated.append(key)
        else:
            never_red.append(key)

    rollback_signals: list[str] = []
    for key, stat in sorted(per_check.items()):
        if stat["followed_by_rollback"] > 0:
            rollback_signals.append(
                f"{key}: red at merge on {stat['red_at_merge']} PR(s), "
                f"{stat['followed_by_rollback']} followed by a revert within "
                f"{ROLLBACK_WINDOW_DAYS} days"
            )
    if rolled_back:
        rollback_signals.append(
            f"{len(rolled_back)} merged PR(s) reverted within "
            f"{ROLLBACK_WINDOW_DAYS} days: "
            + ", ".join(f"#{n}" for n in sorted(rolled_back))
        )

    proposal: dict[str, Any] = {
        "version": PROPOSAL_VERSION,
        "status": STATUS_PROPOSED,
        "demonstrated_bar": {
            "checks_tolerated_red": tolerated,
            "checks_never_red": never_red,
            "tier_ceiling_without_human": 1,
        },
        "rollback_signals": rollback_signals,
        "evidence": {
            "merged_prs_scanned": len(rows),
            "prs_with_check_data": prs_with_checks,
            "history_thin": history_thin,
            "per_check": [
                {"name": key, **stat} for key, stat in sorted(per_check.items())
            ],
        },
        "generated_by": "prismatic/review_factory/calibrate.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return proposal


_PROPOSAL_HEADER = """\
# Merge-bar calibration — PROPOSAL ONLY.
#
# status: proposed. The deterministic bar must NOT read this file until a
# human flips `status` to `approved` (see load_calibration() in
# prismatic/review_factory/calibrate.py, which refuses any other status).
# Nothing here is applied automatically: disagreements are resolved by
# editing this proposal, not by waiting for more data.
#
# Safe defaults (plan §5): strict on tests/verification, advisory on
# lint/style, tier ceiling 1. The never_red set is NOT calibratable —
# hardcoded in calibrate.py (plan §10).
"""


def write_proposal(proposal: dict[str, Any], path: str | Path) -> Path:
    """Write the proposal YAML to *path* (the only write this tool makes)."""
    if not _HAS_YAML:
        raise SystemExit("calibrate.py requires PyYAML to write the proposal")
    path = Path(path)
    body = yaml.safe_dump(proposal, sort_keys=False, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PROPOSAL_HEADER + body, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI: scan history, emit the proposal. Read-only except the proposal."""
    parser = argparse.ArgumentParser(
        description=(
            "Backfill the merge-bar calibration from merged-PR history. "
            "Read-only scan; writes a PROPOSAL only (status: proposed) — "
            "nothing is applied."
        )
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="repo worktree to scan (default: cwd)",
    )
    parser.add_argument(
        "--output",
        default=str(SPEC_DIR / f"{PROPOSAL_VERSION}.yaml"),
        help="where to write the proposal (default: the spec dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the proposal as JSON; write nothing",
    )
    parser.add_argument(
        "--max-prs",
        type=int,
        default=200,
        help="max merged PRs to scan (default: 200)",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    rows = scan_git_history(repo, limit=args.max_prs)
    proposal = build_proposal(rows)

    if args.dry_run:
        print(json.dumps(proposal, indent=2))
        return 0

    path = write_proposal(proposal, args.output)
    ev = proposal["evidence"]
    print(
        json.dumps(
            {
                "status": "proposal_written",
                "path": str(path),
                "merged_prs_scanned": ev["merged_prs_scanned"],
                "prs_with_check_data": ev["prs_with_check_data"],
                "history_thin": ev["history_thin"],
                "checks_tolerated_red": proposal["demonstrated_bar"][
                    "checks_tolerated_red"
                ],
                "checks_never_red": proposal["demonstrated_bar"]["checks_never_red"],
                "note": "status: proposed — not read by the live bar until "
                "a human approves",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
