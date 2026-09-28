#!/usr/bin/env python3
"""receipt_coverage_watch.py — merge-receipt coverage watcher (WI-7).

Finish-line item 3 says every merge carries verifiable proof plus a signed
receipt. The known gap: merges performed in the GitHub UI never pass through
``merge_executor``, so they emit zero receipts — and nothing noticed.

This script is the watcher that notices. It is strictly read-only:

1. lists recently merged PRs via ``gh pr list --state merged``,
2. reads the merge-receipts JSONL log written by
   ``prismatic.verification.merge_receipt`` (``$PRISMATIC_MERGE_RECEIPTS``,
   ``$PRISMATIC_STATE_DIR/merge-receipts.jsonl``, or
   ``~/.prismatic/merge-receipts.jsonl``),
3. reports every merged PR with no matching receipt.

A PR is *covered* when a receipt's ``merge_sha`` equals the PR's merge-commit
OID; as a fallback, a receipt whose ``candidate_sha`` equals the PR head OID
also covers it (recorded as ``matched_via: candidate_sha``).

Exit codes: 0 = 100% coverage; 1 = coverage < 100% (gap found);
2 = tool/data error (gh unavailable or failed, bad arguments) — deliberately
distinct from a coverage gap so a broken watcher never reads as "all clear".

Stdlib only — no prismatic imports, so it runs anywhere ``gh`` does.

Usage:
    python3 scripts/receipt_coverage_watch.py [--repo OWNER/REPO]
        [--limit N] [--receipts PATH] [--json] [--gh PATH]
        [--emit-missing] [--git-dir PATH]

With --emit-missing (WR-3), merged PRs lacking a receipt get one built,
Ed25519-signed, and persisted via prismatic.verification.merge_receipt
(the same builder the WR-2 webhook emitter uses). Watch mode without
the flag stays stdlib-only and read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MERGE_RECEIPT_MARKER = "PRISMATIC_MERGE_RECEIPT_OK"
RECEIPTS_FILENAME = "merge-receipts.jsonl"
DEFAULT_REPO = "mbgulden/prismatic-engine"
DEFAULT_LIMIT = 20

EXIT_OK = 0
EXIT_COVERAGE_GAP = 1
EXIT_TOOL_ERROR = 2


class WatcherError(Exception):
    """gh unavailable/failed, unreadable input — a tool error, not a gap."""


# ── Receipt log ───────────────────────────────────────────────────────


def resolve_receipt_log_path(explicit: str | None = None) -> Path:
    """Resolve the merge-receipts JSONL path.

    Precedence: explicit ``--receipts`` > ``$PRISMATIC_MERGE_RECEIPTS`` >
    ``$PRISMATIC_STATE_DIR/merge-receipts.jsonl`` >
    ``~/.prismatic/merge-receipts.jsonl``.
    (Mirrors ``prismatic.verification.merge_receipt.default_merge_receipts_path``.)
    """
    if explicit:
        return Path(explicit)
    override = os.environ.get("PRISMATIC_MERGE_RECEIPTS")
    if override:
        return Path(override)
    state_dir = os.environ.get("PRISMATIC_STATE_DIR")
    if state_dir:
        return Path(state_dir) / RECEIPTS_FILENAME
    return Path.home() / ".prismatic" / RECEIPTS_FILENAME


def load_receipt_index(log_path: Path | str):
    """Index merge receipts by merge_sha and candidate_sha.

    Returns ``(by_merge_sha, by_candidate_sha, stats)``. Only rows carrying
    the merge-receipt marker count; malformed lines and foreign rows are
    counted in ``stats`` and skipped. A missing log is not an error — it
    simply means zero receipts on record (flagged as such in the report).
    """
    path = Path(log_path)
    stats = {
        "log_path": str(path),
        "log_exists": path.exists(),
        "lines": 0,
        "receipts": 0,
        "malformed_lines": 0,
        "skipped_non_receipt_lines": 0,
    }
    by_merge_sha: dict[str, dict] = {}
    by_candidate_sha: dict[str, dict] = {}
    if not path.exists():
        return by_merge_sha, by_candidate_sha, stats
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WatcherError(f"cannot read receipt log {path}: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        stats["lines"] += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            stats["malformed_lines"] += 1
            continue
        if not isinstance(row, dict) or row.get("marker") != MERGE_RECEIPT_MARKER:
            stats["skipped_non_receipt_lines"] += 1
            continue
        stats["receipts"] += 1
        merge_sha = row.get("merge_sha") or ""
        candidate_sha = row.get("candidate_sha") or ""
        if merge_sha and merge_sha not in by_merge_sha:
            by_merge_sha[merge_sha] = row
        if candidate_sha and candidate_sha not in by_candidate_sha:
            by_candidate_sha[candidate_sha] = row
    return by_merge_sha, by_candidate_sha, stats


# ── Merged PRs ────────────────────────────────────────────────────────


_GH_JSON_FIELDS = (
    "number,title,mergedAt,mergeCommit,headRefOid,headRefName,url,mergedBy"
)


def fetch_merged_prs(
    *, gh_bin: str = "gh", repo: str = DEFAULT_REPO, limit: int = DEFAULT_LIMIT
) -> list[dict]:
    """List recently merged PRs via ``gh``. Raises WatcherError on failure."""
    if limit < 1:
        raise WatcherError(f"--limit must be >= 1, got {limit}")
    cmd = [
        gh_bin, "pr", "list",
        "--state", "merged",
        "--limit", str(limit),
        "--repo", repo,
        "--json", _GH_JSON_FIELDS,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=90
        )
    except FileNotFoundError as exc:
        raise WatcherError(f"gh binary not found: {gh_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise WatcherError(f"gh pr list timed out after 90s") from exc
    if proc.returncode != 0:
        raise WatcherError(
            f"gh pr list failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        prs = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise WatcherError(f"gh pr list returned invalid JSON: {exc}") from exc
    if not isinstance(prs, list):
        raise WatcherError("gh pr list returned a non-list payload")
    return prs


# ── Coverage ──────────────────────────────────────────────────────────


def _receipt_info(row: dict) -> dict:
    return {
        "receipt_id": row.get("receipt_id") or "",
        "emitted_at": row.get("emitted_at") or "",
        "signed": row.get("signature_or_attestation") is not None,
        "schema_version": row.get("schema_version") or "",
    }


def check_coverage(
    prs: list[dict],
    by_merge_sha: dict[str, dict],
    by_candidate_sha: dict[str, dict],
) -> dict:
    """Match each merged PR against the receipt index.

    Returns a report dict with per-PR detail plus ``total`` / ``covered`` /
    ``missing`` / ``missing_pr_numbers``.
    """
    pr_reports = []
    missing = []
    for pr in prs:
        number = pr.get("number")
        merge_oid = (pr.get("mergeCommit") or {}).get("oid") or ""
        head_oid = pr.get("headRefOid") or ""
        entry = {
            "number": number,
            "title": pr.get("title") or "",
            "merged_at": pr.get("mergedAt") or "",
            "merge_sha": merge_oid,
            "head_ref": pr.get("headRefName") or "",
            "url": pr.get("url") or "",
            "status": "missing",
            "matched_via": None,
            "receipt": None,
            "note": "",
        }
        row = by_merge_sha.get(merge_oid) if merge_oid else None
        if row is not None:
            entry["status"] = "covered"
            entry["matched_via"] = "merge_sha"
            entry["receipt"] = _receipt_info(row)
        else:
            row = by_candidate_sha.get(head_oid) if head_oid else None
            if row is not None:
                entry["status"] = "covered"
                entry["matched_via"] = "candidate_sha"
                entry["receipt"] = _receipt_info(row)
            elif not merge_oid and not head_oid:
                entry["note"] = "no merge-commit sha on PR; cannot verify"
            else:
                entry["note"] = "no matching merge receipt"
        if entry["status"] == "missing":
            missing.append(entry)
        pr_reports.append(entry)
    total = len(prs)
    covered = total - len(missing)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "covered": covered,
        "missing": missing,
        "missing_pr_numbers": [e["number"] for e in missing],
        "coverage_pct": (100.0 * covered / total) if total else 100.0,
        "prs": pr_reports,
    }


# ── Reporting ─────────────────────────────────────────────────────────


def _short(sha: str, n: int = 8) -> str:
    return sha[:n] if sha else "—"


def format_human_report(report: dict, *, repo: str, log_path: Path,
                        log_exists: bool) -> str:
    lines = [
        f"Merge-receipt coverage for {repo}: "
        f"{report['covered']}/{report['total']} "
        f"({report['coverage_pct']:.1f}%)",
        f"Receipt log: {log_path}"
        + ("" if log_exists else " (missing — no receipts on record)"),
        "",
    ]
    if report["total"] == 0:
        lines.append("No merged PRs in the window — nothing to check.")
        return "\n".join(lines) + "\n"
    missing = report["missing"]
    covered = [p for p in report["prs"] if p["status"] == "covered"]
    lines.append(f"MISSING RECEIPTS ({len(missing)}):")
    if missing:
        for e in missing:
            title = (e["title"][:60] + "…") if len(e["title"]) > 60 else e["title"]
            lines.append(
                f"  #{e['number']}  {title}  merged {e['merged_at'][:10]}  "
                f"merge {_short(e['merge_sha'])}  — {e['note']}"
            )
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"COVERED ({len(covered)}):")
    if covered:
        for e in covered:
            r = e["receipt"] or {}
            sig = "signed" if r.get("signed") else "unsigned"
            lines.append(
                f"  #{e['number']}  via {e['matched_via']}  "
                f"receipt {r.get('receipt_id', '')[:8]}…  {sig}"
            )
    else:
        lines.append("  (none)")
    if missing:
        lines.append("")
        lines.append(
            "Note: merges done in the GitHub UI never pass through "
            "merge_executor, so they emit no receipt. Every PR above is a "
            "finish-line-3 gap: merged without verifiable proof."
        )
    return "\n".join(lines) + "\n"


def format_json_report(report: dict) -> str:
    payload = {
        "repo": report.get("repo", ""),
        "checked_at": report.get("checked_at", ""),
        "total": report.get("total", 0),
        "covered": report.get("covered", 0),
        "coverage_pct": report.get("coverage_pct"),
        "missing_pr_numbers": report.get("missing_pr_numbers", []),
        "missing": [
            {k: e.get(k, "") for k in ("number", "title", "merged_at", "merge_sha",
                                       "head_ref", "url", "note")}
            for e in report.get("missing", [])
        ],
        "log_path": report.get("log_path", ""),
        "log_exists": report.get("log_exists", False),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# -- Backfill emission (WR-3) ------------------------------------------------

# The prismatic import below is lazy so watch mode stays stdlib-only; it only
# runs when --emit-missing is passed.

WR3_NON_CLAIMS = [
    (
        "backfilled post-merge by WR-3 receipt backfill; "
        "merge did not go through merge_executor"
    ),
    "no Prismatic verification receipts exist for this merge",
]

WR3_POLICY_VERSION = "wr-3"
WR3_CHANGE_CLASS = "github-ui-backfill"
WR3_AUTHORIZATION_ID = "github-ui-manual-merge"


def _git(git_dir, *args):
    """Run git in git_dir; return stripped stdout, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", git_dir, *args],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def resolve_backfill_fields(pr, *, git_dir):
    """Derive receipt fields for one merged PR from gh data + local git.

    Returns None when the merge commit is absent from local git (base_sha
    cannot be established from git).
    """
    merge_sha = (pr.get("mergeCommit") or {}).get("oid") or ""
    if not merge_sha:
        return None
    if _git(git_dir, "cat-file", "-t", merge_sha) != "commit":
        return None
    base_sha = _git(git_dir, "rev-parse", merge_sha + "^1") or ""
    parents = (_git(git_dir, "log", "--format=%P", "-n", "1", merge_sha) or "").split()
    if len(parents) >= 2:
        candidate_sha = _git(git_dir, "rev-parse", merge_sha + "^2") or ""
    else:
        # squash merge: single parent; the PR head is the candidate
        candidate_sha = pr.get("headRefOid") or ""
    candidate_tree = ""
    if candidate_sha:
        candidate_tree = _git(git_dir, "rev-parse", candidate_sha + "^{tree}") or ""
    merged_by = pr.get("mergedBy") or {}
    actor = merged_by.get("login") or "unknown"
    return {
        "number": pr.get("number"),
        "title": pr.get("title") or "",
        "merged_at": pr.get("mergedAt") or "",
        "merge_sha": merge_sha,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "actor": actor,
    }


