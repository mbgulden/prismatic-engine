#!/usr/bin/env python3
"""Branch-protection tripwire — detect unexpected movements of main.

Single-committer private repo: the realistic threats are an accidental
direct push to main and a compromised token pushing. This monitor polls the
main branch head and trips when the head is NOT associated with a merged PR
(direct push) or when history was rewritten (force-push).

The source of truth is GitHub's PR association, NOT merge receipts and NOT
parent count: Michael squash-merges from the GitHub UI (single-parent
commits, zero engine receipts), so both of those would false-positive on
every legitimate merge.

On trip: emits an error-severity Prismatic audit signal and opens a revert
PR for Michael to merge. It NEVER force-pushes or auto-merges.

Usage:
    python3 scripts/branch_protection_tripwire.py --check [--dry-run]

Exit codes: 0 = head expected (or dry-run), 2 = trip detected, 1 = error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ.get("TRIPWIRE_REPO", "mbgulden/prismatic-engine")
BRANCH = "main"
DEFAULT_STATE = os.path.expanduser(
    "~/workspace/goals/prismatic-engine-hardening-and-portability"
    "/hidden_files/tripwire/state.json"
)
SKILL_BIN = Path(
    os.environ.get(
        "TRIPWIRE_SKILL_BIN",
        os.path.expanduser("~/workspace/skills/prismatic-audit/bin"),
    )
)
GRACE_SECONDS = 60  # re-check window for GitHub API association lag


# ── GitHub transport ──────────────────────────────────────────────────


def _surrogate_request(url: str, method: str = "GET", payload: dict | None = None):
    """Build a urllib Request with hatch-VM surrogate auth, else GITHUB_TOKEN."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "prismatic-tripwire",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
        import dynamic_credentials as dc

        dc.add_surrogate_to_request(
            req, "custom.github", allowed_hosts=["api.github.com"]
        )
    except Exception:  # noqa: BLE001
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError(
                "no GitHub auth: surrogate unavailable and GITHUB_TOKEN unset"
            )
        req.add_header("Authorization", f"Bearer {token}")
    return req


def gh(api_path: str, method: str = "GET", payload: dict | None = None) -> dict:
    req = _surrogate_request(f"https://api.github.com{api_path}", method, payload)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


# ── Pure classification logic (unit-tested) ───────────────────────────


def classify_head(associated_prs: list[dict]) -> str:
    """expected iff the head commit is associated with >= 1 merged PR.

    Parent count is deliberately ignored: squash merges (the normal flow)
    are single-parent commits.
    """
    if any(pr.get("merged_at") for pr in associated_prs):
        return "expected"
    return "unexpected-push"


def classify_ancestry(compare_status: str | None) -> str:
    """ok iff the previous head is still an ancestor of the current head."""
    if compare_status in ("ahead", "identical"):
        return "ok"
    if compare_status in ("behind", "diverged"):
        return "rewritten"
    return "unknown"


def revert_commit_message(bad_sha: str, good_sha: str, n_commits: int) -> str:
    noun = "commit" if n_commits == 1 else "commits"
    return (
        f"Revert {n_commits} unexpected direct-push {noun} to main\n\n"
        f"Branch-protection tripwire: {bad_sha[:8]} had no associated merged PR.\n"
        f"Restores the tree of last-known-good {good_sha[:8]}.\n"
        f"Close this PR if the push was intentional."
    )


# ── State ─────────────────────────────────────────────────────────────


def load_state(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


# ── Alerting ──────────────────────────────────────────────────────────


def emit_signal(message: str, args_preview: str, severity: str = "error") -> None:
    """Emit a Prismatic audit signal; never raise (monitoring must not crash)."""
    try:
        subprocess.run(
            [
                str(SKILL_BIN / "emit"),
                "--type",
                "error",
                "--message",
                message[:500],
                "--tool",
                "branch-protection-tripwire",
                "--args",
                args_preview[:300],
                "--severity",
                severity,
            ],
            check=False,
            timeout=30,
        )
        subprocess.run([str(SKILL_BIN / "push")], check=False, timeout=60)
    except Exception as exc:  # noqa: BLE001
        print(f"tripwire: signal emission failed: {exc}", file=sys.stderr)


# ── Response: revert / incident PRs (all API-driven, no local clone) ──


def open_revert_pr(bad_sha: str, good_sha: str, n_commits: int) -> dict:
    """Restore last-good tree on a muse/ branch and open a PR. Returns the PR."""
    good_tree = gh(f"/repos/{REPO}/commits/{good_sha}")["commit"]["tree"]["sha"]
    commit = gh(
        f"/repos/{REPO}/git/commits",
        method="POST",
        payload={
            "message": revert_commit_message(bad_sha, good_sha, n_commits),
            "tree": good_tree,
            "parents": [bad_sha],
        },
    )
    branch = f"muse/tripwire-revert-{bad_sha[:8]}"
    gh(
        f"/repos/{REPO}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
    )
    pr = gh(
        f"/repos/{REPO}/pulls",
        method="POST",
        payload={
            "title": f"Revert unexpected direct push to main ({bad_sha[:8]})",
            "head": branch,
            "base": BRANCH,
            "body": (
                "The branch-protection tripwire detected a push to `main` with "
                f"no associated merged PR (`{bad_sha[:8]}`).\n\n"
                "This PR restores the tree of the last-known-good commit "
                f"`{good_sha[:8]}`. Merge it to undo the push, or close it if "
                "the push was intentional.\n\n"
                "The tripwire never force-pushes or auto-merges."
            ),
        },
    )
    return pr


def open_rewrite_incident_pr(bad_sha: str, good_sha: str) -> dict:
    """History was rewritten: branch the last-good SHA and open a restore PR."""
    branch = f"muse/tripwire-restore-{good_sha[:8]}"
    gh(
        f"/repos/{REPO}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{branch}", "sha": good_sha},
    )
    pr = gh(
        f"/repos/{REPO}/pulls",
        method="POST",
        payload={
            "title": f"Restore main after unexpected history rewrite ({good_sha[:8]})",
            "head": branch,
            "base": BRANCH,
            "body": (
                "The branch-protection tripwire detected that `main`'s history "
                "was rewritten (force-push): the previously seen head "
                f"`{good_sha[:8]}` is no longer an ancestor of `{bad_sha[:8]}`.\n\n"
                "This PR was cut from the last-known-good commit. **Merging it "
                "restores the good tree without anyone force-pushing.**\n\n"
                "Investigate before merging: check who pushed and why. "
                "The tripwire never force-pushes or auto-merges."
            ),
        },
    )
    return pr


