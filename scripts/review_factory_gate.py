#!/usr/bin/env python3
"""CI gate driver: run the review factory verifier against the PR diff.

Builds a MergeCandidateManifest + ReviewJob for the current checkout, runs
VerificationWorker.verify(), and exits 0 only if every check passed (the
manifest advances CANDIDATE -> REVIEW_REQUIRED). Any failure prints the
failing checks and exits 1. Designed to run on the self-hosted runner where
GitHub minutes are free.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Review factory CI gate")
    ap.add_argument("--repo", default=".", help="Repo checkout to verify")
    ap.add_argument(
        "--base",
        default="origin/main",
        help="Diff base for changed files (default: origin/main)",
    )
    ap.add_argument(
        "--tier",
        default="A",
        choices=["A", "B"],
        help="Risk tier: A = focused checks, B = focused + full suite + package",
    )
    ap.add_argument(
        "--log-dir",
        default="rf-gate-logs",
        help="Directory (under repo) to collect verification logs",
    )
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    sys.path.insert(0, str(repo))

    from prismatic.merge_candidate_manifest import (
        MergeCandidateManifest,
        PromotionState,
        RiskTier,
    )
    from prismatic.review_factory.models import ReviewJob
    from prismatic.review_factory.verifier import VerificationWorker

    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
    diff_ok = True
    try:
        base = _git(repo, "merge-base", args.base, "HEAD")
        changed = [
            p
            for p in _git(repo, "diff", "--name-only", f"{base}...{head}").split("\n")
            if p
        ]
    except subprocess.CalledProcessError:
        print(f"WARNING: could not diff {args.base}...HEAD; using empty change set")
        changed = []
        diff_ok = False

    if diff_ok and not changed:
        # Push-to-main runs diff the merge commit against itself (HEAD == base),
        # so there is nothing to verify. Skip cleanly instead of failing.
        print("GATE SKIP: no files changed between base and HEAD; nothing to verify")
        return 0

    print(f"candidate: {head}")
    print(f"tier: {args.tier}, changed files: {len(changed)}")
    for p in changed[:20]:
        print(f"  {p}")

    manifest = MergeCandidateManifest.create(
        issue_id=f"ci-gate-{head[:8]}",
        task_id=f"CI-GATE-{head[:8]}",
        task_file_sha256="0" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="0" * 40,
        candidate_sha=head,
        changed_paths=changed,
        producer="ci-gate",
        preserved_candidate_location=str(repo),
        risk_tier=RiskTier[args.tier],
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )
    job = ReviewJob(
        completed_work_id=f"ci-gate-{head[:8]}",
        task_id=f"CI-GATE-{head[:8]}",
        repository="mbgulden/prismatic-engine",
        base_commit="0" * 40,
        base_tree="0" * 40,
        candidate_commit=head,
        candidate_tree=tree,
        changed_paths_json=json.dumps(changed),
    )

    log_dir = repo / args.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    worker = VerificationWorker(repo_path=repo, log_dir=log_dir)
    receipt, updated = worker.verify(job, manifest)

    exit_codes = json.loads(receipt.exit_codes)
    failures = json.loads(receipt.baseline_failures)
    print("\n--- check results ---")
    for name, code in exit_codes.items():
        print(f"  {'PASS' if code == 0 else 'FAIL'}  {name} (exit {code})")
    print(f"receipt: {receipt.receipt_id}")
    print(f"manifest state: {updated.state.value}")

    if updated.state is PromotionState.REVIEW_REQUIRED:
        print("GATE PASS: all verification checks passed")
        return 0

    print("\nGATE FAIL:")
    for f in failures:
        print(f"  - {f}")
    print(f"logs: {log_dir}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
