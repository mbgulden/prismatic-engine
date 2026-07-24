from copy import deepcopy

import json

from scripts.validate_okf_docs import ROOT, _parity_errors, _schema_errors, validate


def test_canonical_okf_documentation_contract():
    assert validate() == []


def test_registry_covers_agent_and_system_verification():
    data = json.loads((ROOT / "okf/index.yaml").read_text())
    ids = {x["id"] for x in data["objectives"]}
    assert {
        "verified-agent-output",
        "systemic-orchestration-correctness",
        "canonical-knowledge",
        "sustainable-maintainability",
        "provider-neutral-verification",
    } <= ids


def test_dashboard_is_a_read_model_not_underlying_truth():
    text = (ROOT / "docs/governance/source-of-truth-order.md").read_text()
    assert "Read models and controls over durable stores" in text
    assert "API/dashboard/Telegram views" in text


def test_provider_neutral_verification_policy_is_canonical():
    architecture = (ROOT / "docs/architecture/verification-engine.md").read_text()
    contract = (ROOT / "docs/contracts/verification-contract.md").read_text()
    decision = (
        ROOT / "docs/decisions/ADR-0002-provider-neutral-verification-receipts.md"
    ).read_text()
    assert "GitHub Actions is one approved backend" in architecture
    assert "A local repository or Git bundle is an acquisition form" in architecture
    assert "no particular Git provider is mandatory" in contract
    assert "**must** emit the same versioned receipt shape" in contract
    assert "not a claim that every adapter/backend already exists" in contract
    assert (
        "independently verified, exact-head, clean-room verification receipt"
        in decision
    )
    assert "GRO-4203" in decision


def test_okf_schema_validation_fails_closed():
    registry = json.loads((ROOT / "okf/index.yaml").read_text())
    schema = json.loads((ROOT / "okf/schemas/okf.schema.json").read_text())
    invalid = deepcopy(registry)
    del invalid["objectives"][0]["owner"]
    errors = _schema_errors(invalid, schema)
    assert errors
    assert any("owner" in error for error in errors)


def test_okf_human_machine_parity_rejects_system_of_record_drift():
    registry = json.loads((ROOT / "okf/index.yaml").read_text())
    evidence_map = (ROOT / "docs/okf-evidence-map.md").read_text()
    assert _parity_errors(registry, evidence_map) == []
    drifted = evidence_map.replace(
        "Git object identity + versioned verification receipts + Merge Factory attestations",
        "GitHub status",
        1,
    )
    errors = _parity_errors(registry, drifted)
    assert "verified-agent-output: parity system_of_record mismatch" in errors