def emit_missing_receipts(missing, *, repo, log_path, git_dir):
    """Build, sign, and persist a receipt for each missing merged PR.

    Idempotent on merge_sha: skips when a receipt already exists. Returns a
    summary dict {"emitted": [...], "skipped_duplicate": [...],
    "failed": [...]}. Never raises -- per-PR failures are collected.
    """
    try:
        from prismatic.verification.merge_receipt import (
            build_merge_receipt,
            find_merge_receipts,
            persist_merge_receipt,
            sign_merge_receipt,
        )
    except ImportError as exc:
        raise WatcherError(
            "--emit-missing requires the prismatic package "
            f"(prismatic.verification.merge_receipt): {exc}"
        ) from exc

    summary = {"emitted": [], "skipped_duplicate": [], "failed": []}
    for pr in missing:
        number = pr.get("number")
        try:
            fields = resolve_backfill_fields(pr, git_dir=git_dir)
            if fields is None:
                summary["failed"].append(
                    {"number": number, "reason": "merge commit not in local git"}
                )
                continue
            merge_sha = fields["merge_sha"]
            if find_merge_receipts(merge_sha=merge_sha, log_path=log_path):
                summary["skipped_duplicate"].append(number)
                continue
            receipt = build_merge_receipt(
                repository=repo,
                candidate_sha=fields["candidate_sha"],
                candidate_tree=fields["candidate_tree"],
                base_sha=fields["base_sha"],
                merge_sha=merge_sha,
                actor=fields["actor"],
                authorization_id=WR3_AUTHORIZATION_ID,
                job_id=f"github-backfill:{merge_sha[:12]}",
                task_id=(f"PR-{number}") if number else "",
                policy_version=WR3_POLICY_VERSION,
                change_class=WR3_CHANGE_CLASS,
            )
            receipt.setdefault("explicit_non_claims", []).extend(WR3_NON_CLAIMS)
            sign_merge_receipt(receipt)
            receipt_id = persist_merge_receipt(receipt, log_path=log_path)
            if receipt_id is None:
                summary["failed"].append(
                    {"number": number, "reason": "persist failed"}
                )
                continue
            summary["emitted"].append(
                {"number": number, "receipt_id": receipt_id,
                 "merge_sha": merge_sha}
            )
        except Exception as exc:  # noqa: BLE001 -- never break the sweep on one PR
            summary["failed"].append({"number": number, "reason": str(exc)})
    return summary



