"""Signed merge receipts — proof on every merge.

ADR-0002 makes provider-neutral *verification* receipts the merge-evidence
authority. This module closes the other half of the loop: after the
:class:`~prismatic.review_factory.merge_executor.MergeExecutor` performs a
merge, it emits a signed *merge* receipt that durably binds

    merge_sha  ->  candidate_sha/tree  ->  verification receipt id(s)
    ->  actor + authorization  ->  manifest digest

so the earned-autonomy ``receipt_emitted`` condition (T1 deterministic entry,
``spec/autonomy_tiers_v2.yaml``) has queryable evidence instead of a claim.

A merge receipt is NOT a verification receipt: it does not re-verify the
candidate and it never goes through
:func:`~prismatic.verification.receipt_store.persist_verification_receipt`
(the native bindings there require a clean checkout at the candidate, which
cannot hold after the merge). It is a signed record of the merge *decision*,
referencing the verification receipts the executor validated in
``_build_ci_checks``.

Ed25519 signing reuses :func:`canonicalize_receipt` from the attestation
module, so a merge receipt verifies with the same tooling. The private key
comes from ``PRISMATIC_MERGE_RECEIPT_SIGNING_KEY`` (PEM text) or
``~/.prismatic/keys/merge-receipt-ed25519.pem`` — never hardcoded. When no
key is configured the receipt is recorded *explicitly unsigned* (fail-safe,
not fail-silent): the ``explicit_non_claims`` entry says so.

``persist_merge_receipt`` never raises: the merge already happened, and a
receipt failure must not rewrite the merge result (same contract as the
trust-ledger outcome recording).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from prismatic.verification.attestation import canonicalize_receipt

logger = logging.getLogger(__name__)

MERGE_RECEIPT_MARKER = "PRISMATIC_MERGE_RECEIPT_OK"
MERGE_RECEIPT_SCHEMA_VERSION = "merge-receipt/v1"
MERGE_EXECUTOR_VERIFIER_ID = "rf-merge-executor"

RECEIPTS_FILENAME = "merge-receipts.jsonl"
SIGNING_KEY_ENV = "PRISMATIC_MERGE_RECEIPT_SIGNING_KEY"
SIGNING_KEY_FILE = Path.home() / ".prismatic" / "keys" / "merge-receipt-ed25519.pem"
DEFAULT_KEY_ID = "merge-receipt-key-01"


def default_merge_receipts_path() -> Path:
    """Resolve the merge-receipt log path.

    ``$PRISMATIC_MERGE_RECEIPTS`` when set (tests), else
    ``$PRISMATIC_STATE_DIR/merge-receipts.jsonl`` when set, else
    ``~/.prismatic/merge-receipts.jsonl``.
    """
    override = os.environ.get("PRISMATIC_MERGE_RECEIPTS")
    if override:
        return Path(override)
    state = os.environ.get("PRISMATIC_STATE_DIR")
    if state:
        return Path(state) / RECEIPTS_FILENAME
    return Path.home() / ".prismatic" / RECEIPTS_FILENAME


def _load_signing_key() -> tuple[Any | None, str]:
    """Load the Ed25519 private key for merge-receipt signing.

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
                    "PRISMATIC_MERGE_RECEIPT_KEY_ID", DEFAULT_KEY_ID
                )
        except Exception:
            logger.warning("merge receipt signing key from env is unusable")
            return None, DEFAULT_KEY_ID
    key_file = Path(
        os.environ.get(
            "PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(SIGNING_KEY_FILE)
        )
    )
    if key_file.exists():
        try:
            key = load_pem_private_key(
                key_file.read_bytes(), password=None
            )
            if isinstance(key, Ed25519PrivateKey):
                return key, os.environ.get(
                    "PRISMATIC_MERGE_RECEIPT_KEY_ID", DEFAULT_KEY_ID
                )
        except Exception:
            logger.warning("merge receipt signing key file is unusable")
    return None, DEFAULT_KEY_ID


