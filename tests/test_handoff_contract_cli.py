from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "validate_handoff_contract.py"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "handoff-contracts"


def run_cli(fixture: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), str(FIXTURE_DIR / fixture)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_validate_handoff_contract_cli_accepts_pass_fixture() -> None:
    result = run_cli("pass.json")
    assert result.returncode == 0
    assert "HANDOFF_CONTRACT_VALID" in result.stdout
    assert result.stderr == ""


def test_validate_handoff_contract_cli_can_emit_json() -> None:
    result = subprocess.run(
        [sys.executable, str(CLI), "--json", str(FIXTURE_DIR / "pass.json")],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
    assert '"ok": true' in result.stdout
    assert result.stderr == ""


def test_validate_handoff_contract_cli_rejects_missing_result_fixture() -> None:
    result = run_cli("missing-result.json")
    assert result.returncode != 0
    assert "HANDOFF_CONTRACT_INVALID" in result.stderr
    assert "missing required result artifacts" in result.stderr


def test_validate_handoff_contract_cli_rejects_out_of_lane_fixture() -> None:
    result = run_cli("out-of-lane.json")
    assert result.returncode != 0
    assert "HANDOFF_CONTRACT_INVALID" in result.stderr
    assert "changed path outside allowed_paths" in result.stderr


def test_validate_handoff_contract_cli_rejects_missing_production_proof_fixture() -> (
    None
):
    result = run_cli("production-proof-missing.json")
    assert result.returncode != 0
    assert "HANDOFF_CONTRACT_INVALID" in result.stderr
    assert "missing production_proof artifacts" in result.stderr


def test_validate_handoff_contract_cli_rejects_ambiguous_target_fixture() -> None:
    result = run_cli("ambiguous-target-agent.json")
    assert result.returncode != 0
    assert "HANDOFF_CONTRACT_INVALID" in result.stderr
    assert "should be non-empty" in result.stderr
