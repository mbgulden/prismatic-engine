"""
Unit tests for SwarmProof Truth Oracle v0.3.0.
"""

from prismatic.verification.swarmproof_oracle import SwarmProofOracle


def test_swarmproof_oracle_valid_change():
    old_code = """
def test_addition():
    assert 1 + 1 == 2
"""
    new_code = """
def test_addition():
    assert 1 + 1 == 2

def test_multiplication():
    assert 2 * 3 == 6
"""

    receipt = SwarmProofOracle.verify_candidate(
        task_id="GRO-5100",
        candidate_sha="abc12345",
        file_diffs={"tests/test_math.py": {"old": old_code, "new": new_code}},
        visual_audit_result={"passed": True},
    )

    assert receipt.valid is True
    assert receipt.ast_passed is True
    assert receipt.test_integrity_passed is True
    assert receipt.visual_audit_passed is True
    assert len(receipt.violations) == 0
    assert len(receipt.proof_id) > 0


def test_swarmproof_oracle_catches_test_deletion():
    old_code = """
def test_one():
    assert 1 == 1

def test_two():
    assert 2 == 2
"""
    new_code = """
def test_one():
    assert 1 == 1
"""

    receipt = SwarmProofOracle.verify_candidate(
        task_id="GRO-5101",
        candidate_sha="def67890",
        file_diffs={"tests/test_sample.py": {"old": old_code, "new": new_code}},
    )

    assert receipt.valid is False
    assert receipt.test_integrity_passed is False
    assert any("TEST REDUCTION" in v for v in receipt.violations)


def test_swarmproof_oracle_catches_trivial_assertions():
    old_code = """
def test_something():
    assert calculate(5) == 10
"""
    new_code = """
def test_something():
    assert True
"""

    receipt = SwarmProofOracle.verify_candidate(
        task_id="GRO-5102",
        candidate_sha="bad45678",
        file_diffs={"tests/test_sample.py": {"old": old_code, "new": new_code}},
    )

    assert receipt.valid is False
    assert receipt.ast_passed is False
    assert any("Trivial assertion" in v for v in receipt.violations)
