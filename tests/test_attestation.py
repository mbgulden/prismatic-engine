"""Tests for Ed25519 receipt-attestation verification, canonicalization, and trust rotation (GRO-4209)."""

from __future__ import annotations

import base64
import copy

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from prismatic.verification.attestation import (
    canonicalize_receipt,
    verify_receipt_attestation,
)
from prismatic.verification.receipt_validator import determine_merge_eligibility


def make_key_pair() -> tuple[ed25519.Ed25519PrivateKey, str]:
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return priv, pem


def make_key_record(
    key_id: str = "verification-key-1",
    verifier_id: str = "verifier-1",
    public_key_pem: str | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    expires_at: str | None = None,
    revoked_at: str | None = None,
    supersedes_key_id: str | None = None,
) -> tuple[ed25519.Ed25519PrivateKey, dict[str, str | None]]:
    priv, pem = make_key_pair()
    rec: dict[str, str | None] = {
        "id": verifier_id,
        "key_id": key_id,
        "algorithm": "ed25519",
        "public_key_pem": public_key_pem or pem,
        "created_at": created_at,
        "expires_at": expires_at,
        "revoked_at": revoked_at,
    }
    if supersedes_key_id:
        rec["supersedes_key_id"] = supersedes_key_id
    return priv, rec


def sign_receipt(
    receipt: dict,
    private_key: ed25519.Ed25519PrivateKey,
    key_id: str = "verification-key-1",
    algorithm: str = "ed25519",
    att_type: str = "attestation",
) -> dict:
    signed_receipt = copy.deepcopy(receipt)
    signed_receipt["signature_or_attestation"] = {
        "type": att_type,
        "algorithm": algorithm,
        "key_id": key_id,
        "value": "",
    }
    payload_bytes = canonicalize_receipt(signed_receipt)
    raw_sig = private_key.sign(payload_bytes)
    signed_receipt["signature_or_attestation"]["value"] = base64.b64encode(
        raw_sig
    ).decode("ascii")
    return signed_receipt


def test_canonicalization_stability_ordering_nested_none_unicode() -> None:
    data1 = {
        "z_key": "val",
        "a_key": 1,
        "nested": {"b": 2, "a": 1, "unicode": "ñ_🚀"},
        "null_val": None,
        "signature_or_attestation": {
            "type": "attestation",
            "algorithm": "ed25519",
            "key_id": "k1",
            "value": "exclude_me",
        },
    }
    data2 = {
        "a_key": 1,
        "null_val": None,
        "nested": {"a": 1, "unicode": "ñ_🚀", "b": 2},
        "signature_or_attestation": {
            "value": "different_value",
            "key_id": "k1",
            "algorithm": "ed25519",
            "type": "attestation",
        },
        "z_key": "val",
    }
    bytes1 = canonicalize_receipt(data1)
    bytes2 = canonicalize_receipt(data2)
    assert bytes1 == bytes2
    assert b"exclude_me" not in bytes1
    assert b"different_value" not in bytes1
    assert "ñ_🚀".encode() in bytes1


def test_canonicalization_does_not_mutate_input() -> None:
    original = {
        "a": 1,
        "signature_or_attestation": {
            "type": "attestation",
            "algorithm": "ed25519",
            "key_id": "k1",
            "value": "original_sig",
        },
    }
    copied = copy.deepcopy(original)
    _ = canonicalize_receipt(original)
    assert original == copied


def test_valid_ed25519_attestation_passes() -> None:
    priv, rec = make_key_record()
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {
            "identities": [rec],
            "require_producer_verifier_separation": True,
        },
    }
    receipt = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-06-01T00:00:00Z",
        "candidate_sha": "a" * 40,
    }
    signed = sign_receipt(receipt, priv)
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is True
    assert reason is None


def test_wrong_private_public_key_rejects() -> None:
    priv1, rec = make_key_record()
    priv2, _ = make_key_pair()
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}
    signed = sign_receipt(receipt, priv2)  # signed with priv2, policy has pub1
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "signature_mismatch"


def test_unknown_key_id_rejects() -> None:
    priv, rec = make_key_record(key_id="key-1")
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["key-1", "key-2"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}
    signed = sign_receipt(receipt, priv, key_id="key-2")
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "unknown_key_id"


