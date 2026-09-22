#!/usr/bin/env python3
"""Shadow-mode failure triage driver for CI/deploy failures.

Reads the GitHub event payload (a ``workflow_run`` completion, or a manual
``workflow_dispatch`` with a ``run_id`` input), fetches the failed jobs for
the run via the GitHub API, triages each with
``prismatic.review_factory.failure_triage``, and writes the shadow audit
log. Observe + log ONLY: this script never dispatches repairs, never
retries jobs, never rejects candidates, never pages anyone.

The Jev call site stays default-off: unless ``SWARMJEV_ENABLED`` and
``SWARMJEV_CALLSITE_FAILURE_TRIAGE_ENABLED`` are both set in the runner
environment, triage is deterministic-rules-only and no Jev call is made.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG_EXCERPT_CHARS = 4000


def _gh_api(path: str) -> str:
    return subprocess.run(
        ["gh", "api", path, "--paginate"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def failed_jobs(repo: str, run_id: str) -> list[dict]:
    """Fetch failed jobs for a run. Read-only GitHub API calls."""
    data = json.loads(_gh_api(f"repos/{repo}/actions/runs/{run_id}/jobs"))
    jobs: list[dict] = []
    for job in data.get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        failed_steps = [
            s.get("name", "")
            for s in job.get("steps", [])
            if s.get("conclusion") == "failure"
        ]
        log_excerpt = ""
        try:
            logs = _gh_api(f"repos/{repo}/actions/jobs/{job['id']}/logs")
            log_excerpt = logs[-LOG_EXCERPT_CHARS:]
        except subprocess.CalledProcessError:
            pass  # logs unavailable: triage proceeds on names alone
        jobs.append(
            {
                "job_id": str(job.get("id", "unknown")),
                "name": job.get("name", ""),
                "head_sha": job.get("head_sha", ""),
                "failed_steps": failed_steps,
                "log_excerpt": log_excerpt,
            }
        )
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser(description="Shadow-mode failure triage driver")
    ap.add_argument(
        "--audit-log",
        default="failure-triage-shadow.jsonl",
        help="Where to write the shadow audit JSONL (uploaded as an artifact)",
    )
    args = ap.parse_args()

    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path or not Path(event_path).exists():
        print("GITHUB_EVENT_PATH is not set; nothing to triage")
        return 0
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    repo = os.environ.get("GITHUB_REPOSITORY", "mbgulden/prismatic-engine")

    if "workflow_run" in event:
        wr = event["workflow_run"]
        if wr.get("conclusion") != "failure":
            print(f"conclusion is {wr.get('conclusion')!r}; nothing to triage")
            return 0
        run_id = str(wr.get("id", ""))
        workflow_name = str(wr.get("name", ""))
        conclusion = str(wr.get("conclusion", ""))
    else:
        run_id = str((event.get("inputs") or {}).get("run_id", ""))
        if not run_id:
            print("workflow_dispatch without a run_id input; nothing to triage")
            return 0
        workflow_name, conclusion = "manual", "failure"

    jobs = failed_jobs(repo, run_id)
    print(f"triaging {len(jobs)} failed job(s) from run {run_id} ({workflow_name})")

    from prismatic.review_factory.failure_triage import (
        FailureTriage,
        triage_ci_failure,
    )

    triager = FailureTriage(audit_log=args.audit_log)
    results = triage_ci_failure(
        workflow_name=workflow_name,
        run_id=run_id,
        conclusion=conclusion,
        failed_jobs=jobs,
        triager=triager,
    )
    for r in results:
        print(
            f"  {r.failure_id}: deterministic={r.deterministic_verdict} "
            f"jev={r.jev.status}/{r.jev.choice} final={r.final_verdict} "
            "[shadow \u2014 no action taken]"
        )
    print(f"shadow audit log: {args.audit_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
