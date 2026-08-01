#!/usr/bin/env python3
"""Verify the Prismatic execution evidence contract with fixtures.

This is an ad hoc targeted gate for Proof Loop 3. It validates the canonical
schema, sample statuses, verification-scope labels, failure taxonomy, and the
negative Done-without-proof rule.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from prismatic.execution_evidence import (  # noqa: E402
    CommandEvidence,
    ExecutionEvidence,
    FailureCategory,
    VerificationScope,
    VerificationStatus,
    done_gate,
    validate_evidence,
    write_evidence,
)

DEFAULT_OUTPUT_DIR = REPO / "artifacts" / "execution-evidence-contract"


def sample_evidence() -> dict[str, ExecutionEvidence]:
    return {
        "success": ExecutionEvidence(
            task_id="GRO-3599",
            run_id="evidence-success-001",
            status=VerificationStatus.VERIFIED,
            scope=VerificationScope.AD_HOC_TARGETED,
            summary="Focused smoke passed and artifact was saved.",
            commands=[
                CommandEvidence(
                    command="python3 scripts/proof_loop_demo_wedge.py --clean",
                    exit_code=0,
                    scope=VerificationScope.AD_HOC_TARGETED,
                    output_excerpt="Verdict: PASS",
                )
            ],
            artifacts=["artifacts/proof-loop-demo-wedge/latest/demo-evidence.json"],
            files_changed=["scripts/proof_loop_demo_wedge.py"],
            external_side_effects=["Linear comment posted to GRO-3594"],
            cleanup_status="/tmp verifier removed; demo artifacts retained intentionally",
        ),
        "partial": ExecutionEvidence(
            task_id="GRO-3599",
            run_id="evidence-partial-001",
            status=VerificationStatus.PARTIALLY_VERIFIED,
            scope=VerificationScope.AD_HOC_TARGETED,
            summary="Unit smoke passed, but live webhook was not tested.",
            commands=[
                CommandEvidence(
                    command="python3 scripts/proof_loop_demo_wedge.py --clean",
                    exit_code=0,
                    scope=VerificationScope.AD_HOC_TARGETED,
                    output_excerpt="Verdict: PASS",
                )
            ],
            artifacts=["artifacts/proof-loop-demo-wedge/latest/demo-evidence.json"],
            cleanup_status="local artifacts retained",
            failure_category=FailureCategory.BLOCKED_EXTERNAL_API,
            blocker="Live Linear budget was exhausted; fixture path verified only.",
        ),
        "blocked": ExecutionEvidence(
            task_id="GRO-3599",
            run_id="evidence-blocked-001",
            status=VerificationStatus.BLOCKED,
            scope=VerificationScope.NOT_RUN,
            summary="Verification could not run because GitHub push was rejected by remote unpack failure.",
            cleanup_status="no temp resources created",
            failure_category=FailureCategory.BLOCKED_EXTERNAL_API,
            blocker="GitHub remote returned index-pack failure before PR creation.",
        ),
        "failed": ExecutionEvidence(
            task_id="GRO-3599",
            run_id="evidence-failed-001",
            status=VerificationStatus.FAILED,
            scope=VerificationScope.AD_HOC_TARGETED,
            summary="Fresh install smoke failed because a runtime dependency was missing.",
            commands=[
                CommandEvidence(
                    command="python3 scripts/distribution_readiness_smoke.py --fresh-install",
                    exit_code=1,
                    scope=VerificationScope.AD_HOC_TARGETED,
                    output_excerpt="ModuleNotFoundError: No module named 'packaging'",
                )
            ],
            cleanup_status="fresh install tempdir removed",
            failure_category=FailureCategory.VERIFICATION_FAILED,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify execution evidence contract")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = sample_evidence()
    sample_results: dict[str, dict[str, object]] = {}
    negative_done_gate: dict[str, dict[str, object]] = {}
    results: dict[str, object] = {
        "scope": "ad hoc targeted verification for execution evidence contract; not canonical/full-suite green",
        "samples": sample_results,
        "negative_done_gate": negative_done_gate,
    }

    for name, evidence in samples.items():
        errors = validate_evidence(evidence)
        write_evidence(output_dir / f"{name}.json", evidence)
        sample_results[name] = {
            "status": evidence.status.value,
            "scope": evidence.scope.value,
            "failure_category": evidence.failure_category.value,
            "valid": not errors,
            "errors": errors,
        }

    no_evidence_status, no_evidence_errors = done_gate("Done", None)
    self_reported = ExecutionEvidence(
        task_id="GRO-3599",
        run_id="self-report-001",
        status=VerificationStatus.SELF_REPORTED,
        scope=VerificationScope.NOT_RUN,
        summary="Agent says it is done, but no commands/artifacts were provided.",
        cleanup_status="not_reported",
    )
    self_status, self_errors = done_gate("Done", self_reported)
    verified_status, verified_errors = done_gate("Done", samples["success"])

    negative_done_gate.update(
        {
            "done_without_evidence": {
                "result": no_evidence_status,
                "errors": no_evidence_errors,
            },
            "done_with_self_report": {"result": self_status, "errors": self_errors},
            "done_with_verified_evidence": {
                "result": verified_status,
                "errors": verified_errors,
            },
        }
    )

    sample_errors = [
        name for name, payload in sample_results.items() if not payload["valid"]
    ]
    gate_passed = (
        not sample_errors
        and no_evidence_status == "not_done"
        and self_status == "not_done"
        and verified_status == "done"
    )
    results["verdict"] = "PASS" if gate_passed else "FAIL"
    results["artifact_dir"] = str(output_dir)
    (output_dir / "verification-summary.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
