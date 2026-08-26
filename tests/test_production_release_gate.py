"""Unit tests for ReviewFactoryGate and Production Release Pipeline."""

import pytest
from prismatic.verification.review_gate import ReviewFactoryGate
from prismatic.verification.ast_guard import ASTGuard


def test_review_factory_gate_admission_happy_path():
    changed_files = [
        {
            "filename": "tests/test_calculator.py",
            "old_code": "def test_add():\n    assert 1 + 1 == 2\n",
            "new_code": "def test_add():\n    assert 1 + 1 == 2\n    assert 2 + 2 == 4\n",
        }
    ]
    receipt = {
        "exit_code": 0,
        "commit_sha": "abc12345",
        "task_id": "GRO-9001",
    }

    result = ReviewFactoryGate.evaluate_candidate(
        task_id="GRO-9001",
        candidate_sha="abc12345",
        changed_files=changed_files,
        receipt_data=receipt,
    )

    assert result.admitted is True
    assert len(result.violations) == 0
    assert result.receipt_valid is True


def test_review_factory_gate_rejects_ast_anti_weakening():
    changed_files = [
        {
            "filename": "tests/test_calculator.py",
            "old_code": "def test_add():\n    assert 1 + 1 == 2\n\ndef test_sub():\n    assert 5 - 2 == 3\n",
            "new_code": "def test_add():\n    assert 1 + 1 == 2\n",  # test_sub deleted!
        }
    ]
    receipt = {
        "exit_code": 0,
        "commit_sha": "abc12345",
        "task_id": "GRO-9002",
    }

    result = ReviewFactoryGate.evaluate_candidate(
        task_id="GRO-9002",
        candidate_sha="abc12345",
        changed_files=changed_files,
        receipt_data=receipt,
    )

    assert result.admitted is False
    assert any("deleted or renamed" in v for v in result.violations)


def test_review_factory_gate_rejects_failing_receipt():
    changed_files = [
        {
            "filename": "tests/test_calculator.py",
            "old_code": "def test_add():\n    assert 1 + 1 == 2\n",
            "new_code": "def test_add():\n    assert 1 + 1 == 2\n",
        }
    ]
    receipt = {
        "exit_code": 1,  # FAILED
        "commit_sha": "abc12345",
        "task_id": "GRO-9003",
    }

    result = ReviewFactoryGate.evaluate_candidate(
        task_id="GRO-9003",
        candidate_sha="abc12345",
        changed_files=changed_files,
        receipt_data=receipt,
    )

    assert result.admitted is False
    assert any("non-zero exit code" in v for v in result.violations)


def test_review_factory_gate_rejects_sha_mismatch():
    changed_files = [
        {
            "filename": "tests/test_calculator.py",
            "old_code": "def test_add():\n    assert 1 + 1 == 2\n",
            "new_code": "def test_add():\n    assert 1 + 1 == 2\n",
        }
    ]
    receipt = {
        "exit_code": 0,
        "commit_sha": "stale_sha_999",
        "task_id": "GRO-9004",
    }

    result = ReviewFactoryGate.evaluate_candidate(
        task_id="GRO-9004",
        candidate_sha="target_sha_111",
        changed_files=changed_files,
        receipt_data=receipt,
    )

    assert result.admitted is False
    assert any("SHA mismatch" in v for v in result.violations)
