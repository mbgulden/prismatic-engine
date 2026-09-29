"""Execution evidence contract in ~30 seconds.

The engine's rule: an agent saying "done" is not evidence. Every result must
carry machine-checkable evidence, and ``self_reported`` is explicitly rejected
as Done. Run with: ``python3 examples/evidence-contract/run.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prismatic.execution_evidence import (
    CommandEvidence,
    ExecutionEvidence,
    VerificationScope,
    VerificationStatus,
    done_gate,
    validate_evidence,
)


def main() -> None:
    # 1. Self-report: an agent claiming done with no proof.
    self_reported = ExecutionEvidence(
        task_id="example-task",
        run_id="run-1",
        status=VerificationStatus.SELF_REPORTED,
        scope=VerificationScope.NOT_RUN,
        summary="Agent says it finished.",
        cleanup_status="cleaned",
    )
    verdict, reasons = done_gate("done", self_reported)
    print("self_reported ->", verdict, "|", reasons[0])
    assert verdict == "not_done", "self-report must never be Done"

    # 2. Verified: the same claim backed by a real command run.
    verified = ExecutionEvidence(
        task_id="example-task",
        run_id="run-2",
        status=VerificationStatus.VERIFIED,
        scope=VerificationScope.CANONICAL_FULL_SUITE,
        summary="Ran the test suite; all green.",
        commands=[
            CommandEvidence(
                command="pytest -q",
                exit_code=0,
                scope=VerificationScope.CANONICAL_FULL_SUITE,
                output_excerpt="42 passed",
            )
        ],
        cleanup_status="cleaned",
    )
    assert validate_evidence(verified) == []
    verdict2, reasons2 = done_gate("done", verified)
    print("verified      ->", verdict2, "|", reasons2)
    assert verdict2 == "done", "command-backed verified evidence must be Done"
    print("EVIDENCE_CONTRACT_EXAMPLE_OK")


if __name__ == "__main__":
    main()
