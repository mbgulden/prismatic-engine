"""Provider-neutral Ed25519 receipt-attestation verification and canonicalization.

GRO-4209 / PNV-5 Verifier identity, Ed25519 attestation, and trust rotation.
"""

from __future__ import annotations

import base64
import copy
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from .receipt_validator import _parse_utc_timestamp_ns


def canonicalize_receipt(receipt: dict[str, Any]) -> bytes:
    """Return deterministic UTF-8 JSON bytes of receipt excluding signature_or_attestation.value.

    Contract:
    1. Deep-copies input dictionary to guarantee caller input is never mutated.
    2. If receipt contains 'signature_or_attestation' (as a dict), excludes only the 'value' key.
       Attestation metadata ('type', 'algorithm', 'key_id', 'issuer', etc.) remains intact and signed.
    3. Serializes to UTF-8 JSON with sorted keys (sort_keys=True), compact separators (',', ':'),
       ensure_ascii=False (preserving UTF-8 characters), and allow_nan=False.
    """
    if not isinstance(receipt, dict):
        raise TypeError("receipt must be a dictionary")
    cleaned = copy.deepcopy(receipt)
    sig = cleaned.get("signature_or_attestation")
    if isinstance(sig, dict):
        sig.pop("value", None)
    return json.dumps(
        cleaned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _decode_strict_base64_signature(val: str) -> bytes:
    if not isinstance(val, str) or not val.strip():
        raise ValueError("empty_attestation_value")
    # Standard Ed25519 signature is 64 raw bytes -> 88 base64 characters
    try:
        decoded = base64.b64decode(val, validate=True)
    except Exception:
        raise ValueError("malformed_base64_signature")
    if len(decoded) != 64:
        raise ValueError("invalid_signature_length")
    # Strict canonical base64 check: re-encoded base64 must match input string exactly
    if base64.b64encode(decoded).decode("ascii") != val:
        raise ValueError("malformed_base64_signature")
    return decoded


def verify_receipt_attestation(
    receipt: dict[str, Any], policy: dict[str, Any]
) -> tuple[bool, str | None]:
    """Verify receipt attestation against policy verifier key records.

    Fail-closed, non-raising. Returns (is_valid, reason_if_invalid).
    """
    try:
        if not isinstance(receipt, dict):
            return False, "invalid_receipt"
        if not isinstance(policy, dict):
            return False, "invalid_policy"

        att_policy = policy.get("attestation") or {}
        att_required = att_policy.get("required", False)

        sig = receipt.get("signature_or_attestation")
        if not isinstance(sig, dict):
            if att_required:
                return False, "missing_attestation"
            return True, None

        att_type = sig.get("type")
        if att_type != "attestation":
            return False, "unsupported_attestation_type"

        sig_val = sig.get("value")
        if not sig_val or not isinstance(sig_val, str) or not sig_val.strip():
            return False, "empty_attestation_value"

        algo = sig.get("algorithm")
        if not algo or not isinstance(algo, str):
            return False, "missing_attestation_algorithm"

        allowed_algos = att_policy.get("allowed_algorithms", [])
        if algo not in allowed_algos:
            return False, "unapproved_attestation_algorithm"

        if algo != "ed25519":
            return False, "unsupported_attestation_algorithm"

        key_id = sig.get("key_id")
        if not key_id or not isinstance(key_id, str):
            return False, "missing_attestation_key_id"

        allowed_key_ids = att_policy.get("allowed_key_ids", [])
        if key_id not in allowed_key_ids:
            return False, "unapproved_attestation_key_id"

        # Check strict Base64 signature decoding
        try:
            sig_bytes = _decode_strict_base64_signature(sig_val)
        except ValueError as err:
            return False, str(err)

        # Verifier identity & key records lookup
        r_verifier_id = receipt.get("verifier_id")
        if not r_verifier_id or not isinstance(r_verifier_id, str):
            return False, "missing_verifier_id"

        approved_verifiers = policy.get("approved_verifiers") or {}
        identities = approved_verifiers.get("identities")
        if not isinstance(identities, list):
            return False, "invalid_policy_identities"

        # Reject duplicate key_id records across all key records in identities
        seen_key_ids: set[str] = set()
        for rec in identities:
            if isinstance(rec, dict):
                k_id = rec.get("key_id")
                if isinstance(k_id, str) and k_id:
                    if k_id in seen_key_ids:
                        return False, "duplicate_key_id_records"
                    seen_key_ids.add(k_id)

        # Filter records for verifier_id
        verifier_records = [
            rec
            for rec in identities
            if isinstance(rec, dict) and rec.get("id") == r_verifier_id
        ]
        if not verifier_records:
            return False, "no_key_for_approved_verifier"

        # Find matching key record for key_id
        matching_key_records = [
            rec for rec in verifier_records if rec.get("key_id") == key_id
        ]
        if not matching_key_records:
            return False, "unknown_key_id"

        if len(matching_key_records) > 1:
            return False, "duplicate_key_id_records"

        key_record = matching_key_records[0]

        # Key algorithm check
        if key_record.get("algorithm") != "ed25519":
            return False, "unsupported_key_algorithm"

        # Key revocation check
        if key_record.get("revoked_at") is not None:
            return False, "key_revoked"

        # Timestamp checks: receipt completed_at vs key created_at / expires_at
        receipt_completed_str = receipt.get("completed_at") or receipt.get(
            "finished_at"
        )
        if not receipt_completed_str or not isinstance(receipt_completed_str, str):
            return False, "missing_timestamp"

        receipt_completed_ns = _parse_utc_timestamp_ns(receipt_completed_str)
        if receipt_completed_ns is None:
            return False, "malformed_timestamp"

        key_created_str = key_record.get("created_at")
        if not key_created_str or not isinstance(key_created_str, str):
            return False, "malformed_key_created_at"

        key_created_ns = _parse_utc_timestamp_ns(key_created_str)
        if key_created_ns is None:
            return False, "malformed_key_created_at"

        # Active check: key created_at <= receipt.completed_at
        if receipt_completed_ns < key_created_ns:
            return False, "key_not_yet_active"

        # Expired check: receipt.completed_at >= key.expires_at
        key_expires_str = key_record.get("expires_at")
        if key_expires_str is not None:
            if not isinstance(key_expires_str, str):
                return False, "malformed_key_expires_at"
            key_expires_ns = _parse_utc_timestamp_ns(key_expires_str)
            if key_expires_ns is None:
                return False, "malformed_key_expires_at"
            if receipt_completed_ns >= key_expires_ns:
                return False, "key_expired"

        # Fresh key check: require_fresh_key and max_key_age_seconds
        if att_policy.get("require_fresh_key", False):
            max_key_age = att_policy.get("max_key_age_seconds")
            if (
                max_key_age is None
                or isinstance(max_key_age, bool)
                or not isinstance(max_key_age, int)
                or max_key_age <= 0
            ):
                return False, "invalid_max_key_age_seconds"
            key_age_ns = receipt_completed_ns - key_created_ns
            if key_age_ns > max_key_age * 1_000_000_000:
                return False, "key_exceeds_max_age"

        # PEM public key parsing
        pem_str = key_record.get("public_key_pem")
        if not pem_str or not isinstance(pem_str, str):
            return False, "malformed_public_key_pem"

        try:
            pub_key = load_pem_public_key(pem_str.encode("utf-8"))
        except Exception:
            return False, "malformed_public_key_pem"

        if not isinstance(pub_key, Ed25519PublicKey):
            return False, "unsupported_public_key_type"

        # Verify signature over canonical receipt payload
        canonical_bytes = canonicalize_receipt(receipt)
        try:
            pub_key.verify(sig_bytes, canonical_bytes)
        except InvalidSignature:
            return False, "signature_mismatch"
        except Exception:
            return False, "signature_verification_error"

        return True, None

    except Exception as exc:
        return False, f"attestation_verification_error: {exc}"
