"""Signed run receipts — readable proof-of-done for every agent run.

A run receipt is the user-visible twin of the machine-checked execution
evidence contract (:mod:`prismatic.execution_evidence`). Where the contract
answers "is this Done?" for machinery, the receipt answers it for a human:

    what the run did · what it verified (exact checks, with results) ·
    what it could NOT verify · the verdict · who/what ran it · signature

Design notes:

- This module is a rendering + signing + persistence layer OVER
  :class:`~prismatic.execution_evidence.ExecutionEvidence`. It introduces no
  new evidence schema and does not change ``done_gate`` semantics.
- The build/sign/persist/find shape mirrors
  :mod:`prismatic.verification.merge_receipt` deliberately: one attestation
  story (Ed25519 over :func:`canonicalize_receipt`), one explicitly-unsigned
  fail-safe when no key is configured, one JSONL log pattern.
- Harness-neutral by construction: the builder consumes ``ExecutionEvidence``
  plus plain metadata (agent name, harness id, model, timestamps, cost).
  Nothing here imports from any harness.
- ``persist_run_receipt`` never raises: the run already happened, and a
  receipt failure must not rewrite the run result (same contract as merge
  receipts and the trust-ledger outcome recording).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.execution_evidence import (
    ExecutionEvidence,
    done_gate,
    validate_evidence,
)
from prismatic.run_records import AgentRunRecord
from prismatic.verification.attestation import canonicalize_receipt

logger = logging.getLogger(__name__)

RUN_RECEIPT_MARKER = "PRISMATIC_RUN_RECEIPT_OK"
RUN_RECEIPT_SCHEMA_VERSION = "run-receipt/v1"
RUN_RECEIPT_VERIFIER_ID = "prismatic-run-receipt"

RECEIPTS_FILENAME = "run-receipts.jsonl"
SIGNING_KEY_ENV = "PRISMATIC_RUN_RECEIPT_SIGNING_KEY"
SIGNING_KEY_FILE_ENV = "PRISMATIC_RUN_RECEIPT_KEY_FILE"
SIGNING_KEY_FILE = Path.home() / ".prismatic" / "keys" / "run-receipt-ed25519.pem"
PUBLIC_KEY_FILE_ENV = "PRISMATIC_RUN_RECEIPT_PUBLIC_KEY_FILE"
PUBLIC_KEY_FILE = Path.home() / ".prismatic" / "keys" / "run-receipt-ed25519.pub"
DEFAULT_KEY_ID = "run-receipt-key-01"


def default_run_receipts_path() -> Path:
    """Resolve the run-receipt log path.

    ``$PRISMATIC_RUN_RECEIPTS`` when set (tests), else
    ``$PRISMATIC_STATE_DIR/run-receipts.jsonl`` when set, else
    ``~/.prismatic/run-receipts.jsonl``.
    """
    override = os.environ.get("PRISMATIC_RUN_RECEIPTS")
    if override:
        return Path(override)
    state = os.environ.get("PRISMATIC_STATE_DIR")
    if state:
        return Path(state) / RECEIPTS_FILENAME
    return Path.home() / ".prismatic" / RECEIPTS_FILENAME


def _load_signing_key() -> tuple[Any | None, str]:
    """Load the Ed25519 private key for run-receipt signing.

    Returns ``(private_key_or_None, key_id)``. No key configured is not an
    error — the receipt is recorded explicitly unsigned.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError:
        return None, DEFAULT_KEY_ID
    pem_text = os.environ.get(SIGNING_KEY_ENV)
    if pem_text:
        try:
            key = load_pem_private_key(pem_text.encode("utf-8"), password=None)
            if isinstance(key, Ed25519PrivateKey):
                return key, os.environ.get(
                    "PRISMATIC_RUN_RECEIPT_KEY_ID", DEFAULT_KEY_ID
                )
        except Exception:
            logger.warning("run receipt signing key from env is unusable")
            return None, DEFAULT_KEY_ID
    key_file = Path(os.environ.get(SIGNING_KEY_FILE_ENV, str(SIGNING_KEY_FILE)))
    if key_file.exists():
        try:
            key = load_pem_private_key(key_file.read_bytes(), password=None)
            if isinstance(key, Ed25519PrivateKey):
                return key, os.environ.get(
                    "PRISMATIC_RUN_RECEIPT_KEY_ID", DEFAULT_KEY_ID
                )
        except Exception:
            logger.warning("run receipt signing key file is unusable")
    return None, DEFAULT_KEY_ID


