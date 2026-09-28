"""ReceiptJudge unit tests — ADR-0002 independent receipt validation.

The judge is the merge authority's independent check on the candidate's
verification receipt at decision time. These tests use a fake receipt store
so the judge's own fail-closed logic is exercised without touching the real
DB.
"""

from datetime import datetime, timedelta, timezone

from prismatic.review_factory.receipt_judge import ReceiptJudge


class _Stored:
    def __init__(self, receipt):
        self.receipt = receipt


class _FakeStore:
    def __init__(self, receipts=None):
        self.receipts = dict(receipts or {})

    def get(self, receipt_id):
        try:
            return self.receipts[receipt_id]
        except KeyError:
            raise KeyError(receipt_id)


def _ts(dt):
    """Canonical UTC RFC3339 (Z-suffixed), as the freshness validator requires."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fresh_receipt(**overrides):
    now = datetime.now(timezone.utc)
    receipt = {
        "receipt_id": "rcpt-1",
        "candidate_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "producer_id": "hermes-agent-7",
        "verifier_id": "rf-verifier-1",
        "decision": {"status": "pass", "merge_eligible": True},
        "started_at": _ts(now - timedelta(minutes=5)),
        "completed_at": _ts(now),
        "expires_at": _ts(now + timedelta(hours=23)),
        "revocation_status": "active",
    }
    receipt.update(overrides)
    return receipt


def _judge(receipts=None, **kwargs):
    store = _FakeStore({"rcpt-1": _Stored(_fresh_receipt())} if receipts is None else receipts)
    return ReceiptJudge(store=store, **kwargs)


# ── Success path ─────────────────────────────────────────────────────


def test_valid_receipt_passes():
    ok, reason = _judge().validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
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
    ok, reason = _judge().validate(
        receipt_id="nope", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason == "receipt_not_found"


def test_candidate_sha_mismatch_refuses():
    ok, reason = _judge().validate(
        receipt_id="rcpt-1", expected_candidate_sha="f" * 40
    )
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
        "rcpt-1": _Stored(
            _fresh_receipt(producer_id="same", verifier_id="same")
        )
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
    ok, reason = judge.validate(
        receipt_id="rcpt-1", expected_candidate_sha="a" * 40
    )
    assert ok is False
    assert reason.startswith("receipt_store_error")