# ── CLI ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch merge-receipt coverage: flag merged PRs with no "
                    "signed merge-executor receipt. Read-only."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help=f"GitHub repo (default {DEFAULT_REPO})")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"recent merged PRs to check (default {DEFAULT_LIMIT})")
    parser.add_argument("--receipts", default=None,
                        help="explicit merge-receipts JSONL path "
                             "(default: env override or ~/.prismatic/merge-receipts.jsonl)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON report on stdout")
    parser.add_argument("--gh", default="gh",
                        help="gh binary to invoke (default: gh)")
    parser.add_argument("--emit-missing", action="store_true",
                        help="WR-3: build, sign, and persist merge receipts "
                             "for merged PRs lacking one (requires the "
                             "prismatic package and signing key; idempotent)")
    parser.add_argument("--git-dir", default=None,
                        help="git working tree used to resolve merge parents "
                             "for --emit-missing (default: current directory)")
    return parser


def _coverage_report(prs, repo, log_path):
    """Assemble the coverage report dict (shared by watch and emit flows)."""
    by_merge_sha, by_candidate_sha, stats = load_receipt_index(log_path)
    report = check_coverage(prs, by_merge_sha, by_candidate_sha)
    report["repo"] = repo
    report["log_path"] = str(log_path)
    report["log_exists"] = stats["log_exists"]
    report["receipt_stats"] = stats
    return report


