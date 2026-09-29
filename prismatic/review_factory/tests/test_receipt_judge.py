"""ReceiptJudge unit tests — ADR-0002 independent receipt validation.

The judge is the merge authority's independent check on the candidate's
verification receipt at decision time. These tests use a fake receipt store
so the judge's own fail-closed logic is exercised without touching the real
DB. Receipts are Ed25519-signed against a test key whose record lives in
the fake stored policy, mirroring the production attestation path.
"""

import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from prismatic.review_factory.receipt_judge import ReceiptJudge
from prismatic.verification.attestation import canonicalize_receipt

_TEST_KEY = Ed25519PrivateKey.generate()
_TEST_KEY_ID = "judge-test-key-01"
_TEST_VERIFIER_ID = "rf-verifier-1"


def _ts(dt):
    """Canonical UTC RFC3339 (Z-suffixed), as the freshness validator requires."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _test_policy(**overrides):
    now = datetime.now(timezone.utc)
    policy = {
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": [_TEST_KEY_ID],
        },
        "approved_verifiers": {
            "identities": [
                {
                    "id": _TEST_VERIFIER_ID,
                    "key_id": _TEST_KEY_ID,
                    "algorithm": "ed25519",
                    "public_key_pem": _TEST_KEY.public_key()
                    .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
                    .decode("utf-8"),
                    "created_at": _ts(now - timedelta(days=1)),
                    "expires_at": _ts(now + timedelta(days=365)),
                }
            ]
        },
        "freshness": {"max_age_seconds": 3600},
    }
    policy.update(overrides)
    return policy


class _Stored:
    def __init__(self, receipt, policy=None):
        self.receipt = receipt
        self.policy = policy if policy is not None else _test_policy()


class _FakeStore:
    def __init__(self, receipts=None):
        self.receipts = dict(receipts or {})

    def get(self, receipt_id):
        try:
            return self.receipts[receipt_id]
        except KeyError:
            raise KeyError(receipt_id)


def _sign(receipt, key=None, key_id=None):
    """Stage attestation metadata, sign canonical bytes, fill the value."""
    key = key or _TEST_KEY
    receipt["signature_or_attestation"] = {
        "type": "attestation",
        "algorithm": "ed25519",
        "key_id": key_id or _TEST_KEY_ID,
        "issuer": _TEST_VERIFIER_ID,
        "value": "",
    }
    signature = key.sign(canonicalize_receipt(receipt))
    receipt["signature_or_attestation"]["value"] = base64.b64encode(signature).decode(
        "ascii"
    )
    return receipt


def _fresh_receipt(**overrides):
    now = datetime.now(timezone.utc)
    receipt = {
        "receipt_id": "rcpt-1",
        "candidate_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "producer_id": "hermes-agent-7",
        "verifier_id": _TEST_VERIFIER_ID,
        "decision": {"status": "pass", "merge_eligible": True},
        "started_at": _ts(now - timedelta(minutes=5)),
        "completed_at": _ts(now),
        "expires_at": _ts(now + timedelta(hours=23)),
        "revocation_status": "active",
    }
    receipt.update(overrides)
    return _sign(receipt)


def _judge(receipts=None, **kwargs):
    store = _FakeStore(
        {"rcpt-1": _Stored(_fresh_receipt())} if receipts is None else receipts
    )
    return ReceiptJudge(store=store, **kwargs)


# ── Success path ─────────────────────────────────────────────────────


def test_valid_receipt_passes():
    ok, reason = _judge().validate(receipt_id="rcpt-1", expected_candidate_sha="a" * 40)
    assert ok is True
    assert reason is None


def test_valid_receipt_passes_with_tree_binding():
    ok, _ = _judge().validate(
        receipt_id="rcpt-1",
        expected_candidate_sha="a" * 40,
        expected_tree_sha="b" * 40,
    )
    assert ok is True


# ── Failure paths (all fail closed) ──────────────────────────────────


def test_missing_receipt_id_refuses():
    ok, reason = _judge().validate(receipt_id="", expected_candidate_sha="a" * 40)
    assert ok is False
    assert reason == "receipt_missing"


def test_unknown_receipt_id_refuses():
    ok, reason = _judge().validate(receipt_id="nope", expected_candidate_sha="a" * 40)
    assert ok is False
    assert reason == "receipt_not_found"


def test_candidate_sha_mismatch_refuses():
    ok, reason = _judge().validate(receipt_id="rcpt-1", expected_candidate_sha="f" * 40)
    assert ok is False
    assert reason == "candidate_sha_mismatch"


def test_tree_sha_mismatch_refuses():
    ok, reason = _judge().validate(
        receipt_id="rcpt-1",
        expected_candidate_sha="a" * 40,
        expected_tree_sha="f" * 40,
    )
    assert ok is False
    assert reason == "tree_sha_mismatch"


def test_producer_verifier_separation_failure_refuses():
    receipts = {
        "rcpt-1": _Stored(_fresh_receipt(producer_id="same", verifier_id="same"))
    }
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "producer_verifier_separation_failed"


def test_missing_producer_identity_refuses():
    receipts = {"rcpt-1": _Stored(_fresh_receipt(producer_id=None))}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "producer_or_verifier_identity_missing"


def test_non_pass_decision_refuses():
    receipts = {
        "rcpt-1": _Stored(
            _fresh_receipt(decision={"status": "fail", "merge_eligible": False})
        )
    }
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "decision_status_fail"


def test_pass_but_not_merge_eligible_refuses():
    receipts = {
        "rcpt-1": _Stored(
            _fresh_receipt(decision={"status": "pass", "merge_eligible": False})
        )
    }
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "decision_not_merge_eligible"


def test_stale_receipt_refuses():
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=5)
    receipts = {
        "rcpt-1": _Stored(
            _fresh_receipt(
                started_at=_ts(old - timedelta(minutes=5)),
                completed_at=_ts(old),
                expires_at=_ts(now + timedelta(hours=1)),
            )
        )
    }
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "freshness_failed: receipt_stale"


def test_revoked_receipt_refuses(tmp_path):
    revoked = tmp_path / "revocations.json"
    revoked.write_text('["rcpt-1"]', encoding="utf-8")
    ok, reason = _judge(revocation_store=revoked).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "revocation_failed: receipt_revoked_by_id_rcpt-1"


def test_revocation_status_not_active_refuses():
    receipts = {"rcpt-1": _Stored(_fresh_receipt(revocation_status="revoked"))}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "revocation_failed: revocation_status_revoked"


def test_malformed_stored_receipt_refuses():
    receipts = {"rcpt-1": _Stored("not-a-dict")}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "malformed_receipt"


def test_judge_never_raises_on_store_error():
    class _ExplodingStore:
        def get(self, receipt_id):
            raise RuntimeError("disk on fire")

    judge = ReceiptJudge(store=_ExplodingStore())
    ok, reason = judge.validate(receipt_id="rcpt-1", expected_candidate_sha="a" * 40)
    assert ok is False
    assert reason.startswith("receipt_store_error")


# ── Attestation (fail closed on unsigned / tampered) ──────────────────


def test_unsigned_receipt_refuses():
    receipt = _fresh_receipt()
    receipt["signature_or_attestation"] = None
    receipts = {"rcpt-1": _Stored(receipt)}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "missing_attestation"


def test_empty_signature_value_refuses():
    receipt = _fresh_receipt()
    receipt["signature_or_attestation"]["value"] = ""
    receipts = {"rcpt-1": _Stored(receipt)}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "missing_attestation"


def test_tampered_receipt_refuses():
    # Change a signed field the judge has no independent check for:
    # the signature no longer verifies.
    receipt = _fresh_receipt()
    receipt["producer_id"] = "mallory"
    receipts = {"rcpt-1": _Stored(receipt)}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason.startswith("attestation_failed")


def test_wrong_key_signature_refuses():
    other_key = Ed25519PrivateKey.generate()
    receipt = _fresh_receipt()
    _sign(receipt, key=other_key)  # re-sign with an unapproved key
    receipts = {"rcpt-1": _Stored(receipt)}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason.startswith("attestation_failed")


# ── Freshness bound comes from the stored policy ─────────────────────


def test_policy_freshness_bound_is_honored():
    now = datetime.now(timezone.utc)
    completed = now - timedelta(seconds=120)
    receipt = _fresh_receipt(
        started_at=_ts(completed - timedelta(minutes=1)),
        completed_at=_ts(completed),
        expires_at=_ts(now + timedelta(hours=1)),
    )
    policy = _test_policy(freshness={"max_age_seconds": 60})
    receipts = {"rcpt-1": _Stored(receipt, policy)}
    ok, reason = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "freshness_failed: receipt_stale"


def test_malformed_policy_freshness_falls_back_to_default():
    now = datetime.now(timezone.utc)
    completed = now - timedelta(seconds=120)
    receipt = _fresh_receipt(
        started_at=_ts(completed - timedelta(minutes=1)),
        completed_at=_ts(completed),
        expires_at=_ts(now + timedelta(hours=1)),
    )
    policy = _test_policy()
    del policy["freshness"]
    receipts = {"rcpt-1": _Stored(receipt, policy)}
    ok, _ = _judge(receipts).validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is True  # 120s < default 3600s backstop
