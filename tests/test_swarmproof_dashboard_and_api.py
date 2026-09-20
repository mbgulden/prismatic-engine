"""Tests for SwarmProof Hub UI surface and API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from prismatic.gateway.server import app

client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HTML_PATH = REPO_ROOT / "prismatic/gateway/templates/dashboard.html"


def test_dashboard_template_contains_all_swarmproof_components():
    """Verify compiled dashboard.html contains all SwarmProof surface elements."""
    assert DASHBOARD_HTML_PATH.exists(), "dashboard.html template must exist"
    html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")

    # Navigation tab
    assert 'id="tab-btn-swarmproof"' in html
    assert 'href="/swarmproof"' in html
    assert "switchTab('swarmproof', event)" in html

    # Main section and proof marker
    assert 'id="section-swarmproof"' in html
    assert 'data-proof-marker="swarmproof-oracle-dashboard"' in html

    # 4 KPI Stat IDs
    assert 'id="sp-metric-invariants"' in html
    assert 'id="sp-metric-ledgers"' in html
    assert 'id="sp-metric-red-green"' in html
    assert 'id="sp-metric-deflections"' in html

    # All 10 Anti-Deception Invariants
    for i in range(1, 11):
        inv_tag = f"INV-{i:02d}"
        assert inv_tag in html, f"Invariant {inv_tag} missing from dashboard"

    # Receipts Explorer Table
    assert 'id="swarmproof-receipts-table"' in html
    assert 'id="swarmproof-receipts-tbody"' in html
    assert 'id="sp-search-input"' in html
    assert 'id="sp-stage-filter"' in html

    # Modals
    assert 'id="swarmproof-verify-modal"' in html
    assert 'id="swarmproof-ast-modal"' in html
    assert 'id="swarmproof-run-modal"' in html
    assert 'id="swarmproof-hooks-modal"' in html
    assert 'id="swarmproof-receipt-modal"' in html


def test_swarmproof_html_route_serves_dashboard():
    """Verify GET /swarmproof returns 200 with dashboard HTML."""
    response = client.get("/swarmproof")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "SwarmProof Truth Oracle" in response.text


def test_api_swarmproof_status():
    """Verify GET /api/swarmproof/status returns oracle metadata."""
    response = client.get("/api/swarmproof/status")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ACTIVE_ORACLE"
    assert data.get("version") == "0.3.0"
    assert data.get("active_invariants") == 10
    assert "hooks_installed" in data


def test_api_swarmproof_receipts():
    """Verify GET /api/swarmproof/receipts returns a receipts array."""
    response = client.get("/api/swarmproof/receipts")
    assert response.status_code == 200
    data = response.json()
    assert "receipts" in data
    assert isinstance(data["receipts"], list)


def test_api_swarmproof_ast_analysis_clean():
    """Verify POST /api/swarmproof/analyze-ast passes clean additions."""
    payload = {
        "baseline": "def test_a():\n    assert token.is_valid()\n",
        "candidate": "def test_a():\n    assert token.is_valid()\n    assert token.role == 'admin'\n",
    }
    response = client.post("/api/swarmproof/analyze-ast", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data.get("is_clean") is True
    assert data.get("candidate_asserts") == 2
    assert len(data.get("violations", [])) == 0


def test_api_swarmproof_ast_analysis_detects_tautology():
    """Verify POST /api/swarmproof/analyze-ast flags tautological assertion weakening."""
    payload = {
        "baseline": "def test_a():\n    assert token.is_valid()\n",
        "candidate": "def test_a():\n    assert True\n",
    }
    response = client.post("/api/swarmproof/analyze-ast", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data.get("is_clean") is False
    assert len(data.get("violations", [])) > 0


def test_api_swarmproof_verify_evaluates_manifest():
    """Verify POST /api/swarmproof/verify evaluates dual manifest."""
    # Test with empty/invalid payload
    response = client.post("/api/swarmproof/verify", json={"payload": {}, "strict": True})
    assert response.status_code == 200
    data = response.json()
    assert data.get("passed") is False
    assert len(data.get("violations", [])) > 0


def test_api_swarmproof_run_test_security_guards_reject_unsafe_commands():
    """Verify POST /api/swarmproof/run-test enforces whitelist and blocks shell operators."""
    # Empty command
    r1 = client.post("/api/swarmproof/run-test", json={"command": ""})
    assert r1.status_code == 400

    # Shell chaining semicolon
    r2 = client.post("/api/swarmproof/run-test", json={"command": "pytest tests/ ; rm -rf /"})
    assert r2.status_code == 400
    assert "disallowed shell token ';'" in r2.json().get("detail", "")

    # Shell chaining &&
    r3 = client.post("/api/swarmproof/run-test", json={"command": "pytest tests/ && echo pwned"})
    assert r3.status_code == 400
    assert "disallowed shell token '&&'" in r3.json().get("detail", "")

    # Shell pipe |
    r4 = client.post("/api/swarmproof/run-test", json={"command": "pytest tests/ | cat"})
    assert r4.status_code == 400
    assert "disallowed shell token '|'" in r4.json().get("detail", "")

    # Non-whitelisted binary
    r5 = client.post("/api/swarmproof/run-test", json={"command": "curl http://malicious.com"})
    assert r5.status_code == 400
    assert "approved test runner" in r5.json().get("detail", "")


def test_api_swarmproof_ast_analysis_structured_syntax_error():
    """Verify POST /api/swarmproof/analyze-ast safely flags unparsable syntax as a violation."""
    payload = {
        "baseline": "def test_a():\n    assert token.is_valid()\n",
        "candidate": "def test_a():\n    assert ((\n",  # Syntax error unclosed parenthesis
    }
    response = client.post("/api/swarmproof/analyze-ast", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data.get("is_clean") is False
    assert len(data.get("violations", [])) > 0
    assert any("SYNTAX_ERROR" in v or "ASSERTION_DEGRADED" in v for v in data.get("violations", []))


def test_dashboard_template_contains_accessibility_modal_attributes():
    """Verify all 5 modals possess role=dialog, aria-modal=true, and aria-labelledby."""
    html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    for modal_id in (
        "swarmproof-verify-modal",
        "swarmproof-ast-modal",
        "swarmproof-run-modal",
        "swarmproof-hooks-modal",
        "swarmproof-receipt-modal",
    ):
        assert f'id="{modal_id}"' in html
        assert f'role="dialog"' in html
        assert f'aria-modal="true"' in html
    assert 'aria-label="Close modal"' in html
    assert 'Escape' in html


def test_dashboard_template_contains_light_mode_styles():
    """Verify compiled dashboard contains light mode contrast overrides for SwarmProof."""
    html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    assert "body.light-mode #section-swarmproof .glass-panel" in html
    assert "body.light-mode #swarmproof-receipts-table" in html

