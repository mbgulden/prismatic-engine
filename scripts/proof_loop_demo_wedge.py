#!/usr/bin/env python3
"""Prismatic Proof Loop 2 demo wedge smoke.

Builds a deterministic, non-destructive demo artifact for the Linear/GitHub
Automation Demo Wedge:

    Linear label event -> Prismatic routing decision -> bounded AGY execution
    fixture -> verification verdict -> operator/founder demo package.

The script never calls Linear, GitHub, AGY, or a network endpoint. It creates a
replayable fixture and evidence bundle that a non-internal viewer can inspect or
record in under 90 seconds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = REPO / "artifacts" / "proof-loop-demo-wedge"


@dataclass(frozen=True)
class DemoFixture:
    issue_id: str
    issue_identifier: str
    issue_title: str
    labels: list[str]
    github_repo: str
    github_event: str
    github_branch: str
    expected_agent: str
    verification_command: str


@dataclass(frozen=True)
class StageEvidence:
    stage: str
    status: str
    timestamp: str
    detail: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_fixture() -> DemoFixture:
    return DemoFixture(
        issue_id="fixture-linear-demo-0001",
        issue_identifier="DEMO-90",
        issue_title="Demo: verify Prismatic guarded agent dispatch",
        labels=["dispatch:ready", "agent:agy", "proof-loop:demo", "github:fixture"],
        github_repo="example/prismatic-demo-repo",
        github_event="pull_request.opened",
        github_branch="feature/demo-90-proof-loop",
        expected_agent="agy",
        verification_command="python3 -m py_compile demo_workspace/agent_output.py",
    )


def route_agent(labels: list[str]) -> str:
    for label in labels:
        if label.startswith("agent:"):
            return label.split(":", 1)[1]
    return "unassigned"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def deterministic_id(fixture: DemoFixture) -> str:
    raw = json.dumps(asdict(fixture), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def run_demo(output_dir: Path, *, clean: bool) -> dict:
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fixture = build_fixture()
    run_id = f"demo-{deterministic_id(fixture)}"
    workspace = output_dir / "demo_workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    fixture_payload = {
        "run_id": run_id,
        "linear_event": {
            "type": "issueLabelAdded",
            "source": "linear.fixture",
            "issue": {
                "id": fixture.issue_id,
                "identifier": fixture.issue_identifier,
                "title": fixture.issue_title,
                "labels": fixture.labels,
            },
        },
        "github_event": {
            "type": fixture.github_event,
            "source": "github.fixture",
            "repository": fixture.github_repo,
            "branch": fixture.github_branch,
        },
    }

    evidence: list[StageEvidence] = []
    evidence.append(
        StageEvidence(
            stage="trigger",
            status="verified",
            timestamp=utc_now(),
            detail=f"Fixture Linear issue {fixture.issue_identifier} carries dispatch:ready + agent:agy labels; GitHub fixture branch {fixture.github_branch} is attached.",
        )
    )

    routed_agent = route_agent(fixture.labels)
    routing_status = "verified" if routed_agent == fixture.expected_agent else "failed"
    evidence.append(
        StageEvidence(
            stage="routing",
            status=routing_status,
            timestamp=utc_now(),
            detail=f"Labels routed issue {fixture.issue_identifier} to agent:{routed_agent}.",
        )
    )

    agent_output = workspace / "agent_output.py"
    write_text(
        agent_output,
        dedent(
            '''
            """Demo AGY output fixture.

            This file represents the bounded work product in the 90-second demo.
            It is intentionally tiny and safe: verification is py_compile only.
            """

            def proof_loop_value() -> str:
                return "linear_label_to_governed_agent_to_verified_result"
            '''
        ).lstrip(),
    )
    evidence.append(
        StageEvidence(
            stage="bounded_agent_execution",
            status="verified",
            timestamp=utc_now(),
            detail=f"Fixture AGY work product written to {agent_output.relative_to(output_dir)} without external side effects.",
        )
    )

    import py_compile

    try:
        py_compile.compile(str(agent_output), doraise=True)
        verification_status = "verified"
        verification_detail = f"{fixture.verification_command} passed for {agent_output.relative_to(output_dir)}."
    except py_compile.PyCompileError as exc:
        verification_status = "failed"
        verification_detail = str(exc)

    evidence.append(
        StageEvidence(
            stage="verification",
            status=verification_status,
            timestamp=utc_now(),
            detail=verification_detail,
        )
    )

    cleanup_status = "clean"
    evidence.append(
        StageEvidence(
            stage="cleanup",
            status="verified",
            timestamp=utc_now(),
            detail="No Linear/GitHub/AGY network calls were made; generated artifacts are contained in the output directory.",
        )
    )

    verdict = "PASS" if all(item.status == "verified" for item in evidence) else "FAIL"

    demo_script = (
        dedent(
            f"""
        # 90-second demo script — Prismatic Proof Loop 2

        ## One-line positioning
        Prismatic turns an issue label into governed agent execution with proof, not self-report.

        ## 0–15s — Pain
        "Most agent systems can start work. The hard part is knowing what triggered it, which agent owned it, and whether the result was actually verified."

        ## 15–35s — Trigger
        Show fixture `{fixture.issue_identifier}` with labels: `{", ".join(fixture.labels)}`.
        Explain: `dispatch:ready` means work is eligible; `agent:agy` selects the bounded builder lane; the GitHub fixture branch is `{fixture.github_branch}`.

        ## 35–60s — Governed routing and work
        Run:

        ```bash
        python3 scripts/proof_loop_demo_wedge.py --output-dir artifacts/proof-loop-demo-wedge/latest --clean
        ```

        Point at `demo-evidence.json`: trigger, routing, bounded execution, verification, cleanup.

        ## 60–80s — Proof
        Show verification status: `{verification_status}`.
        The demo does not claim "done" until the verification command passes and the artifact names cleanup status.

        ## 80–90s — Ask
        "If this handled one of your production queues, which approval or verification step would you want visible on your phone first?"
        """
        ).strip()
        + "\n"
    )

    capture_checklist = (
        dedent(
            f"""
        # Demo capture checklist

        - [ ] Show the fixture issue identifier: `{fixture.issue_identifier}`
        - [ ] Show labels: `{", ".join(fixture.labels)}`
        - [ ] Run the demo command from repo root
        - [ ] Show `demo-evidence.json`
        - [ ] Call out routed agent: `{routed_agent}`
        - [ ] Call out verification verdict: `{verification_status}`
        - [ ] Call out cleanup status: `{cleanup_status}`
        - [ ] End with one feedback ask, not a feature tour
        """
        ).strip()
        + "\n"
    )

    feedback_package = (
        dedent(
            """
        # Three-user feedback package

        Message:

        > I’m testing a 90-second Prismatic demo: Linear/GitHub trigger → governed agent route → verified result.
        > Please watch/run the artifact and answer one question: what would make you trust this on a real queue?

        Attach/share:
        - `demo-script.md`
        - `demo-evidence.json`
        - `capture-checklist.md`

        Feedback capture fields:
        1. Did the trigger/routing/result chain make sense in under 90 seconds?
        2. What proof was missing?
        3. Would this be valuable for one real workflow you own? Which one?
        """
        ).strip()
        + "\n"
    )

    evidence_payload = {
        "run_id": run_id,
        "scope": "ad hoc targeted fixture demo; no production Linear/GitHub/AGY calls; not canonical/full-suite green",
        "verdict": verdict,
        "fixture": asdict(fixture),
        "routed_agent": routed_agent,
        "stages": [asdict(item) for item in evidence],
        "artifacts": {
            "fixture": "fixture-event.json",
            "demo_script": "demo-script.md",
            "capture_checklist": "capture-checklist.md",
            "feedback_package": "three-user-feedback-package.md",
            "agent_output": "demo_workspace/agent_output.py",
        },
        "cleanup_status": cleanup_status,
    }

    write_json(output_dir / "fixture-event.json", fixture_payload)
    write_json(output_dir / "demo-evidence.json", evidence_payload)
    write_text(output_dir / "demo-script.md", demo_script)
    write_text(output_dir / "capture-checklist.md", capture_checklist)
    write_text(output_dir / "three-user-feedback-package.md", feedback_package)

    return evidence_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and verify the Proof Loop 2 demo wedge fixture"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR / "latest",
        help="Artifact directory to write (default: artifacts/proof-loop-demo-wedge/latest)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing output directory before writing",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print compact JSON summary only"
    )
    args = parser.parse_args(argv)

    evidence = run_demo(args.output_dir.resolve(), clean=args.clean)
    summary = {
        "verdict": evidence["verdict"],
        "run_id": evidence["run_id"],
        "routed_agent": evidence["routed_agent"],
        "output_dir": str(args.output_dir.resolve()),
        "scope": evidence["scope"],
        "cleanup_status": evidence["cleanup_status"],
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("=" * 72)
        print("Prismatic Proof Loop 2 — Demo Wedge Smoke")
        print("=" * 72)
        print(f"Verdict: {summary['verdict']}")
        print(f"Run ID: {summary['run_id']}")
        print(f"Routed agent: {summary['routed_agent']}")
        print(f"Output dir: {summary['output_dir']}")
        print(f"Scope: {summary['scope']}")
        print(f"Cleanup: {summary['cleanup_status']}")
        print(
            "Artifacts: fixture-event.json, demo-evidence.json, demo-script.md, capture-checklist.md, three-user-feedback-package.md"
        )
        print("JSON_SUMMARY=" + json.dumps(summary, sort_keys=True))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
