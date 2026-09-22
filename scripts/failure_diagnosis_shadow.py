#!/usr/bin/env python3
"""Failure-diagnosis shadow driver (Jev #28).

Reads the GitHub event payload (``workflow_run`` completion) or explicit
``workflow_dispatch`` inputs, runs the typed failure diagnoser in shadow
mode, and prints a human summary. Observe + log only: this script never
dispatches response actions, never gates anything, and never fails the
workflow on a clean skip.

Usage:
    python3 scripts/failure_diagnosis_shadow.py [--repo .]
        [--event-path $GITHUB_EVENT_PATH]
        [--failure-kind ci-failure] [--error-text "..."] ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(_repo_root()))

    ap = argparse.ArgumentParser(description="Failure diagnosis (shadow mode)")
    ap.add_argument("--repo", default=".", help="Repo checkout (unused, for symmetry)")
    ap.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH", ""),
        help="GitHub event payload path",
    )
    ap.add_argument(
        "--failure-kind",
        default="",
        help="watchdog-trip | deploy-failure | ci-failure (default: derived)",
    )
    ap.add_argument(
        "--error-text",
        default=os.environ.get("FAILURE_DIAGNOSIS_ERROR_TEXT", ""),
        help="Error text to classify",
    )
    ap.add_argument(
        "--error-classes",
        default="",
        help="Comma-separated error-class tokens",
    )
    ap.add_argument("--commit-sha", default="", help="Commit SHA under diagnosis")
    ap.add_argument(
        "--test-history",
        default="",
        help='JSON list of [sha, "pass"|"fail"] pairs',
    )
    ap.add_argument("--source", default="", help="Failure source label")
    args = ap.parse_args()

    from prismatic.review_factory.failure_diagnosis import (
        DiagnosisInput,
        FailureDiagnosis,
    )

    event: dict = {}
    if args.event_path and Path(args.event_path).exists():
        event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))

    failure_kind = args.failure_kind
    error_text = args.error_text
    error_classes = tuple(c.strip() for c in args.error_classes.split(",") if c.strip())
    commit_sha = args.commit_sha
    source = args.source
    test_history: tuple = ()

    if "inputs" in event and isinstance(event["inputs"], dict):
        inputs = event["inputs"]
        failure_kind = failure_kind or str(inputs.get("failure_kind", ""))
        error_text = error_text or str(inputs.get("error_text", ""))
        commit_sha = commit_sha or str(inputs.get("commit_sha", ""))
        source = source or "workflow_dispatch"

    run = event.get("workflow_run")
    if isinstance(run, dict):
        workflow_name = str(run.get("name", ""))
        conclusion = str(run.get("conclusion", ""))
        if not failure_kind and conclusion != "failure":
            print(
                f"workflow_run conclusion is {conclusion!r} (not failure): "
                "nothing to diagnose."
            )
            return 0
        failure_kind = failure_kind or (
            "deploy-failure" if "deploy" in workflow_name.lower() else "ci-failure"
        )
        commit_sha = commit_sha or str(run.get("head_sha", ""))
        source = source or f"{workflow_name} run {run.get('id')} ({conclusion})"

    if args.test_history:
        test_history = tuple(tuple(pair) for pair in json.loads(args.test_history))

    if not failure_kind:
        print("No failure to diagnose (no failure-kind and no failure event).")
        return 0

    diag = FailureDiagnosis()
    result = diag.diagnose(
        DiagnosisInput(
            failure_kind=failure_kind,
            error_text=error_text,
            error_classes=error_classes,
            commit_sha=commit_sha,
            test_history=test_history,
            source=source,
        )
    )

    print("Failure diagnosis (shadow — no action taken)")
    print(f"  kind:            {result.failure_kind}")
    print(f"  source:          {result.source}")
    print(
        f"  deterministic:   {result.deterministic_verdict or 'none'}"
        + (
            f" ({result.deterministic_evidence})"
            if result.deterministic_evidence
            else ""
        )
    )
    print(
        f"  jev:             {result.jev_status}"
        + (f" -> {result.jev_advice['choice']}" if result.jev_advice else "")
    )
    print(f"  final verdict:   {result.final_verdict}")
    print(f"  recommended:     {result.recommended_channel} (recommendation only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
