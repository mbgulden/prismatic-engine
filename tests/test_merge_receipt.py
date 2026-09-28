"""Merge receipt tests — signed proof emitted on every merge.

Covers: receipt shape and bindings, Ed25519 signing (and the explicit-
unsigned path when no key is configured), durable JSONL persistence, and
read-back for the earned-autonomy ``receipt_emitted`` evidence.
"""

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from prismatic.verification.attestation import canonicalize_receipt
from prismatic.verification.merge_receipt import (
    MERGE_RECEIPT_MARKER,
    build_merge_receipt,
    find_merge_receipts,
    persist_merge_receipt,
    sign_merge_receipt,
)


def _make_receipt():
    return build_merge_receipt(
        repository="mbgulden/prismatic-engine",
        candidate_sha="a" * 40,
        candidate_tree="b" * 40,
        base_sha="c" * 40,
        merge_sha="d" * 40,
        actor="merge-authority",
        authorization_id="auth-1",
        job_id="job-1",
        task_id="task-1",
        manifest_digest="m" * 64,
        policy_version="v1",
        change_class="docs",
        verified_receipt_refs=[
            {"receipt_id": "rcpt-1", "receipt_sha256": "e" * 64}
        ],
    )


# ── Shape ────────────────────────────────────────────────────────────


def test_build_bindings_and_marker():
    r = _make_receipt()
    assert r["marker"] == MERGE_RECEIPT_MARKER
    assert r["schema_version"] == "merge-receipt/v1"
    assert r["receipt_id"]
    assert r["emitted_at"]
    assert r["candidate_sha"] == "a" * 40
    assert r["candidate_tree"] == "b" * 40
    assert r["base_sha"] == "c" * 40
    assert r["merge_sha"] == "d" * 40
    assert r["verified_receipt_refs"] == [
        {"receipt_id": "rcpt-1", "receipt_sha256": "e" * 64}
    ]
    assert r["verifier_id"] == "rf-merge-executor"
    assert r["explicit_non_claims"]
    assert r["signature_or_attestation"] is None


# ── Signing ──────────────────────────────────────────────────────────


def _pem(private_key):
    return private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode("utf-8")


def test_sign_with_key_produces_verifiable_attestation(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPT_SIGNING_KEY", _pem(key))
    monkeypatch.setenv(
        "PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(tmp_path / "nope.pem")
    )
    r = sign_merge_receipt(_make_receipt())
    att = r["signature_or_attestation"]
    assert att["type"] == "attestation"
    assert att["algorithm"] == "ed25519"
    assert att["issuer"] == "rf-merge-executor"
    # Verify with the public key over the same canonical bytes the signer used.
    sig = base64.b64decode(att["value"])
    key.public_key().verify(sig, canonicalize_receipt(r))


def test_sign_without_key_is_explicitly_unsigned(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISMATIC_MERGE_RECEIPT_SIGNING_KEY", raising=False)
    monkeypatch.setenv(
        "PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(tmp_path / "nope.pem")
    )
    r = sign_merge_receipt(_make_receipt())
    assert r["signature_or_attestation"] is None
    assert any(
        claim.startswith("unsigned:")
        for claim in r["explicit_non_claims"]
    )


# ── Persistence / read-back ──────────────────────────────────────────


def test_persist_and_find_round_trip(tmp_path):
    log = tmp_path / "receipts.jsonl"
    r = _make_receipt()
    persisted = persist_merge_receipt(r, log_path=log)
    assert persisted == r["receipt_id"]
    rows = list(log.read_text(encoding="utf-8").strip().splitlines())
    assert len(rows) == 1
    assert json.loads(rows[0])["merge_sha"] == "d" * 40


def test_find_filters_by_merge_sha_and_candidate(tmp_path):
    log = tmp_path / "receipts.jsonl"
    r1 = _make_receipt()
    persist_merge_receipt(r1, log_path=log)
    r2 = _make_receipt()
    r2["merge_sha"] = "f" * 40
    persist_merge_receipt(r2, log_path=log)

    assert [r["receipt_id"] for r in find_merge_receipts(
        merge_sha="d" * 40, log_path=log
    )] == [r1["receipt_id"]]
    assert len(find_merge_receipts(
        candidate_sha="a" * 40, log_path=log
    )) == 2
    assert find_merge_receipts(merge_sha="0" * 40, log_path=log) == []
    assert find_merge_receipts(log_path=tmp_path / "missing.jsonl") == []


def test_persist_never_raises(tmp_path):
    # A directory as the log path cannot be appended to — must return None,
    # not raise.
    assert persist_merge_receipt(_make_receipt(), log_path=tmp_path) is None


def test_persist_returns_receipt_id_when_receipt_lacks_one(tmp_path):
    log = tmp_path / "receipts.jsonl"
    r = _make_receipt()
    r["receipt_id"] = ""
    assert persist_merge_receipt(r, log_path=log) is None
    assert log.exists()  # the write itself still happened
