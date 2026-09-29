#!/usr/bin/env python3
"""Demo: issue a real signed run receipt for a real engine task.

This is the C1 prototype proof. It runs the ACTUAL execution-evidence
fixture verifier (``scripts/verify_execution_evidence_contract.py``),
captures its real exit code and output into ``CommandEvidence``, and
emits a signed run receipt — then does the adversarial twin: an agent
that claims "done" while its check exits 1, whose receipt must read
NOT DONE.

Usage:
    python3 scripts/demo_run_receipt.py [--output-dir DIR]

Writes the two receipts to ``<output-dir>/run-receipts.jsonl`` and prints
both rendered receipts plus signature-verification results.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from prismatic.execution_evidence import (  # noqa: E402
    CommandEvidence,
    ExecutionEvidence,
    FailureCategory,
    VerificationScope,
    VerificationStatus,
)
from prismatic.verification.run_receipt import (  # noqa: E402
    build_run_receipt,
    find_run_receipts,
    persist_run_receipt,
    render_receipt_text,
    sign_run_receipt,
    verify_run_receipt_signature,
)


def _run_check(argv: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=300)
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output[-2000:]


def _ephemeral_keypair():
    private = Ed25519PrivateKey.generate()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private, public_pem


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    out_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="run-receipt-demo-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run-receipts.jsonl"
    evidence_dir = out_dir / "evidence-fixtures"
    private, public_pem = _ephemeral_keypair()
    started = datetime.now(timezone.utc).isoformat()

    # ── Real run: the evidence-contract fixture verifier ──────────────
    cmd = [
        sys.executable,
        "scripts/verify_execution_evidence_contract.py",
        "--output-dir",
        str(evidence_dir),
        "--clean",
    ]
    code, output = _run_check(cmd, REPO_ROOT)
    real_evidence = ExecutionEvidence(
        task_id="demo-evidence-contract",
        run_id="demo-run-real",
        status=(
            VerificationStatus.VERIFIED if code == 0 else VerificationStatus.FAILED
        ),
        scope=VerificationScope.AD_HOC_TARGETED,
        summary="execution-evidence fixture verifier (real run)",
        commands=[
            CommandEvidence(
                command=" ".join(cmd),
                exit_code=code,
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt=output,
            )
        ],
        artifacts=[str(evidence_dir / "summary.json")],
        cleanup_status="clean",
        failure_category=(
            FailureCategory.NONE if code == 0 else FailureCategory.VERIFICATION_FAILED
        ),
    )
    real_receipt = build_run_receipt(
        run_id="demo-run-real",
        task_id="demo-evidence-contract",
        agent_name="demo-agent",
        evidence=real_evidence,
        harness="demo-harness",
        model="local",
        started_at=started,
        notes="C1 prototype: real check, real exit code, real signature",
    )
    sign_run_receipt(real_receipt, private_key=private, key_id="demo-key-01")
    persist_run_receipt(real_receipt, log_path=log_path)

    # ── Adversarial run: agent claims done, check exits 1 ─────────────
    bad_code, bad_output = _run_check(
        [sys.executable, "-c", "import sys; print('boom'); sys.exit(1)"],
        REPO_ROOT,
    )
    bad_evidence = ExecutionEvidence(
        task_id="demo-adversarial",
        run_id="demo-run-adversarial",
        status=VerificationStatus.FAILED,
        scope=VerificationScope.AD_HOC_TARGETED,
        summary="agent claimed done; verification check failed",
        commands=[
            CommandEvidence(
                command="python3 -c \"import sys; print('boom'); sys.exit(1)\"",
                exit_code=bad_code,
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt=bad_output,
            )
        ],
        cleanup_status="clean",
        failure_category=FailureCategory.VERIFICATION_FAILED,
    )
    bad_receipt = build_run_receipt(
        run_id="demo-run-adversarial",
        task_id="demo-adversarial",
        agent_name="demo-agent",
        evidence=bad_evidence,
        harness="demo-harness",
        model="local",
        started_at=started,
        notes="C1 prototype adversarial: claimed done, check failed",
    )
    sign_run_receipt(bad_receipt, private_key=private, key_id="demo-key-01")
    persist_run_receipt(bad_receipt, log_path=log_path)

    # ── Showtime ──────────────────────────────────────────────────────
    print("=" * 72)
    print("REAL RUN RECEIPT (evidence-contract fixture verifier, real exit code)")
    print("=" * 72)
    print(render_receipt_text(real_receipt), end="")
    ok, reason = verify_run_receipt_signature(real_receipt, public_pem)
    print(f"signature check: {'VALID' if ok else f'INVALID ({reason})'}")
    print()
    print("=" * 72)
    print("ADVERSARIAL RECEIPT (agent claimed done; check exited 1)")
    print("=" * 72)
    print(render_receipt_text(bad_receipt), end="")
    ok2, reason2 = verify_run_receipt_signature(bad_receipt, public_pem)
    print(f"signature check: {'VALID' if ok2 else f'INVALID ({reason2})'}")
    print()
    # Tamper demo on the persisted copy
    persisted = find_run_receipts(log_path=log_path, limit=10)
    assert len(persisted) == 2, f"expected 2 persisted, got {len(persisted)}"
    tampered = dict(persisted[0])
    tampered["evidence"] = dict(tampered["evidence"])
    tampered["evidence"]["summary"] = "tampered summary"
    ok3, reason3 = verify_run_receipt_signature(tampered, public_pem)
    print(
        f"tamper demo: flipped one byte -> {'VALID (BUG!)' if ok3 else f'INVALID ({reason3}) as required'}"
    )
    print(f"\nreceipts persisted: {log_path}")
    if bad_code != 1:
        print("unexpected: adversarial check did not exit 1", file=sys.stderr)
        return 1
    if real_receipt["done_gate_result"] != ("done" if code == 0 else "not_done"):
        print("unexpected: real receipt gate mismatch", file=sys.stderr)
        return 1
    if bad_receipt["done_gate_result"] != "not_done":
        print("BUG: adversarial receipt passed the done gate", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
