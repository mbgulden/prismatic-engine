"""Run receipt tests — readable, signed proof-of-done for agent runs.

Covers: receipt shape and done-gate integration, Ed25519 sign/verify round
trip, tamper detection (adversarial), the explicitly-unsigned fail-safe,
``from_run_record`` mapping, durable JSONL persistence, human-readable
rendering, and the adversarial claimed-done-but-check-failed case.
"""

from __future__ import annotations

import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from prismatic.execution_evidence import (
    CommandEvidence,
    ExecutionEvidence,
    FailureCategory,
    VerificationScope,
    VerificationStatus,
)
from prismatic.run_records import AgentRunRecord
from prismatic.verification.run_receipt import (
    RUN_RECEIPT_MARKER,
    build_run_receipt,
    default_run_receipts_path,
    find_run_receipts,
    from_run_record,
    persist_run_receipt,
    render_receipt_text,
    sign_run_receipt,
    verify_run_receipt_signature,
)


def _verified_evidence() -> ExecutionEvidence:
    return ExecutionEvidence(
        task_id="task-1",
        run_id="run-1",
        status=VerificationStatus.VERIFIED,
        scope=VerificationScope.AD_HOC_TARGETED,
        summary="fixture verifier passed",
        commands=[
            CommandEvidence(
                command="python3 scripts/verify_execution_evidence_contract.py",
                exit_code=0,
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt='{"verdict": "PASS"}',
            )
        ],
        artifacts=["artifacts/evidence/latest/summary.json"],
        files_changed=["prismatic/verification/run_receipt.py"],
        cleanup_status="clean",
    )


def _failed_evidence() -> ExecutionEvidence:
    return ExecutionEvidence(
        task_id="task-2",
        run_id="run-2",
        status=VerificationStatus.FAILED,
        scope=VerificationScope.AD_HOC_TARGETED,
        summary="agent claimed done; smoke check failed",
        commands=[
            CommandEvidence(
                command="python3 scripts/smoke_check.py",
                exit_code=1,
                scope=VerificationScope.AD_HOC_TARGETED,
                output_excerpt="AssertionError: widget not rendered",
            )
        ],
        cleanup_status="clean",
        failure_category=FailureCategory.VERIFICATION_FAILED,
    )


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


# ── Shape + done gate ─────────────────────────────────────────────────


def test_build_verified_receipt_is_done():
    receipt = build_run_receipt(
        run_id="run-1",
        task_id="task-1",
        agent_name="worker-1",
        evidence=_verified_evidence(),
        harness="test-harness",
        model="test-model",
    )
    assert receipt["marker"] == RUN_RECEIPT_MARKER
    assert receipt["schema_version"] == "run-receipt/v1"
    assert receipt["receipt_id"]
    assert receipt["done_gate_result"] == "done"
    assert receipt["verification_status"] == "verified"
    assert receipt["signature_or_attestation"] is None  # unsigned until signed


def test_render_shows_done_and_checks():
    receipt = build_run_receipt(
        run_id="run-1",
        task_id="task-1",
        agent_name="worker-1",
        evidence=_verified_evidence(),
        harness="test-harness",
        model="test-model",
        duration_s=12.5,
        cost_usd=0.0231,
    )
    text = render_receipt_text(receipt)
    assert "RECEIPT: DONE" in text
    assert "verified" in text
    assert "verify_execution_evidence_contract.py" in text
    assert "[PASS]" in text
    assert "test-harness" in text and "test-model" in text
    assert "duration 12.5s" in text
    assert "cost $0.0231" in text
    assert "UNSIGNED" in text  # fail-safe wording present pre-signing


# ── Signing ───────────────────────────────────────────────────────────


def test_sign_verify_round_trip():
    receipt = build_run_receipt(
        run_id="run-1",
        task_id="task-1",
        agent_name="worker-1",
        evidence=_verified_evidence(),
    )
    private, public_pem = _ephemeral_keypair()
    sign_run_receipt(receipt, private_key=private, key_id="test-key-01")
    sig = receipt["signature_or_attestation"]
    assert sig["value"]  # non-empty signature
    assert sig["key_id"] == "test-key-01"
    ok, reason = verify_run_receipt_signature(receipt, public_pem)
    assert ok, reason
    text = render_receipt_text(receipt)
    assert "ed25519 signed" in text


