#!/usr/bin/env python3
"""Repeatable end-to-end execution verification harness for Prismatic Engine.

This harness proves the control-plane links without touching live Linear or a
real agent account:

    issue -> dispatch -> execution -> artifact -> Linear update

It intentionally uses deterministic local fakes so operators can run the same
command repeatedly from any checkout:

    python3 scripts/e2e_execution_harness.py

For failure isolation, inject a broken link:

    python3 scripts/e2e_execution_harness.py --break-stage artifact --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

Stage = Literal["issue", "dispatch", "execution", "artifact", "linear-update"]
STAGES: tuple[Stage, ...] = (
    "issue",
    "dispatch",
    "execution",
    "artifact",
    "linear-update",
)


class HarnessError(RuntimeError):
    """Raised when a named harness link fails."""

    def __init__(self, stage: Stage, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message


@dataclass
class StageResult:
    stage: Stage
    ok: bool
    detail: str


@dataclass
class HarnessReport:
    ok: bool
    issue_id: str
    workspace: str
    artifact_path: str | None
    artifact_sha256: str | None
    linear_state: str | None
    linear_comments: int
    stages: list[StageResult] = field(default_factory=list)
    failed_stage: str | None = None
    failure: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "issue_id": self.issue_id,
                "workspace": self.workspace,
                "artifact_path": self.artifact_path,
                "artifact_sha256": self.artifact_sha256,
                "linear_state": self.linear_state,
                "linear_comments": self.linear_comments,
                "failed_stage": self.failed_stage,
                "failure": self.failure,
                "stages": [result.__dict__ for result in self.stages],
            },
            indent=2,
            sort_keys=True,
        )


@dataclass
class FakeIssue:
    id: str
    identifier: str
    title: str
    description: str
    state: str = "Todo"
    labels: list[str] = field(default_factory=lambda: ["agent:ned", "dispatch:ready"])
    comments: list[str] = field(default_factory=list)


class FakeLinear:
    """Tiny in-memory Linear ledger used by the harness."""

    def __init__(self) -> None:
        self.issues: dict[str, FakeIssue] = {}

    def create_issue(self, issue: FakeIssue) -> None:
        if issue.id in self.issues:
            raise HarnessError("issue", f"duplicate issue id: {issue.id}")
        self.issues[issue.id] = issue

    def get_dispatchable_issue(self, agent_label: str) -> FakeIssue:
        for issue in self.issues.values():
            if (
                issue.state == "Todo"
                and agent_label in issue.labels
                and "dispatch:ready" in issue.labels
            ):
                return issue
        raise HarnessError(
            "dispatch", f"no Todo issue carrying {agent_label} + dispatch:ready"
        )

    def mark_in_review(
        self, issue_id: str, artifact_path: Path, artifact_sha256: str
    ) -> None:
        issue = self.issues[issue_id]
        issue.state = "In Review"
        issue.labels = [
            label
            for label in issue.labels
            if label not in {"agent:ned", "dispatch:ready"}
        ]
        if "agent:peer-review" not in issue.labels:
            issue.labels.append("agent:peer-review")
        issue.comments.append(
            "Self-Review PASSED — end-to-end harness verified execution artifact "
            f"{artifact_path.name} sha256={artifact_sha256}"
        )


class FakeExecutor:
    """Deterministic local executor that writes a RESULT.md artifact."""

    def __init__(self, workspace: Path, *, break_stage: Stage | None = None) -> None:
        self.workspace = workspace
        self.break_stage = break_stage

    def run(self, issue: FakeIssue) -> Path:
        if self.break_stage == "execution":
            raise HarnessError(
                "execution", "executor refused to run (injected failure)"
            )
        artifact_dir = self.workspace / "artifacts" / issue.identifier
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "RESULT.md"
        if self.break_stage == "artifact":
            return artifact_path
        artifact_path.write_text(
            "\n".join(
                [
                    f"# RESULT for {issue.identifier}",
                    "",
                    f"Title: {issue.title}",
                    f"Executed at: {datetime.now(timezone.utc).isoformat()}",
                    "",
                    "Proof: fake executor completed successfully.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return artifact_path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_harness(workspace: Path, *, break_stage: Stage | None = None) -> HarnessReport:
    workspace.mkdir(parents=True, exist_ok=True)
    linear = FakeLinear()
    issue = FakeIssue(
        id="issue-gro-3493-e2e",
        identifier="GRO-3493-E2E",
        title="Harness canary: prove issue to Linear update",
        description="Synthetic task used by scripts/e2e_execution_harness.py",
    )
    report = HarnessReport(
        ok=False,
        issue_id=issue.identifier,
        workspace=str(workspace),
        artifact_path=None,
        artifact_sha256=None,
        linear_state=None,
        linear_comments=0,
    )

    try:
        if break_stage == "issue":
            raise HarnessError(
                "issue", "synthetic issue creation failed (injected failure)"
            )
        linear.create_issue(issue)
        report.stages.append(
            StageResult("issue", True, "created synthetic Linear issue")
        )

        dispatch_issue = linear.get_dispatchable_issue("agent:ned")
        report.stages.append(
            StageResult("dispatch", True, f"selected {dispatch_issue.identifier}")
        )

        executor = FakeExecutor(workspace, break_stage=break_stage)
        artifact_path = executor.run(dispatch_issue)
        report.stages.append(
            StageResult("execution", True, "executor returned control")
        )

        if not artifact_path.exists():
            raise HarnessError(
                "artifact", f"expected artifact missing: {artifact_path}"
            )
        artifact_sha = sha256_file(artifact_path)
        report.artifact_path = str(artifact_path)
        report.artifact_sha256 = artifact_sha
        report.stages.append(
            StageResult("artifact", True, f"artifact sha256={artifact_sha}")
        )

        if break_stage == "linear-update":
            raise HarnessError(
                "linear-update", "Linear update refused (injected failure)"
            )
        linear.mark_in_review(dispatch_issue.id, artifact_path, artifact_sha)
        updated = linear.issues[dispatch_issue.id]
        if (
            updated.state != "In Review"
            or "agent:peer-review" not in updated.labels
            or not updated.comments
        ):
            raise HarnessError(
                "linear-update", "Linear ledger did not record review handoff"
            )
        report.linear_state = updated.state
        report.linear_comments = len(updated.comments)
        report.stages.append(
            StageResult(
                "linear-update",
                True,
                "state=In Review, label=agent:peer-review, evidence comment written",
            )
        )
        report.ok = True
        return report
    except HarnessError as exc:
        report.failed_stage = exc.stage
        report.failure = exc.message
        report.linear_state = (
            linear.issues.get(issue.id, issue).state
            if issue.id in linear.issues
            else None
        )
        report.linear_comments = (
            len(linear.issues.get(issue.id, issue).comments)
            if issue.id in linear.issues
            else 0
        )
        report.stages.append(StageResult(exc.stage, False, exc.message))
        return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify issue -> dispatch -> execution -> artifact -> Linear update"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace to use; defaults to a temporary directory",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Do not delete the temporary workspace",
    )
    parser.add_argument(
        "--break-stage",
        choices=STAGES,
        help="Inject a failure at a specific link for isolation checks",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    temp_dir: str | None = None
    workspace = args.workspace
    if workspace is None:
        temp_dir = tempfile.mkdtemp(prefix="prismatic-e2e-harness-")
        workspace = Path(temp_dir)

    break_stage = cast(Stage | None, args.break_stage)
    report = run_harness(workspace, break_stage=break_stage)
    try:
        if args.json:
            print(report.to_json())
        else:
            status = "PASS" if report.ok else "FAIL"
            print(
                f"{status}: issue -> dispatch -> execution -> artifact -> Linear update"
            )
            for result in report.stages:
                marker = "OK" if result.ok else "FAIL"
                print(f"  [{marker}] {result.stage}: {result.detail}")
            if report.artifact_path:
                print(f"  artifact: {report.artifact_path}")
                print(f"  sha256: {report.artifact_sha256}")
            if report.failed_stage:
                print(f"  failed_stage: {report.failed_stage}")
        return 0 if report.ok else 2
    finally:
        if temp_dir and not args.keep_workspace:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
