from __future__ import annotations

import re
from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "agent-skill-packs" / "completed-work-skill-packs.md"
TEXT = DOC.read_text()


REQUIRED_SHARED_PACKS = [
    "shared/prismatic-completed-work-contract",
    "shared/prismatic-proof-packet",
    "shared/prismatic-non-claims",
    "shared/prismatic-safe-file-scope",
]

REQUIRED_AGENT_PACKS = [
    "agy/agy-structured-result-packet",
    "agy/agy-one-task-scope",
    "agy/agy-dashboard-work",
    "agy/agy-model-preflight",
    "fred/fred-clean-pr-builder",
    "fred/fred-verification-gate-runner",
    "fred/fred-deploy-proof",
    "george/george-dashboard-operator-audit",
    "kai/kai-prismatic-domain-review",
]


def test_shared_contract_exists() -> None:
    assert DOC.exists()
    assert "Canonical completed-work packet contract" in TEXT
    for field in ["agent", "source_path", "changed_files", "proof", "non_claims", "marker"]:
        assert f"`{field}`" in TEXT
    for pack in REQUIRED_SHARED_PACKS:
        assert pack in TEXT


def test_agy_packet_example_has_source_path() -> None:
    assert "### AGY structured result packet" in TEXT
    agy_section = TEXT.split("### AGY structured result packet", 1)[1].split("### Fred clean PR builder packet", 1)[0]
    assert '"agent": "agy"' in agy_section
    assert '"source_path"' in agy_section
    assert "AGY_STRUCTURED_RESULT_PACKET_OK" in agy_section


def test_proof_packet_example_has_command_result_log_scope_nonclaims_marker() -> None:
    assert "Proof packet contract" in TEXT
    for key in ["COMMAND=", "RESULT=", "LOG=", "SCOPE=", "NOT_CLAIMING=", "MARKER="]:
        assert key in TEXT
    canonical_json = TEXT.split("### Canonical JSON shape", 1)[1].split("## Proof packet contract", 1)[0]
    for key in ['"command"', '"result"', '"log"', '"scope"', '"non_claims"', '"marker"']:
        assert key in canonical_json


def test_non_claims_example_present() -> None:
    for claim in [
        "unbounded_overnight_autopilot",
        "auto_merge_enabled",
        "bulk_agy_dispatch",
        "production_deploy",
        "real_github_pr_created",
        "live_Linear_mutations_without_approval",
    ]:
        assert claim in TEXT


def test_agent_specific_skill_matrix_present() -> None:
    assert "Agent-specific skill packs" in TEXT
    for agent in ["AGY", "Fred", "George", "Kai"]:
        assert f"| {agent} |" in TEXT
    for pack in REQUIRED_AGENT_PACKS:
        assert pack in TEXT


def test_dispatch_preflight_and_writeback_language_present() -> None:
    for key in [
        "dispatch_preflight:",
        "skill_pack_state=loaded",
        "skill_pack_state=unavailable_or_not_reported",
        "packet_contract_version=prismatic-completed-work-v1",
        "packet_validation=passed",
        "packet_validation=required",
    ]:
        assert key in TEXT


def test_static_acceptance_booleans_present() -> None:
    for key in [
        "shared_contract_exists=true",
        "agy_packet_example_has_source_path=true",
        "proof_packet_example_has_command_result_log_scope_nonclaims_marker=true",
        "non_claims_example_present=true",
        "agent_specific_skill_matrix_present=true",
        "no_secrets_in_docs=true",
    ]:
        assert key in TEXT


def test_no_secrets_in_docs() -> None:
    forbidden_patterns = [
        r"ghp_[A-Za-z0-9_]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, TEXT), pattern
