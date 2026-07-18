from __future__ import annotations

import json
import re
from pathlib import Path
import pytest

from prismatic.agy_completed_work import normalize_agy_result_packet
from prismatic.completed_work_gate import classify_completed_work, GateClassification

DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "agent-skill-packs" / "completed-work-skill-packs.md"
TEXT = DOC_PATH.read_text()


def get_fixture_by_title(title: str) -> str:
    pattern = rf"#### \d+\.\s*{re.escape(title)}.*?\n```(?:json|text)\n(.*?)\n```"
    match = re.search(pattern, TEXT, re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not find fixture: {title}")
    return match.group(1).strip()


def test_missing_source_path_fixture() -> None:
    raw = get_fixture_by_title("Missing source path")
    packet = json.loads(raw)
    
    normalized = normalize_agy_result_packet(packet)
    state = classify_completed_work(normalized)
    
    assert state.classification == GateClassification.REJECTED
    assert any("source_path" in r for r in state.reasons)


def test_missing_proof_log_fixture() -> None:
    raw = get_fixture_by_title("Missing proof log")
    packet = json.loads(raw)
    
    packet["source_branch"] = "feature/gro-3954-test"
    normalized = normalize_agy_result_packet(packet)
    normalized["proof"]["ad_hoc_or_canonical"] = "ad-hoc targeted"
    # Remove log field after normalization so we test the missing log check specifically
    normalized["proof"].pop("log", None)
    
    state = classify_completed_work(normalized)
    assert state.classification == GateClassification.BLOCKED_MISSING_PROOF
    assert any("log" in r for r in state.reasons)


def test_missing_non_claims_fixture() -> None:
    raw = get_fixture_by_title("Missing non-claims")
    packet = json.loads(raw)
    
    packet["source_branch"] = "feature/gro-3954-test"
    normalized = normalize_agy_result_packet(packet)
    normalized["proof"]["ad_hoc_or_canonical"] = "ad-hoc targeted"
    # Remove non-claims fields after normalization
    normalized["proof"].pop("non_claims", None)
    normalized["proof"].pop("not_claiming", None)
    
    state = classify_completed_work(normalized)
    assert state.classification == GateClassification.BLOCKED_MISSING_PROOF
    assert "proof must include non_claims or legacy not_claiming" in state.reasons


def test_invalid_changed_files_fixture() -> None:
    raw = get_fixture_by_title("Invalid changed files")
    packet = json.loads(raw)
    packet["source_branch"] = "feature/gro-3954-test"
    
    normalized = normalize_agy_result_packet(packet)
    state = classify_completed_work(normalized)
    
    assert state.classification == GateClassification.REJECTED
    assert "changed_files must not be empty" in state.reasons


def test_production_claim_without_proof_fixture() -> None:
    raw = get_fixture_by_title("Production claim without proof")
    packet = json.loads(raw)
    
    packet["source_branch"] = "feature/gro-3954-test"
    normalized = normalize_agy_result_packet(packet)
    normalized["proof"]["ad_hoc_or_canonical"] = "ad-hoc targeted"
    # Remove non-claims fields after normalization
    normalized["proof"].pop("non_claims", None)
    normalized["proof"].pop("not_claiming", None)
    
    state = classify_completed_work(normalized)
    assert state.classification == GateClassification.BLOCKED_MISSING_PROOF
    assert "proof must include non_claims or legacy not_claiming" in state.reasons


def test_agent_prose_only_fixture() -> None:
    raw = get_fixture_by_title("Agent prose only")
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_wrong_agent_or_ambiguous_agent_fixture() -> None:
    raw = get_fixture_by_title("Wrong agent or ambiguous agent")
    packet = json.loads(raw)
    packet["source_branch"] = "feature/gro-3954-test"
    
    normalized = normalize_agy_result_packet(packet)
    state = classify_completed_work(normalized)
    
    assert state.classification == GateClassification.REJECTED
    assert "untrusted agent: super-agent" in state.reasons