def build_run_receipt(
    *,
    run_id: str,
    task_id: str,
    agent_name: str,
    evidence: ExecutionEvidence | dict[str, Any],
    harness: str = "",
    model: str = "",
    started_at: str = "",
    completed_at: str = "",
    duration_s: float | None = None,
    cost_usd: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Build one run receipt (unsigned until :func:`sign_run_receipt`).

    ``evidence`` is canonical :class:`ExecutionEvidence` (or its dict form).
    The receipt computes the evidence-contract ``done_gate`` verdict and
    carries the full evidence payload so the receipt is self-contained.
    """
    parsed = (
        evidence
        if isinstance(evidence, ExecutionEvidence)
        else ExecutionEvidence.from_dict(evidence)
    )
    evidence_errors = validate_evidence(parsed)
    gate_result, gate_errors = done_gate("done", parsed)
    done_errors = list(dict.fromkeys(evidence_errors + gate_errors))
    now = datetime.now(timezone.utc).isoformat()
    return {
        "marker": RUN_RECEIPT_MARKER,
        "schema_version": RUN_RECEIPT_SCHEMA_VERSION,
        "receipt_id": str(uuid.uuid4()),
        "emitted_at": now,
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": agent_name,
        "harness": harness,
        "model": model,
        "started_at": started_at,
        "completed_at": completed_at or now,
        "duration_s": duration_s,
        "cost_usd": cost_usd,
        "notes": notes,
        "verifier_id": RUN_RECEIPT_VERIFIER_ID,
        "evidence": parsed.to_dict(),
        "verification_status": parsed.status.value,
        "verification_scope": parsed.scope.value,
        "failure_category": parsed.failure_category.value,
        "done_gate_result": gate_result,
        "done_gate_errors": done_errors,
        "explicit_non_claims": [
            "run receipt records the run's evidence, not a re-verification",
            "verification evidence lives in the embedded evidence payload",
            "unsigned receipts are explicitly marked; absence of a signature "
            "is a statement, not an omission",
        ],
        "signature_or_attestation": None,
    }


def from_run_record(
    record: AgentRunRecord,
    *,
    harness: str = "",
    model: str = "",
    cost_usd: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Build an unsigned run receipt from an :class:`AgentRunRecord`.

    The record must carry attached evidence; a record without evidence
    produces a ``self_reported`` receipt whose done gate is ``not_done``
    (the contract's own rule, surfaced readably).
    """
    evidence_payload: dict[str, Any] | None = record.evidence
    if evidence_payload is None:
        evidence_payload = {
            "task_id": record.issue_id,
            "run_id": record.run_id,
            "status": "self_reported",
            "scope": "not_run",
            "summary": record.error_message
            or "run completed without attached execution evidence",
            "cleanup_status": record.cleanup_status,
        }
    duration_s: float | None = None
    try:
        if record.started_at and record.completed_at:
            start = datetime.fromisoformat(record.started_at)
            end = datetime.fromisoformat(record.completed_at)
            duration_s = max(0.0, (end - start).total_seconds())
    except (ValueError, TypeError):
        duration_s = None
    return build_run_receipt(
        run_id=record.run_id,
        task_id=record.issue_id,
        agent_name=record.agent_name,
        evidence=evidence_payload,
        harness=harness,
        model=model,
        started_at=record.started_at,
        completed_at=record.completed_at or "",
        duration_s=duration_s,
        cost_usd=cost_usd,
        notes=notes,
    )


def sign_run_receipt(
    receipt: dict[str, Any],
    *,
    private_key: Any | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
    """Ed25519-sign a run receipt in place; returns the receipt.

    When no key is available the receipt is left explicitly unsigned and an
    ``unsigned: ...`` entry is appended to ``explicit_non_claims`` —
    fail-safe, not fail-silent (same contract as merge receipts).
    """
    key, default_key_id = (
        _load_signing_key()
        if private_key is None
        else (private_key, key_id or DEFAULT_KEY_ID)
    )
    kid = key_id or default_key_id
    if key is None:
        receipt["signature_or_attestation"] = None
        non_claims = receipt.setdefault("explicit_non_claims", [])
        non_claims.append("unsigned: no run-receipt signing key configured")
        return receipt
    # Per the canonicalize_receipt contract, the attestation *metadata* is
    # signed (only "value" is excluded): stage the metadata with an empty
    # value, sign the canonical bytes, then fill the value in.
    receipt["signature_or_attestation"] = {
        "type": "attestation",
        "algorithm": "ed25519",
        "key_id": kid,
        "issuer": RUN_RECEIPT_VERIFIER_ID,
        "value": "",
    }
    signature = key.sign(canonicalize_receipt(receipt))
    receipt["signature_or_attestation"]["value"] = base64.b64encode(signature).decode(
        "ascii"
    )
    return receipt


def verify_run_receipt_signature(
    receipt: dict[str, Any], public_key_pem: str
) -> tuple[bool, str | None]:
    """Verify a run receipt's Ed25519 signature. Fail-closed, non-raising.

    Returns ``(True, None)`` on a valid signature, ``(False, reason)`` on
    any problem — including unsigned receipts and tampered payloads.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError:
        return False, "cryptography_unavailable"
    try:
        if not isinstance(receipt, dict):
            return False, "invalid_receipt"
        sig = receipt.get("signature_or_attestation")
        if not isinstance(sig, dict):
            return False, "unsigned_receipt"
        value = sig.get("value")
        if not value or not isinstance(value, str):
            return False, "unsigned_receipt"
        if sig.get("algorithm") != "ed25519":
            return False, "unsupported_algorithm"
        try:
            signature = base64.b64decode(value, validate=True)
        except Exception:
            return False, "malformed_signature"
        if len(signature) != 64:
            return False, "invalid_signature_length"
        try:
            public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
        except Exception:
            return False, "malformed_public_key"
        if not isinstance(public_key, Ed25519PublicKey):
            return False, "unsupported_public_key_type"
        public_key.verify(signature, canonicalize_receipt(receipt))
        return True, None
    except InvalidSignature:
        return False, "signature_mismatch"
    except Exception as exc:
        return False, f"verification_error: {exc}"


def persist_run_receipt(
    receipt: dict[str, Any], *, log_path: Path | str | None = None
) -> str | None:
    """Append a run receipt to the durable JSONL log. Never raises.

    Returns the ``receipt_id`` on success, ``None`` on failure.
    """
    receipt_id = str(receipt.get("receipt_id") or "")
    try:
        path = Path(log_path) if log_path is not None else default_run_receipts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        return receipt_id or None
    except Exception:
        logger.warning("persist_run_receipt failed", exc_info=True)
        return None


def find_run_receipts(
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    log_path: Path | str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read back run receipts (newest last). Non-raising; missing log -> []."""
    path = Path(log_path) if log_path is not None else default_run_receipts_path()
    if not path.exists():
        return []
    matches: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("marker") != RUN_RECEIPT_MARKER:
                    continue
                if run_id and row.get("run_id") != run_id:
                    continue
                if task_id and row.get("task_id") != task_id:
                    continue
                matches.append(row)
                if len(matches) >= limit:
                    break
    except OSError:
        return []
    return matches


def render_receipt_text(receipt: dict[str, Any]) -> str:
    """Render a run receipt as human-readable text.

    This is the product surface: the thing a user reads instead of a JSON
    blob. Verdict banner first, then checks, then what was NOT verified,
    then attribution and signature status.
    """
    done = receipt.get("done_gate_result") == "done"
    banner = "RECEIPT: DONE ✔" if done else "RECEIPT: NOT DONE ✘"
    ev = receipt.get("evidence") or {}
    lines = [
        f"{banner}  [{receipt.get('verification_status', '?')}"
        f" / {receipt.get('verification_scope', '?')}]",
        f"run: {receipt.get('run_id', '—')}  task: {receipt.get('task_id', '—')}",
        f"agent: {receipt.get('agent_name', '—')}"
        + (f"  harness: {receipt.get('harness')}" if receipt.get("harness") else "")
        + (f"  model: {receipt.get('model')}" if receipt.get("model") else ""),
        "",
        "Summary:",
        f"  {ev.get('summary', '—')}",
        "",
    ]
    if receipt.get("failure_category") not in (None, "none"):
        lines.append(f"Failure category: {receipt['failure_category']}")
        lines.append("")
    lines.append("Checks:")
    commands = ev.get("commands") or []
    if commands:
        for cmd in commands:
            code = cmd.get("exit_code")
            mark = "PASS" if code == 0 else f"exit={code}"
            lines.append(
                f"  [{mark}] ({cmd.get('scope', '?')}) {cmd.get('command', '?')}"
            )
            excerpt = (cmd.get("output_excerpt") or "").strip()
            if excerpt:
                first = excerpt.splitlines()[0][:160]
                lines.append(f"         → {first}")
    else:
        lines.append("  (no verification commands recorded)")
    artifacts = ev.get("artifacts") or []
    if artifacts:
        lines.append("Artifacts:")
        for art in artifacts:
            lines.append(f"  • {art}")
    files_changed = ev.get("files_changed") or []
    if files_changed:
        lines.append(f"Files changed: {len(files_changed)}")
        for path in files_changed[:10]:
            lines.append(f"  • {path}")
        if len(files_changed) > 10:
            lines.append(f"  … and {len(files_changed) - 10} more")
    side_effects = ev.get("external_side_effects") or []
    if side_effects:
        lines.append("External side effects:")
        for effect in side_effects:
            lines.append(f"  • {effect}")
    blocker = ev.get("blocker") or ""
    errors = receipt.get("done_gate_errors") or []
    if blocker or errors or not done:
        lines.append("")
        lines.append("Not verified / blockers:")
        if blocker:
            lines.append(f"  blocker: {blocker}")
        for err in errors:
            lines.append(f"  • {err}")
        if not blocker and not errors:
            lines.append("  (no passing verification evidence)")
    lines.append("")
    timing_bits = []
    if receipt.get("started_at"):
        timing_bits.append(f"started {receipt['started_at']}")
    if receipt.get("completed_at"):
        timing_bits.append(f"completed {receipt['completed_at']}")
    if receipt.get("duration_s") is not None:
        timing_bits.append(f"duration {receipt['duration_s']:.1f}s")
    if receipt.get("cost_usd") is not None:
        timing_bits.append(f"cost ${receipt['cost_usd']:.4f}")
    if timing_bits:
        lines.append("Attribution: " + " · ".join(timing_bits))
    sig = receipt.get("signature_or_attestation")
    if isinstance(sig, dict) and sig.get("value"):
        lines.append(
            f"Signature: ed25519 signed (key {sig.get('key_id', '?')}) — tamper-evident"
        )
    else:
        lines.append(
            "Signature: UNSIGNED — no signing key configured (treat as informational)"
        )
    lines.append(f"receipt_id: {receipt.get('receipt_id', '—')}")
    return "\n".join(lines) + "\n"


__all__ = [
    "RUN_RECEIPT_MARKER",
    "RUN_RECEIPT_SCHEMA_VERSION",
    "RUN_RECEIPT_VERIFIER_ID",
    "build_run_receipt",
    "from_run_record",
    "sign_run_receipt",
    "verify_run_receipt_signature",
    "persist_run_receipt",
    "find_run_receipts",
    "default_run_receipts_path",
    "render_receipt_text",
]
