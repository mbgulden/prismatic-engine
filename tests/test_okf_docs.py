from scripts.validate_okf_docs import ROOT, validate
import json


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
    } <= ids


def test_dashboard_is_a_read_model_not_underlying_truth():
    text = (ROOT / "docs/governance/source-of-truth-order.md").read_text()
    assert "Read models and controls over durable stores" in text
    assert "API/dashboard/Telegram views" in text
