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

Modes (``--mode``):

- ``shadow`` (default): observe + log only. Byte-for-byte today's behavior:
  one shadow audit signal per failure, no retries, no repairs, no pages.
- ``active``: after emitting the shadow audit row, each triage result is
  offered to the enabled triage-action executors
  (``prismatic.review_factory.triage_actions``). Today only ``execute_retry``
  exists, behind ``PRISMATIC_TRIAGE_RETRY_ENABLED`` (default off) — with the
  flag off, active mode behaves exactly like shadow mode.
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
    try:
        return subprocess.run(
            ["gh", "api", path, "--paginate"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        # Include gh's stderr so the next failure is diagnosable from logs
        # instead of a bare CalledProcessError.
        raise RuntimeError(
            f"gh api {path} failed (exit {exc.returncode}): "
            f"{(exc.stderr or '').strip()[:500]}"
        ) from exc


def failed_jobs(repo: str, run_id: str) -> list[dict] | None:
    """Fetch failed jobs for a run. Read-only GitHub API calls.

    Returns ``None`` when the jobs API itself is unreadable (e.g. the
    workflow token lacks ``actions:read``) so the caller can degrade to
    names-only triage instead of crashing.
    """
    try:
        data = json.loads(_gh_api(f"repos/{repo}/actions/runs/{run_id}/jobs"))
    except RuntimeError as exc:
        print(f"jobs API unreadable ({exc}); degrading to names-only triage")
        return None
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
        except RuntimeError:
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
    ap.add_argument(
        "--mode",
        choices=("shadow", "active"),
        default="shadow",
        help="shadow: observe + log only (default). active: also offer each "
        "triage result to the enabled triage-action executors.",
    )
    ap.add_argument(
        "--retry-state-path",
        default=None,
        help="Path to the retry idempotency state file. Defaults to "
        "$PRISMATIC_TRIAGE_STATE_DIR/triage-retry-state.json.",
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

    from prismatic.review_factory.failure_triage import (
        FailureInput,
        FailureTriage,
        triage_ci_failure,
    )
    from prismatic.review_factory.triage_actions import (
        RetriedRunStore,
        RetryRequest,
        default_retry_store,
        execute_retry,
    )

    def _active_retries(results, *, repo, run_id):
        """Offer each triage result to execute_retry (active mode only)."""
        store = (
            RetriedRunStore(args.retry_state_path)
            if args.retry_state_path
            else default_retry_store()
        )
        for r in results:
            req = RetryRequest(
                repo=repo,
                run_id=run_id,
                failure_id=r.failure_id,
                deterministic_verdict=r.deterministic_verdict,
                deterministic_evidence=r.deterministic_evidence,
            )
            res = execute_retry(req, store=store, audit_log=args.audit_log)
            print(
                f"  {r.failure_id}: deterministic={r.deterministic_verdict} "
                f"jev={r.jev.status}/{r.jev.choice} final={r.final_verdict} "
                f"[active: retry executed={res.executed} reason={res.reason}]"
            )

    triager = FailureTriage(audit_log=args.audit_log)
    # A failed run always has at least one failed job: an empty list means
    # the jobs API returned nothing usable (observed 2026-09-28), not "no
    # failures". Degrade to names-only triage rather than silently triaging
    # zero jobs and emitting zero audit rows.
    if not jobs:
        # Degraded path: the jobs API was unreadable or returned no jobs,
        # so triage the run by name only. The workflow still emits its one
        # shadow audit signal (uploaded as the artifact) instead of crashing.
        result = triager.triage(
            FailureInput(
                failure_id=f"ci:{run_id}:jobs-unavailable",
                source="ci",
                error_text=(
                    "failed-jobs API unreadable for this run; "
                    "names-only triage (no job detail available)"
                ),
                test_name=workflow_name,
                metadata={
                    "workflow_name": workflow_name,
                    "run_id": run_id,
                    "conclusion": conclusion,
                    "degraded": True,
                },
            )
        )
        if args.mode == "active":
            _active_retries([result], repo=repo, run_id=run_id)
        else:
            print(
                f"  {result.failure_id}: [shadow \u2014 jobs API unavailable, "
                "no action taken]"
            )
        print(f"shadow audit log: {args.audit_log}")
        return 0

    print(f"triaging {len(jobs)} failed job(s) from run {run_id} ({workflow_name})")
    results = triage_ci_failure(
        workflow_name=workflow_name,
        run_id=run_id,
        conclusion=conclusion,
        failed_jobs=jobs,
        triager=triager,
    )
    if args.mode == "active":
        _active_retries(results, repo=repo, run_id=run_id)
    else:
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
