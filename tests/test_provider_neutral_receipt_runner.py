"""Unit and integration tests for provider-neutral verification receipt runner (GRO-4203)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from prismatic.verification.receipt_runner import (
    PROVIDER_NEUTRAL_RECEIPT_RUNNER_MARKER,
    CommandSpec,
    ReceiptRunnerResult,
    run_provider_neutral_verification,
    cli,
)
from prismatic.verification.receipt_store import get_verification_receipt
from prismatic.verification.attestation import verify_receipt_attestation


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create an isolated, committed Git repo for verification testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True, capture_output=True)

    # Initial commit
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo), check=True, capture_output=True)

    # Candidate commit
    (repo / "feature.py").write_text("def hello(): return 'world'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add feature"], cwd=str(repo), check=True, capture_output=True)

    return repo


def test_provider_neutral_receipt_runner_success(temp_git_repo: Path, tmp_path: Path):
    db_path = tmp_path / "receipts.sqlite3"
    signing_key = Ed25519PrivateKey.generate()

    commands = [
        CommandSpec(
            command_id="test-cmd-success",
            argv=(sys.executable, "-c", "print('All tests passed cleanly')"),
            proof_class="unit",
            cwd=str(temp_git_repo),
        )
    ]

    result = run_provider_neutral_verification(
        repository_path=temp_git_repo,
        candidate_ref="HEAD",
        task_id="GRO-4203-T1",
        policy_id="test-clean-room",
        policy_version="1.0.0",
        commands=commands,
        signing_key=signing_key,
        signing_key_id="verifier-key-test",
        db_path=db_path,
    )

    assert result.reason is None, f"Reason: {result.reason}"
    assert result.status == "PASS"
    assert result.eligible is True
    assert result.reason is None
    assert result.task_id == "GRO-4203-T1"
    assert len(result.candidate_sha) == 40
    assert len(result.tree_sha) == 40
    assert len(result.base_sha) == 40

    # Verify signature attestation
    sig = result.receipt_payload.get("signature_or_attestation")
    assert sig is not None
    assert sig["type"] == "attestation"
    assert sig["algorithm"] == "ed25519"
    assert sig["key_id"] == "verifier-key-test"
    assert len(sig["value"]) > 0

    # Verify persistence in SQLite store
    stored = get_verification_receipt(result.receipt_id, db_path=db_path)
    assert stored is not None
    assert stored.receipt["candidate_sha"] == result.candidate_sha
    assert stored.merge_eligible is True


def test_provider_neutral_receipt_runner_command_failure(temp_git_repo: Path, tmp_path: Path):
    db_path = tmp_path / "receipts_fail.sqlite3"

    commands = [
        CommandSpec(
            command_id="test-cmd-fail",
            argv=(sys.executable, "-c", "import sys; sys.exit(2)"),
            proof_class="unit",
            cwd=str(temp_git_repo),
        )
    ]

    result = run_provider_neutral_verification(
        repository_path=temp_git_repo,
        candidate_ref="HEAD",
        task_id="GRO-4203-FAIL",
        commands=commands,
        db_path=db_path,
    )

    assert result.marker == PROVIDER_NEUTRAL_RECEIPT_RUNNER_MARKER
    assert result.status == "FAIL"
    assert result.eligible is False
    assert result.receipt_payload["decision"]["status"] == "fail"
    assert result.receipt_payload["decision"]["merge_eligible"] is False
    assert result.receipt_payload["commands_and_exit_states"][0]["exit_code"] == 2


def test_provider_neutral_receipt_runner_cli(temp_git_repo: Path, tmp_path: Path, capsys):
    db_path = tmp_path / "cli_receipts.sqlite3"
    exit_code = cli([
        "--repo", str(temp_git_repo),
        "--task-id", "CLI-TASK-01",
        "--command", f"{sys.executable} -c \"print('CLI test passed')\"",
        "--db-path", str(db_path),
        "--json",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["marker"] == PROVIDER_NEUTRAL_RECEIPT_RUNNER_MARKER
    assert payload["eligible"] is True
    assert payload["task_id"] == "CLI-TASK-01"


def test_prismatic_cli_receipt_run(temp_git_repo: Path, tmp_path: Path, capsys):
    from prismatic.cli import run

    db_path = tmp_path / "cli_receipts_main.sqlite3"
    exit_code = run([
        "receipt-run",
        "--repo", str(temp_git_repo),
        "--task-id", "CLI-MAIN-01",
        "--command", f"{sys.executable} -c \"print('CLI main passed')\"",
        "--db-path", str(db_path),
        "--json",
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["marker"] == PROVIDER_NEUTRAL_RECEIPT_RUNNER_MARKER
    assert payload["eligible"] is True
    assert payload["task_id"] == "CLI-MAIN-01"
