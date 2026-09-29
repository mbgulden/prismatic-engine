#!/usr/bin/env python3
"""Cancel stale queued CI runs so a post-merge deploy never waits behind them.

Event-driven companion to merges: runs on push to main (see
.github/workflows/ci-queue-janitor.yml) and cancels queued/waiting/requested/
pending workflow runs whose head SHA is neither a protected branch head
(main, deploy-fresh) nor the head of an open PR.

INVARIANTS (never violated, unit-tested in tests/test_ci_queue_janitor.py):
  - NEVER cancels in_progress runs (cancelling those left self-hosted runners
    stuck "busy" with nothing running).
  - NEVER cancels post-merge-deploy runs, or this workflow's own runs.

Auth: GITHUB_TOKEN env var (provided automatically in Actions). No
machine-specific credentials. Safe to run in any fork.

Usage: ci_queue_janitor.py [--dry-run]
"""

import argparse
import datetime
import json
import os
import sys
import urllib.request

OWNER_REPO = os.environ.get("GITHUB_REPOSITORY", "mbgulden/prismatic-engine")
API = f"https://api.github.com/repos/{OWNER_REPO}"
CANCELLABLE = {"queued", "waiting", "requested", "pending"}
NEVER_CANCEL_PATHS = {
    ".github/workflows/post-merge-deploy.yml",
    ".github/workflows/ci-queue-janitor.yml",
}
PROTECTED_BRANCHES = ("main", "deploy-fresh")
# Runs younger than this are never cancelled: closes the rapid-double-merge
# race where a janitor run fetches the main SHA just before a second push.
TOO_NEW_SECONDS = 120
MAX_PAGES = 3


def api(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ci-queue-janitor",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status == 204:
            return {}
        return json.loads(resp.read().decode())


def parse_ts(value):
    """Parse a GitHub ISO-8601 timestamp; None on failure (fail-closed: not new)."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def should_cancel(run, protected_shas, open_heads, now=None):
    """Pure decision function.

    run: dict with status/head_sha/path/created_at (created_at ISO-8601 or None).
    protected_shas: iterable of full SHAs treated like main (main, deploy-fresh).
    open_heads: iterable of full SHAs that are open PR heads.
    Returns (cancel: bool, reason: str).
    """
    if run.get("status") not in CANCELLABLE:
        return False, "not-cancellable-status"
    if run.get("path") in NEVER_CANCEL_PATHS:
        return False, "deploy-protected"
    head = (run.get("head_sha") or "")[:8]
    if head in {s[:8] for s in protected_shas if s}:
        return False, "protected-branch-head"
    if head in {h[:8] for h in open_heads if h}:
        return False, "open-pr-head"
    created = parse_ts(run.get("created_at"))
    if created is not None and now is not None:
        age = (now - created).total_seconds()
        if 0 <= age < TOO_NEW_SECONDS:
            return False, "too-new"
    return True, "stale-head"


def protected_branch_shas(token):
    shas = []
    for branch in PROTECTED_BRANCHES:
        try:
            data = api("GET", f"{API}/branches/{branch}", token)
            shas.append(data["commit"]["sha"])
        except Exception as exc:  # deploy-fresh may not exist; tolerate it
            print(f"note: could not resolve branch {branch}: {exc}", file=sys.stderr)
    return shas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("error: GITHUB_TOKEN is not set", file=sys.stderr)
        return 2

    protected = protected_branch_shas(token)
    prs = api("GET", f"{API}/pulls?state=open&per_page=100", token)
    open_heads = [p["head"]["sha"] for p in prs]
    now = datetime.datetime.now(datetime.timezone.utc)

    cancelled, kept = [], []
    page = 1
    while True:
        runs = api("GET", f"{API}/actions/runs?per_page=100&page={page}", token)[
            "workflow_runs"
        ]
        if not runs:
            break
        for r in runs:
            run = {
                "id": r["id"],
                "name": r["name"],
                "status": r["status"],
                "head_sha": r["head_sha"],
                "path": r.get("path", ""),
                "created_at": r.get("created_at"),
            }
            ok, reason = should_cancel(run, protected, open_heads, now)
            entry = (
                f"{r['id']} {r['name'][:40]} {r['head_sha'][:8]} "
                f"{r['status']} ({reason})"
            )
            if ok:
                if not args.dry_run:
                    api("POST", f"{API}/actions/runs/{r['id']}/cancel", token)
                cancelled.append(entry)
            else:
                kept.append(entry)
        if len(runs) < 100 or page >= MAX_PAGES:
            break
        page += 1

    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"[{stamp}] dry_run={args.dry_run} protected={[s[:8] for s in protected]}",
        f"cancelled={len(cancelled)} kept={len(kept)}",
    ]
    lines += ["  CANCEL " + c for c in cancelled]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