def format_emit_summary(summary, log_path):
    lines = [
        "",
        "BACKFILL (--emit-missing):",
        f"  emitted: {len(summary['emitted'])}",
        f"  skipped_duplicate: {len(summary['skipped_duplicate'])}",
        f"  failed: {len(summary['failed'])}",
        f"  receipt log: {log_path}",
    ]
    for f in summary["failed"]:
        lines.append(f"  FAILED #{f['number']}: {f['reason']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prs = fetch_merged_prs(gh_bin=args.gh, repo=args.repo, limit=args.limit)
        log_path = resolve_receipt_log_path(args.receipts)
        report = _coverage_report(prs, args.repo, log_path)
        emit_summary = None
        if args.emit_missing and report["missing"]:
            emit_summary = emit_missing_receipts(
                report["missing"],
                repo=args.repo,
                log_path=log_path,
                git_dir=args.git_dir or os.getcwd(),
            )
            report = _coverage_report(prs, args.repo, log_path)
            report["emit_summary"] = emit_summary
    except WatcherError as exc:
        print(f"receipt_coverage_watch: error: {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    if args.json:
        print(format_json_report(report), end="")
    else:
        print(
            format_human_report(
                report, repo=args.repo,
                log_path=log_path, log_exists=report["log_exists"],
            ),
            end="",
        )
        if emit_summary is not None:
            print(format_emit_summary(emit_summary, log_path), end="")
    return EXIT_OK if not report["missing"] else EXIT_COVERAGE_GAP


if __name__ == "__main__":
    sys.exit(main())
