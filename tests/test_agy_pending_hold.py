"""Tests for the AGY closeout contract v0.2.

Covers the runtime enforcement path used by completed-work ingestion:

- v0.2 packets require a trusted launch context (env-bound).
- Producer ``ACCEPTANCE_DECISION=PENDING`` is held, not bypassed.
- Legacy packets remain on the legacy dialect.
- Immutability / atomic idempotency of completed-work rows is preserved.
- Dual-artifact validation runs at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismatic.agy_completed_work import (
    AgyCompletedWorkConflictError,
    ingest_completed_work,
    normalize_agy_result_packet,
)
from prismatic.completed_work_gate import (
    GateClassification,
    classify_completed_work,
)

EXAMPLES_DIR = (
    Path(__file__).resolve().parent.parent
    / "prismatic"
    / "skills"
    / "prismatic-agent-closeout-contract"
    / "examples"
)


LAUNCH_ENV = {
    "PRISMATIC_DISPATCH_ISSUE": "GRO-4500",
    "PRISMATIC_DISPATCH_SOURCE_BRANCH": "feature/GRO-4500-closeout",
    "PRISMATIC_DISPATCH_BASE_BRANCH": "main",
    "PRISMATIC_DISPATCH_SOURCE_PATH": str(Path.home() / "work"),
    "PRISMATIC_DISPATCH_CANDIDATE_HEAD": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    "PRISMATIC_DISPATCH_CANDIDATE_TREE": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e",
    "PRISMATIC_DISPATCH_BASE_HEAD": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
}


def _load_pass_fixture() -> dict:
    packet = json.loads((EXAMPLES_DIR / "result-packet.pass.json").read_text())
    # The shipped fixture uses the legacy GRO-4457 identifier; rewrite to a
    # v0.2-mandated issue so we exercise the post-cutoff enforcement path.
    packet["TASK_ID"] = "GRO-4500"
    return packet


def test_v02_packet_requires_launch_context_in_env(monkeypatch: pytest.MonkeyPatch):
    for env_name in LAUNCH_ENV:
        monkeypatch.delenv(env_name, raising=False)
    with pytest.raises(ValueError, match="trusted launch context"):
        normalize_agy_result_packet(_load_pass_fixture())


def test_v02_packet_passes_with_launch_context(monkeypatch: pytest.MonkeyPatch):
    for key, value in LAUNCH_ENV.items():
        monkeypatch.setenv(key, value)
    normalized = normalize_agy_result_packet(_load_pass_fixture())
    gate_state = classify_completed_work(normalized)
    source = _load_pass_fixture()
    assert normalized["v02_closeout"] == source
    assert (
        normalized["trusted_launch_context"]["candidate_tree"]
        == LAUNCH_ENV["PRISMATIC_DISPATCH_CANDIDATE_TREE"]
    )
    assert normalized["proof"]["scope"] == source["SCOPE"]
    assert normalized["proof"]["attempt_id"] == source["ATTEMPT_ID"]
    assert normalized["proof"]["candidate_tree"] == source["CANDIDATE_TREE"]
    assert normalized["proof"]["log_sha256"] == source["LOG_SHA256"]
    assert normalized["proof"]["proof_classes"] == source["PROOF_CLASSES"]
    assert normalized["proof"]["side_effects"] == source["SIDE_EFFECTS"]
    assert normalized["proof"]["blockers"] == source["BLOCKERS"]
    assert gate_state.eligible_for_merge is False
    assert any(
        "producer acceptance pending independent review" in r
        for r in gate_state.reasons
    )
    assert gate_state.classification == GateClassification.MANUAL_REVIEW_SCOPE


def test_v02_packet_rejected_when_task_id_does_not_match_launch(
    monkeypatch: pytest.MonkeyPatch,
):
    for key, value in LAUNCH_ENV.items():
        monkeypatch.setenv(key, value)
    packet = _load_pass_fixture()
    packet["TASK_ID"] = "GRO-4501"  # mismatch with launch issue GRO-4500
    with pytest.raises(Exception) as exc:
        normalize_agy_result_packet(packet)
    assert "GRO-4500" in str(exc.value) or "does not match" in str(exc.value)


def test_v02_packet_rejects_unknown_property_and_nonpending_acceptance(
    monkeypatch: pytest.MonkeyPatch,
):
    for key, value in LAUNCH_ENV.items():
        monkeypatch.setenv(key, value)
    packet = _load_pass_fixture()
    packet["UNKNOWN_PROPERTY"] = "not allowed"
    packet["ACCEPTANCE_DECISION"] = "CLEAN"
    with pytest.raises(Exception) as exc:
        normalize_agy_result_packet(packet)
    message = str(exc.value)
    assert "Additional properties" in message or "unknown" in message.lower()
    assert "must be PENDING" in message


def test_v02_packet_rejects_secret_and_traversal_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
):
    for key, value in LAUNCH_ENV.items():
        monkeypatch.setenv(key, value)
    packet = _load_pass_fixture()
    packet["CHANGED_PATHS"] = ["../../.ssh/id_rsa"]
    packet["COMMAND"] = ["deploy --token=super-secret-value"]
    packet["LOG"] = "/tmp/unsafe.log"
    with pytest.raises(Exception) as exc:
        normalize_agy_result_packet(packet)
    message = str(exc.value).lower()
    assert "unsafe" in message or "secret" in message or "traversal" in message


def test_lowercase_legacy_packet_below_cutoff_unchanged():
    legacy = {
        "agent": "agy",
        "issue_identifier": "GRO-4499",
        "branch": "feature/legacy-fix",
        "base_branch": "main",
        "result_artifacts": ["/home/ubuntu/work/legacy/log.txt"],
        "verification": {
            "commands": ["pytest tests/test_legacy.py"],
            "result": "PASS",
            "log_path": "/home/ubuntu/work/legacy/log.txt",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["Canonical suite not exercised"],
        "merge_lane": "backend-api",
        "risk_level": "low",
        "next_action": "merge-ready",
        "marker": "AGY_TASK_RESULT_PACKET_OK",
    }
    normalized = normalize_agy_result_packet(legacy)
    assert normalized["issue_identifier"] == "GRO-4499"


def test_agy_completed_work_idempotent_conflict_error_preserved(tmp_path: Path):
    """Immutability invariant: same id with different content raises."""

    db = tmp_path / "agy.db"
    packet_a = {
        "agent": "agy",
        "issue_identifier": "GRO-4400",
        "branch": "feature/GRO-4400-a",
        "base_branch": "main",
        "changed_files": ["prismatic/cli/main.py"],
        "result_artifacts": ["artifacts/a.log"],
        "verification": {
            "commands": ["pytest"],
            "result": "PASS",
            "log_path": "artifacts/a.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["canonical"],
        "merge_lane": "backend-api",
        "risk_level": "low",
        "next_action": "merge-ready",
        "marker": "AGY_TASK_RESULT_PACKET_OK",
    }
    packet_b = json.loads(json.dumps(packet_a))
    packet_b["verification"]["commands"] = ["pytest tests/test_different.py"]

    ingest_completed_work(packet_a, db_path=db)
    with pytest.raises(AgyCompletedWorkConflictError):
        ingest_completed_work(packet_b, db_path=db)


def test_dual_artifact_validator_runs_from_cli(monkeypatch: pytest.MonkeyPatch):
    """Standalone CLI must keep working and never emit acceptance vocabulary."""
    import subprocess

    validator_script = (
        Path(__file__).resolve().parents[1]
        / "prismatic"
        / "skills"
        / "prismatic-agent-closeout-contract"
        / "scripts"
        / "validate_closeout_packet.py"
    )
    proc = subprocess.run(
        [
            "python",
            str(validator_script),
            str(EXAMPLES_DIR),
            "--test-fixtures",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STATUS=PASS" in proc.stdout
    assert "ACCEPTANCE_DECISION=CLEAN_STRUCTURE" not in proc.stdout