def test_malformed_pem_base64_and_signature_length_reject() -> None:
    priv, rec = make_key_record()
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}
    signed = sign_receipt(receipt, priv)

    # Malformed PEM
    bad_pem_policy = copy.deepcopy(policy)
    bad_pem_policy["approved_verifiers"]["identities"][0]["public_key_pem"] = (
        "NOT_A_PEM"
    )
    ok, reason = verify_receipt_attestation(signed, bad_pem_policy)
    assert ok is False
    assert reason == "malformed_public_key_pem"

    # Malformed Base64 signature
    bad_b64 = copy.deepcopy(signed)
    bad_b64["signature_or_attestation"]["value"] = "!!!not_base64!!!"
    ok, reason = verify_receipt_attestation(bad_b64, policy)
    assert ok is False
    assert reason == "malformed_base64_signature"

    # Signature length != 64 bytes
    bad_len = copy.deepcopy(signed)
    short_raw = b"short_sig"
    bad_len["signature_or_attestation"]["value"] = base64.b64encode(short_raw).decode(
        "ascii"
    )
    ok, reason = verify_receipt_attestation(bad_len, policy)
    assert ok is False
    assert reason == "invalid_signature_length"


def test_tampered_nested_receipt_value_rejects() -> None:
    priv, rec = make_key_record()
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-06-01T00:00:00Z",
        "nested": {"val": 123},
    }
    signed = sign_receipt(receipt, priv)

    # Tamper with nested value
    signed["nested"]["val"] = 999
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "signature_mismatch"


def test_attestation_metadata_is_signed() -> None:
    priv1, rec1 = make_key_record(key_id="verification-key-1", verifier_id="verifier-1")
    priv2, rec2 = make_key_record(key_id="verification-key-2", verifier_id="verifier-1")
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1", "verification-key-2"],
        },
        "approved_verifiers": {"identities": [rec1, rec2]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}
    signed = sign_receipt(receipt, priv1, key_id="verification-key-1")

    # Tamper with attestation key_id metadata after signing
    signed["signature_or_attestation"]["key_id"] = "verification-key-2"
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "signature_mismatch"


def test_missing_attestation_when_required_rejects() -> None:
    _, rec = make_key_record()
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}
    ok, reason = verify_receipt_attestation(receipt, policy)
    assert ok is False
    assert reason == "missing_attestation"


def test_wrong_algorithm_or_type_rejects() -> None:
    priv, rec = make_key_record()
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}

    # Wrong attestation type
    signed_type = sign_receipt(receipt, priv, att_type="wrong_type")
    ok, reason = verify_receipt_attestation(signed_type, policy)
    assert ok is False
    assert reason == "unsupported_attestation_type"

    # Wrong algorithm
    signed_algo = sign_receipt(receipt, priv, algorithm="ecdsa-p256")
    ok, reason = verify_receipt_attestation(signed_algo, policy)
    assert ok is False
    assert reason == "unapproved_attestation_algorithm"


def test_key_not_yet_active_rejects() -> None:
    priv, rec = make_key_record(created_at="2026-06-01T12:00:00.000000000Z")
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    # Receipt completed 1 nanosecond before creation
    receipt = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-06-01T11:59:59.999999999Z",
    }
    signed = sign_receipt(receipt, priv)
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "key_not_yet_active"


def test_expired_key_rejects_at_exact_nanosecond_boundary() -> None:
    priv, rec = make_key_record(
        created_at="2026-01-01T00:00:00Z", expires_at="2026-06-01T12:00:00.000000000Z"
    )
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }

    # Receipt completed 1 nanosecond before expiry -> passes
    receipt_valid = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-06-01T11:59:59.999999999Z",
    }
    signed_valid = sign_receipt(receipt_valid, priv)
    ok_valid, reason_valid = verify_receipt_attestation(signed_valid, policy)
    assert ok_valid is True
    assert reason_valid is None

    # Receipt completed at exact expiry timestamp -> rejects
    receipt_exact = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-06-01T12:00:00.000000000Z",
    }
    signed_exact = sign_receipt(receipt_exact, priv)
    ok_exact, reason_exact = verify_receipt_attestation(signed_exact, policy)
    assert ok_exact is False
    assert reason_exact == "key_expired"


