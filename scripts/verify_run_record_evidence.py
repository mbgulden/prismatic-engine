#!/usr/bin/env python3
"""Verify run records surface canonical execution evidence.

Ad hoc targeted gate for Proof Loop 3 follow-up. It proves completed runs cannot
silently appear Done without evidence, and that reports/API payloads expose the
verification fields operators need.
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
    VerificationScope,
    VerificationStatus,
)
from prismatic.run_records import AgentRunRecordStore  # noqa: E402

DEFAULT_OUTPUT_DIR = REPO / "artifacts" / "run-record-evidence"


def build_verified_evidence(run_id: str) -> ExecutionEvidence:
    return ExecutionEvidence(
        task_id="GRO-3599",
        run_id=run_id,
        status=VerificationStatus.VERIFIED,
        scope=VerificationScope.AD_HOC_TARGETED,
        summary="Run record evidence smoke passed.",
        commands=[
            CommandEvidence(
                command="python3 scripts/verify_run_record_evidence.py --clean",
                exit_code=0,
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt="verdict=PASS",
            )
        ],
        artifacts=["artifacts/run-record-evidence/latest/summary.json"],
        files_changed=["prismatic/run_records.py", "prismatic/gateway/server.py"],
        external_side_effects=[],
        cleanup_status="temp store removed; artifact retained intentionally",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify run-record evidence surfacing")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    store_path = output_dir / "tmp_state" / "run_records.json"
    store = AgentRunRecordStore(str(store_path))

    no_evidence_run = store.create_run("GRO-3599", "agy")
    store.update_run(no_evidence_run, status="completed")
    no_evidence = store.get_run(no_evidence_run)
    assert no_evidence is not None

    verified_run = store.create_run("GRO-3599", "agy")
    evidence = build_verified_evidence(verified_run)
    store.update_run(verified_run, status="completed", evidence=evidence)
    verified = store.get_run(verified_run)
    assert verified is not None

    report = store.generate_report("GRO-3599")
    report_path = output_dir / "run-report.md"
    report_path.write_text(report, encoding="utf-8")

    # Import locally after records exist so this remains a lightweight API-shape check.
    from prismatic.gateway.server import _run_record_to_dict  # noqa: PLC0415

    no_evidence_payload = _run_record_to_dict(no_evidence)
    verified_payload = _run_record_to_dict(verified)

    checks = {
        "completed_without_evidence_not_done": no_evidence.done_gate_result
        == "not_done",
        "completed_without_evidence_self_reported": no_evidence.verification_status
        == "self_reported",
        "verified_evidence_done": verified.done_gate_result == "done",
        "verified_status_visible": verified.verification_status == "verified",
        "report_shows_verification": "**Verification:** verified" in report,
        "report_shows_not_done_error": "Done requires execution evidence" in report,
        "api_payload_shows_done_gate": verified_payload["done_gate_result"] == "done",
        "api_payload_shows_scope": verified_payload["verification_scope"]
        == "ad_hoc_targeted",
        "api_payload_rejects_missing_evidence": no_evidence_payload["done_gate_result"]
        == "not_done",
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    summary = {
        "scope": "ad hoc targeted verification for run-record evidence surfacing; not canonical/full-suite green",
        "verdict": verdict,
        "checks": checks,
        "no_evidence_payload": no_evidence_payload,
        "verified_payload": verified_payload,
        "report_path": str(report_path),
        "store_path": str(store_path),
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(store_path.parent, ignore_errors=True)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
