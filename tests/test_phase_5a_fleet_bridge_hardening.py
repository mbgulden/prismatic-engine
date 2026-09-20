"""Unit and Integration Test Suite for Phase 5A Fleet Bridge Hardening."""

import os
import tempfile
from pathlib import Path
import pytest

from prismatic.hypervisor.ledger import HypervisorLedger, _get_default_ledger_db
from prismatic.verification.ast_guard import ASTGuard
from prismatic.client.interceptor import HypervisorClient


def test_hypervisor_ledger_persistent_path_and_wal_checkpointing():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "persistent_ledger.db"
        ledger = HypervisorLedger(db_path=db_path)

        # 1. Record events up to checkpoint trigger
        for i in range(105):
            ledger.record_event(
                task_id="GRO-5001",
                producer="fleet_test",
                action="BATCH_EVENT",
                payload={"index": i},
            )

        # 2. Checkpoint WAL explicitly
        cp = ledger.checkpoint_wal(mode="PASSIVE")
        assert "busy" in cp
        assert "log_frames" in cp
        assert "checkpointed" in cp

        # 3. Verify integrity
        integrity = ledger.verify_chain_integrity()
        assert integrity["valid"] is True
        assert integrity["verified_entries"] == 105


def test_ast_guard_javascript_test_anti_weakening():
    old_js_code = """
describe("Payment Gateway Suite", () => {
    test("processes valid charge", () => {
        expect(charge.status).toBe("succeeded");
        expect(charge.amount).toBe(5000);
    });

    test("handles card decline", () => {
        expect(decline.code).toBe("card_declined");
    });
});
"""

    # 1. Valid additive JS edit -> PASS
    valid_new_js = """
describe("Payment Gateway Suite", () => {
    test("processes valid charge", () => {
        expect(charge.status).toBe("succeeded");
        expect(charge.amount).toBe(5000);
        expect(charge.currency).toBe("usd");
    });

    test("handles card decline", () => {
        expect(decline.code).toBe("card_declined");
    });
});
"""
    res = ASTGuard.validate_diff(old_js_code, valid_new_js, filename="tests/payment.spec.js")
    assert res.valid is True
    assert len(res.violations) == 0

    # 2. Deleting a test in JS -> FAIL
    deleted_test_js = """
describe("Payment Gateway Suite", () => {
    test("processes valid charge", () => {
        expect(charge.status).toBe("succeeded");
        expect(charge.amount).toBe(5000);
    });
});
"""
    res_del = ASTGuard.validate_diff(old_js_code, deleted_test_js, filename="tests/payment.test.ts")
    assert res_del.valid is False
    assert any("deleted or renamed" in v for v in res_del.violations)

    # 3. Trivial assertion in JS -> FAIL
    trivial_js = """
describe("Payment Suite", () => {
    test("trivial assertion test", () => {
        expect(true).toBe(true);
    });
});
"""
    res_triv = ASTGuard.validate_diff("", trivial_js, filename="tests/payment.spec.js")
    assert res_triv.valid is False
    assert any("Trivial assertion" in v for v in res_triv.violations)


def test_hypervisor_client_resilient_backoff():
    # Client targeting non-existent local port -> handles backoff gracefully without unhandled exception
    client = HypervisorClient(endpoint="http://127.0.0.1:59999")
    res = client._get("/api/healthz", max_retries=2)
    assert res is None