def test_old_key_before_expiry_passes_at_after_expiry_rejects_new_key_passes() -> None:
    # Key 1: active 2026-01-01 to 2026-06-01
    priv1, rec1 = make_key_record(
        key_id="k1",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-06-01T00:00:00Z",
    )
    # Key 2: active starting 2026-06-01 (successor)
    priv2, rec2 = make_key_record(
        key_id="k2",
        created_at="2026-06-01T00:00:00Z",
        expires_at=None,
        supersedes_key_id="k1",
    )

    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["k1", "k2"],
        },
        "approved_verifiers": {"identities": [rec1, rec2]},
    }

    # Old key signing receipt from May 2026 -> passes
    r_may = {"verifier_id": "verifier-1", "completed_at": "2026-05-15T00:00:00Z"}
    signed_may = sign_receipt(r_may, priv1, key_id="k1")
    ok1, reason1 = verify_receipt_attestation(signed_may, policy)
    assert ok1 is True

    # Old key signing receipt from June 15 2026 -> rejects (expired)
    r_june = {"verifier_id": "verifier-1", "completed_at": "2026-06-15T00:00:00Z"}
    signed_june_old = sign_receipt(r_june, priv1, key_id="k1")
    ok2, reason2 = verify_receipt_attestation(signed_june_old, policy)
    assert ok2 is False
    assert reason2 == "key_expired"

    # New key signing receipt from June 15 2026 -> passes
    signed_june_new = sign_receipt(r_june, priv2, key_id="k2")
    ok3, reason3 = verify_receipt_attestation(signed_june_new, policy)
    assert ok3 is True
    assert reason3 is None


def test_revoked_key_always_rejects() -> None:
    priv, rec = make_key_record(
        created_at="2026-01-01T00:00:00Z", revoked_at="2026-03-01T00:00:00Z"
    )
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }

    # Even for receipt completed BEFORE revoked_at, current policy revocation is authoritative
    r_feb = {"verifier_id": "verifier-1", "completed_at": "2026-02-01T00:00:00Z"}
    signed = sign_receipt(r_feb, priv)
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "key_revoked"


def test_duplicate_key_id_records_reject() -> None:
    priv1, rec1 = make_key_record(key_id="k1", verifier_id="verifier-1")
    _, rec2 = make_key_record(key_id="k1", verifier_id="verifier-2")

    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["k1"],
        },
        "approved_verifiers": {"identities": [rec1, rec2]},
    }
    receipt = {"verifier_id": "verifier-1", "completed_at": "2026-06-01T00:00:00Z"}
    signed = sign_receipt(receipt, priv1, key_id="k1")
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "duplicate_key_id_records"


def test_no_key_for_approved_verifier_rejects() -> None:
    priv, rec = make_key_record(verifier_id="verifier-1")
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
        },
        "approved_verifiers": {"identities": [rec]},
    }
    receipt = {"verifier_id": "verifier-2", "completed_at": "2026-06-01T00:00:00Z"}
    signed = sign_receipt(receipt, priv)
    signed["verifier_id"] = "verifier-2"
    ok, reason = verify_receipt_attestation(signed, policy)
    assert ok is False
    assert reason == "no_key_for_approved_verifier"


def test_fresh_key_threshold_exact_boundary_tests() -> None:
    # Created 2026-01-01T00:00:00Z, max_key_age_seconds = 3600
    priv, rec = make_key_record(created_at="2026-01-01T00:00:00.000000000Z")
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["verification-key-1"],
            "require_fresh_key": True,
            "max_key_age_seconds": 3600,
        },
        "approved_verifiers": {"identities": [rec]},
    }

    # Completed exactly at 3600 seconds -> fresh
    r_exact = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-01-01T01:00:00.000000000Z",
    }
    signed_exact = sign_receipt(r_exact, priv)
    ok_exact, reason_exact = verify_receipt_attestation(signed_exact, policy)
    assert ok_exact is True
    assert reason_exact is None

    # Completed 3600s + 1ns after creation -> exceeds max age
    r_stale = {
        "verifier_id": "verifier-1",
        "completed_at": "2026-01-01T01:00:00.000000001Z",
    }
    signed_stale = sign_receipt(r_stale, priv)
    ok_stale, reason_stale = verify_receipt_attestation(signed_stale, policy)
    assert ok_stale is False
    assert reason_stale == "key_exceeds_max_age"


def test_producer_verifier_separation_preserved() -> None:
    from test_receipt_validator import valid_policy, valid_receipt

    priv, rec = make_key_record(verifier_id="verifier-1")
    policy = valid_policy()
    policy["approved_verifiers"]["identities"] = [rec]
    receipt = valid_receipt()
    receipt["producer_id"] = "verifier-1"
    receipt["verifier_id"] = "verifier-1"
    signed = sign_receipt(receipt, priv)
    ok, reason = determine_merge_eligibility(signed, policy)
    assert ok is False
    assert reason == "producer_verifier_separation_failed"