def build_merge_receipt(
    *,
    repository: str,
    candidate_sha: str,
    candidate_tree: str,
    base_sha: str,
    merge_sha: str,
    actor: str,
    authorization_id: str,
    job_id: str,
    task_id: str = "",
    manifest_digest: str = "",
    policy_version: str = "",
    change_class: str = "",
    verified_receipt_refs: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Build one merge receipt (unsigned until :func:`sign_merge_receipt`)."""
    return {
        "marker": MERGE_RECEIPT_MARKER,
        "schema_version": MERGE_RECEIPT_SCHEMA_VERSION,
        "receipt_id": str(uuid.uuid4()),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "job_id": job_id,
        "task_id": task_id,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "base_sha": base_sha,
        "merge_sha": merge_sha,
        "actor": actor,
        "authorization_id": authorization_id,
        "manifest_digest": manifest_digest,
        "policy_version": policy_version,
        "change_class": change_class,
        "verifier_id": MERGE_EXECUTOR_VERIFIER_ID,
        "verified_receipt_refs": verified_receipt_refs or [],
        "explicit_non_claims": [
            "merge receipt records the merge decision, not a re-verification",
            "verification evidence lives in the referenced verification receipts",
        ],
        "signature_or_attestation": None,
    }


def sign_merge_receipt(
    receipt: dict[str, Any],
    *,
    private_key: Any | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
    """Ed25519-sign a merge receipt in place; returns the receipt.

    When no key is available the receipt is left explicitly unsigned and an
    ``unsigned: ...`` entry is appended to ``explicit_non_claims`` —
    fail-safe, not fail-silent.
    """
    key, default_key_id = _load_signing_key() if private_key is None else (
        private_key,
        key_id or DEFAULT_KEY_ID,
    )
    kid = key_id or default_key_id
    if key is None:
        receipt["signature_or_attestation"] = None
        non_claims = receipt.setdefault("explicit_non_claims", [])
        non_claims.append("unsigned: no merge-receipt signing key configured")
        return receipt
    # Per the canonicalize_receipt contract, the attestation *metadata* is
    # signed (only "value" is excluded): stage the metadata with an empty
    # value, sign the canonical bytes, then fill the value in.
    receipt["signature_or_attestation"] = {
        "type": "attestation",
        "algorithm": "ed25519",
        "key_id": kid,
        "issuer": MERGE_EXECUTOR_VERIFIER_ID,
        "value": "",
    }
    signature = key.sign(canonicalize_receipt(receipt))
    receipt["signature_or_attestation"]["value"] = base64.b64encode(
        signature
    ).decode("ascii")
    return receipt


def persist_merge_receipt(
    receipt: dict[str, Any], *, log_path: Path | str | None = None
) -> str | None:
    """Append a merge receipt to the durable JSONL log. Never raises.

    Returns the ``receipt_id`` on success, ``None`` on failure.
    """
    receipt_id = str(receipt.get("receipt_id") or "")
    try:
        path = Path(log_path) if log_path is not None else default_merge_receipts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(receipt, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        return receipt_id or None
    except Exception:
        logger.warning("persist_merge_receipt failed", exc_info=True)
        return None


def find_merge_receipts(
    *,
    merge_sha: str | None = None,
    candidate_sha: str | None = None,
    log_path: Path | str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read back merge receipts (evidence collector for ``receipt_emitted``)."""
    path = Path(log_path) if log_path is not None else default_merge_receipts_path()
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
                if merge_sha and row.get("merge_sha") != merge_sha:
                    continue
                if candidate_sha and row.get("candidate_sha") != candidate_sha:
                    continue
                matches.append(row)
                if len(matches) >= limit:
                    break
    except OSError:
        return []
    return matches


__all__ = [
    "MERGE_RECEIPT_MARKER",
    "MERGE_RECEIPT_SCHEMA_VERSION",
    "MERGE_EXECUTOR_VERIFIER_ID",
    "build_merge_receipt",
    "sign_merge_receipt",
    "persist_merge_receipt",
    "find_merge_receipts",
    "default_merge_receipts_path",
]