def test_tampered_receipt_fails_verification():
    """Adversarial: one flipped byte in the payload must break the signature."""
    receipt = build_run_receipt(
        run_id="run-1",
        task_id="task-1",
        agent_name="worker-1",
        evidence=_verified_evidence(),
    )
    private, public_pem = _ephemeral_keypair()
    sign_run_receipt(receipt, private_key=private)
    # Tamper: rewrite the summary inside the signed evidence payload.
    receipt["evidence"]["summary"] = "tampered: everything passed, trust me"
    ok, reason = verify_run_receipt_signature(receipt, public_pem)
    assert not ok
    assert reason == "signature_mismatch"


def test_unsigned_when_no_key_configured(monkeypatch):
    """Fail-safe, not fail-silent: no key -> explicitly unsigned."""
    monkeypatch.delenv("PRISMATIC_RUN_RECEIPT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("PRISMATIC_RUN_RECEIPT_KEY_FILE", "/nonexistent/key.pem")
    receipt = build_run_receipt(
        run_id="run-1",
        task_id="task-1",
        agent_name="worker-1",
        evidence=_verified_evidence(),
    )
    sign_run_receipt(receipt)
    assert receipt["signature_or_attestation"] is None
    assert any(
        "unsigned: no run-receipt signing key configured" in claim
        for claim in receipt["explicit_non_claims"]
    )
    ok, reason = verify_run_receipt_signature(receipt, _ephemeral_keypair()[1])
    assert not ok and reason == "unsigned_receipt"


# ── Adversarial: claimed done, check failed ───────────────────────────


def test_claimed_done_with_failed_check_is_not_done():
    """The founding-pain case: agent says done, verification says otherwise."""
    receipt = build_run_receipt(
        run_id="run-2",
        task_id="task-2",
        agent_name="worker-2",
        evidence=_failed_evidence(),
    )
    assert receipt["verification_status"] == "failed"
    assert receipt["done_gate_result"] == "not_done"
    text = render_receipt_text(receipt)
    assert "RECEIPT: NOT DONE" in text
    assert "smoke_check.py" in text  # the failed check is named
    assert "exit=1" in text
    assert "verification_failed" in text


# ── Run-record bridge ─────────────────────────────────────────────────


def test_from_run_record_maps_evidence():
    record = AgentRunRecord(
        run_id="run-9",
        issue_id="issue-9",
        agent_name="worker-9",
        status="completed",
        started_at="2026-09-28T22:00:00+00:00",
        completed_at="2026-09-28T22:01:30+00:00",
    )
    receipt = from_run_record(record, harness="agy-cli", model="m1")
    assert receipt["run_id"] == "run-9"
    assert receipt["task_id"] == "issue-9"
    assert receipt["harness"] == "agy-cli"
    # No evidence attached -> self_reported -> not done (contract's own rule)
    assert receipt["verification_status"] == "self_reported"
    assert receipt["done_gate_result"] == "not_done"
    assert receipt["duration_s"] == pytest.approx(90.0)
    text = render_receipt_text(receipt)
    assert "RECEIPT: NOT DONE" in text


# ── Persistence ───────────────────────────────────────────────────────


def test_persist_and_find_round_trip(tmp_path, monkeypatch):
    log = tmp_path / "run-receipts.jsonl"
    monkeypatch.setenv("PRISMATIC_RUN_RECEIPTS", str(log))
    assert default_run_receipts_path() == log
    receipt = build_run_receipt(
        run_id="run-3",
        task_id="task-3",
        agent_name="worker-3",
        evidence=_verified_evidence(),
    )
    receipt_id = persist_run_receipt(receipt)
    assert receipt_id == receipt["receipt_id"]
    found = find_run_receipts(run_id="run-3")
    assert len(found) == 1
    assert found[0]["receipt_id"] == receipt_id
    # marker filter: foreign rows are ignored
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"marker": "SOMETHING_ELSE"}) + "\n")
    assert len(find_run_receipts()) == 1
    # missing log -> [] (fail-open for the dashboard probe)
    assert find_run_receipts(log_path=tmp_path / "nope.jsonl") == []


def test_default_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_RUN_RECEIPTS", str(tmp_path / "x.jsonl"))
    assert default_run_receipts_path() == tmp_path / "x.jsonl"
    monkeypatch.delenv("PRISMATIC_RUN_RECEIPTS")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    assert default_run_receipts_path() == tmp_path / "run-receipts.jsonl"
    # os import used (keeps linters honest about the env plumbing)
    assert os.environ["PRISMATIC_STATE_DIR"] == str(tmp_path)