# ── Check driver ──────────────────────────────────────────────────────


def check(dry_run: bool = False, state_path: str = DEFAULT_STATE) -> dict:
    """Run one tripwire check. Returns a verdict dict (also printed as JSON)."""
    now = datetime.now(timezone.utc).isoformat()
    state = load_state(state_path)

    head = gh(f"/repos/{REPO}/branches/{BRANCH}")["commit"]["sha"]

    def done(verdict: str, **extra) -> dict:
        result = {"verdict": verdict, "head": head[:8], "at": now, **extra}
        print(json.dumps(result))
        return result

    # First run: baseline, never trip — but flag an unrecognized head.
    if not state or not state.get("last_seen_sha"):
        baseline = {"last_seen_sha": head, "last_check_utc": now, "tripped_sha": None}
        if not dry_run:
            save_state(state_path, baseline)
        prs = gh(f"/repos/{REPO}/commits/{head}/pulls")
        if classify_head(prs) != "expected" and not dry_run:
            emit_signal(
                f"tripwire baselined an unrecognized main head {head[:8]} "
                "(no merged PR); manual review advised",
                f"head={head[:8]}",
                severity="warn",
            )
        return done("baseline-recorded", baseline_head=head[:8])

    last_seen = state["last_seen_sha"]

    # Quiet unless the head moved; one trip per incident.
    if head == last_seen:
        if not dry_run:
            state["last_check_utc"] = now
            save_state(state_path, state)
        return done("unchanged")
    if state.get("tripped_sha") == head:
        return done("already-tripped")

    prs = gh(f"/repos/{REPO}/commits/{head}/pulls")
    if classify_head(prs) == "unexpected-push":
        # Grace: GitHub's PR association can lag the push by seconds.
        time.sleep(GRACE_SECONDS)
        prs = gh(f"/repos/{REPO}/commits/{head}/pulls")
    kind = classify_head(prs)

    if kind == "expected":
        cmp = gh(f"/repos/{REPO}/compare/{last_seen}...{head}")
        if classify_ancestry(cmp.get("status")) == "rewritten":
            return _trip(
                "history-rewrite", head, last_seen, dry_run, state_path, state, now
            )
        if not dry_run:
            state.update(
                {"last_seen_sha": head, "last_check_utc": now, "tripped_sha": None}
            )
            save_state(state_path, state)
        return done("expected", prs=[p["number"] for p in prs if p.get("merged_at")])

    return _trip("direct-push", head, last_seen, dry_run, state_path, state, now)


def _trip(
    kind: str,
    head: str,
    last_seen: str,
    dry_run: bool,
    state_path: str,
    state: dict,
    now: str,
) -> dict:
    if dry_run:
        action = (
            f"would open revert PR restoring {last_seen[:8]}"
            if kind == "direct-push"
            else f"would open restore PR from {last_seen[:8]}"
        )
        print(json.dumps({"verdict": "TRIP", "kind": kind, "dry_run_action": action}))
        return {
            "verdict": "TRIP",
            "kind": kind,
            "dry_run": True,
            "dry_run_action": action,
        }
    if kind == "direct-push":
        cmp = gh(f"/repos/{REPO}/compare/{last_seen}...{head}")
        n = cmp.get("ahead_by") or 1
        pr = open_revert_pr(head, last_seen, n)
    else:
        pr = open_rewrite_incident_pr(head, last_seen)
    emit_signal(
        f"branch-protection tripwire TRIP: {kind} on main "
        f"({last_seen[:8]} -> {head[:8]}); revert PR #{pr['number']} opened",
        f"kind={kind} head={head[:8]} pr={pr['number']}",
    )
    state.update({"last_check_utc": now, "tripped_sha": head})
    # NOTE: last_seen_sha deliberately stays at the last GOOD head — the
    # baseline must not advance onto a tripped commit.
    save_state(state_path, state)
    print(json.dumps({"verdict": "TRIP", "kind": kind, "pr": pr["number"]}))
    return {"verdict": "TRIP", "kind": kind, "pr": pr["number"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run one check")
    ap.add_argument(
        "--dry-run", action="store_true", help="no signals, no PRs, no state writes"
    )
    ap.add_argument("--state", default=DEFAULT_STATE)
    a = ap.parse_args()
    if not a.check:
        ap.print_help()
        return 1
    try:
        result = check(dry_run=a.dry_run, state_path=a.state)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"verdict": "error", "error": str(exc)[:200]}))
        return 1
    return 2 if result.get("verdict") == "TRIP" and not a.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
