"""Unit and integration test suite for Hypervisor Immutable Ledger and AST Anti-Weakening Guard."""

import os
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.hypervisor.ledger import HypervisorLedger, _compute_merkle_root
from prismatic.verification.ast_guard import ASTGuard


@pytest.fixture
def client():
    return TestClient(app)


def test_hypervisor_ledger_hash_chain_and_merkle_roots():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_ledger.db"
        ledger = HypervisorLedger(db_path=db_path)

        # 1. Record consecutive events
        e1 = ledger.record_event(task_id="GRO-4001", producer="agy", action="TASK_ADMITTED", payload={"intent": "refactor"})
        assert e1.prev_hash == "0" * 64
        assert len(e1.entry_hash) == 64

        e2 = ledger.record_event(task_id="GRO-4001", producer="agy", action="SWARMLOCK_ACQUIRED", payload={"paths": ["a.py"]})
        assert e2.prev_hash == e1.entry_hash

        e3 = ledger.record_event(task_id="GRO-4002", producer="kai", action="SIGNAL_EMITTED", payload={"stage": "STYLING"})
        assert e3.prev_hash == e2.entry_hash

        # 2. Verify unbroken chain
        integrity = ledger.verify_chain_integrity()
        assert integrity["valid"] is True
        assert integrity["verified_entries"] == 3

        # 3. Merkle root computation
        merkle = ledger.get_merkle_root()
        assert len(merkle["merkle_root"]) == 64
        assert merkle["entry_count"] == 3

        # 4. Filter by task_id
        task_events = ledger.list_events(task_id="GRO-4001")
        assert len(task_events) == 2


def test_ast_guard_detects_anti_weakening_violations():
    old_test_code = """
def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4

def test_subtraction():
    assert 5 - 3 == 2
"""

    # 1. Valid additive change
    valid_new_code = """
def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4
    assert 3 + 3 == 6

def test_subtraction():
    assert 5 - 3 == 2
"""
    res = ASTGuard.validate_diff(old_test_code, valid_new_code, filename="tests/test_math.py")
    assert res.valid is True
    assert len(res.violations) == 0
    assert res.new_assertion_count == 4

    # 2. Deleting a test function -> Violation
    deleted_func_code = """
def test_addition():
    assert 1 + 1 == 2
    assert 2 + 2 == 4
"""
    res = ASTGuard.validate_diff(old_test_code, deleted_func_code, filename="tests/test_math.py")
    assert res.valid is False
    assert any("deleted or renamed" in v for v in res.violations)

    # 3. Deleting an assert statement inside a test -> Violation
    deleted_assert_code = """
def test_addition():
    assert 1 + 1 == 2

def test_subtraction():
    assert 5 - 3 == 2
"""
    res = ASTGuard.validate_diff(old_test_code, deleted_assert_code, filename="tests/test_math.py")
    assert res.valid is False
    assert any("assertion count dropped" in v or "Total assertion count decreased" in v for v in res.violations)

    # 4. Trivial assertion `assert True` or `assert 1 == 1` -> Violation
    trivial_code = """
def test_addition():
    assert True
    assert 1 == 1
"""
    res = ASTGuard.validate_diff("", trivial_code, filename="tests/test_math.py")
    assert res.valid is False
    assert any("Trivial assertion" in v or "Constant comparison" in v for v in res.violations)


def test_hypervisor_api_endpoints(client):
    # 1. Check ledger endpoint
    res = client.get("/api/gateway/hypervisor/ledger?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["events"], list)

    # 2. Check merkle-root endpoint
    res_root = client.get("/api/gateway/hypervisor/merkle-root")
    assert res_root.status_code == 200
    assert "merkle_root" in res_root.json()

    # 3. Check AST verification endpoint
    payload = {
        "old_code": "def test_foo():\n    assert 1 == 1\n",
        "new_code": "def test_foo():\n    assert 1 + 1 == 2\n",
        "filename": "tests/test_foo.py"
    }
    res_ast = client.post("/api/gateway/hypervisor/verify-ast", json=payload)
    assert res_ast.status_code == 200
    assert "result" in res_ast.json()
