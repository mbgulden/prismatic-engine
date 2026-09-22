#!/usr/bin/env python3
"""Shadow-mode entry point for typed failure diagnosis + attention routing.

Reads GitHub events via the `gh` CLI, runs the deterministic diagnoser /
attention router, prints a human-readable summary, and appends the shadow
audit rows. Observe-only: never dispatches, freezes, comments, or gates.

Exit code is always 0: every failure mode (gh error, unreadable event,
Jev error) is logged and the run ends cleanly — fail-closed means
"diagnose nothing", never "crash the workflow".

Usage:
    failure_diagnosis_shadow.py diagnose-ci --run-id 123 [--repo o/r]
    failure_diagnosis_shadow.py diagnose-watchdog --metric ci_failure_rate
    failure_diagnosis_shadow.py score-pr --pr-number 42 [--repo o/r]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prismatic.review_factory.attention_routing import (  # noqa: E402
    AttentionRouter,
    PRInput,
)
from prismatic.review_factory.failure_diagnosis import (  # noqa: E402
    FailureDiagnoser,
    diagnose_ci_failure,
    diagnose_deploy_failure,
    diagnose_watchdog_trip,
)

_MAX_JOB_LOG_CHARS = 6000


def _gh_api(path: str) -> dict | list | None:
    """GET a GitHub API path via the gh CLI. Returns parsed JSON or None."""
    try:
        out = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"gh api unavailable: {exc}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"gh api {path} failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        print(f"gh api {path}: bad JSON: {exc}", file=sys.stderr)
        return None


def _failed_job_text(repo: str, run_id: str) -> tuple[str, tuple[str, ...]]:
    """Collect error text + failed check names from a completed run's jobs."""
    data = _gh_api(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    if not isinstance(data, dict):
        return "", ()
    texts: list[str] = []
    failed: list[str] = []
    for job in data.get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        failed.append(str(job.get("name", "unknown-job")))
        for step in job.get("steps", []):
            if step.get("conclusion") == "failure":
                texts.append(f"step: {step.get('name', '?')}")
    blob = "\n".join(texts)[:_MAX_JOB_LOG_CHARS]
    return blob, tuple(failed)


def cmd_diagnose_ci(args: argparse.Namespace) -> int:
    error_text, failed_checks = _failed_job_text(args.repo, args.run_id)
    diagnoser = FailureDiagnoser()
    result = diagnose_ci_failure(
        diagnoser,
        error_text=error_text,
        failed_checks=failed_checks,
        commit_sha=args.commit_sha or "",
    )
    print(
        f"diagnosis={result.diagnosis.value} route={result.route} "
        f"matched_rule={result.matched_rule} jev={result.jev_status} "
        f"[shadow — no action taken]"
    )
    return 0


def cmd_diagnose_deploy(args: argparse.Namespace) -> int:
    error_text, failed_checks = _failed_job_text(args.repo, args.run_id)
    diagnoser = FailureDiagnoser()
    result = diagnose_deploy_failure(
        diagnoser,
        error_text=error_text,
        error_classes=tuple(failed_checks),
        deploy_stage=args.stage or "",
    )
    print(
        f"diagnosis={result.diagnosis.value} route={result.route} "
        f"page={result.page} jev={result.jev_status} "
        f"[shadow — no action taken]"
    )
    return 0


def cmd_diagnose_watchdog(args: argparse.Namespace) -> int:
    diagnoser = FailureDiagnoser()
    result = diagnose_watchdog_trip(
        diagnoser, metric_name=args.metric, error_text=args.error_text or ""
    )
    print(
        f"metric={args.metric} diagnosis={result.diagnosis.value} "
        f"route={result.route} page={result.page} "
        f"[shadow — no action taken]"
    )
    return 0


def cmd_score_pr(args: argparse.Namespace) -> int:
    pr = _gh_api(f"repos/{args.repo}/pulls/{args.pr_number}")
    if not isinstance(pr, dict):
        print("could not read PR; scored nothing", file=sys.stderr)
        return 0
    files_data = _gh_api(f"repos/{args.repo}/pulls/{args.pr_number}/files?per_page=100")
    files = (
        tuple(str(f.get("filename", "")) for f in files_data)
        if isinstance(files_data, list)
        else ()
    )
    head_sha = str((pr.get("head") or {}).get("sha", ""))
    failing = 0
    if head_sha:
        checks = _gh_api(
            f"repos/{args.repo}/commits/{head_sha}/check-runs?per_page=100"
        )
        if isinstance(checks, dict):
            failing = sum(
                1
                for c in checks.get("check_runs", [])
                if c.get("conclusion") == "failure"
            )
    router = AttentionRouter()
    result = router.score_pr(
        PRInput(
            number=int(args.pr_number),
            head_sha=head_sha,
            author=str((pr.get("user") or {}).get("login", "")),
            additions=int(pr.get("additions") or 0),
            deletions=int(pr.get("deletions") or 0),
            files=files,
            failing_checks=failing,
        )
    )
    print(
        f"pr=#{args.pr_number} score={result.score:.1f} band={result.band} "
        f"factors={', '.join(result.top_factors)} jev={result.jev_status} "
        f"[advisory only — no action taken]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("diagnose-ci", help="diagnose a failed CI workflow run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--commit-sha", default="")

    p = sub.add_parser("diagnose-deploy", help="diagnose a failed deploy run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--stage", default="")

    p = sub.add_parser("diagnose-watchdog", help="diagnose a watchdog trip")
    p.add_argument("--metric", required=True)
    p.add_argument("--error-text", default="")

    p = sub.add_parser("score-pr", help="risk-score a pull request")
    p.add_argument("--pr-number", required=True)
    p.add_argument("--repo", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "diagnose-ci":
            return cmd_diagnose_ci(args)
        if args.command == "diagnose-deploy":
            return cmd_diagnose_deploy(args)
        if args.command == "diagnose-watchdog":
            return cmd_diagnose_watchdog(args)
        if args.command == "score-pr":
            return cmd_score_pr(args)
    except Exception as exc:  # fail-closed: log, exit 0
        print(f"shadow run failed closed: {exc!r}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
