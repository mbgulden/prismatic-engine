from __future__ import annotations

import pytest

from plugins.pwp.module_contracts import ModuleContractError, load_module_contracts, validate_module_contract
from plugins.pwp.module_fixture_renderer import render_all_fixtures, render_fixture


def test_all_module_contracts_validate_and_cover_phase_two_library() -> None:
    contracts = load_module_contracts()
    ids = {contract["id"] for contract in contracts}
    assert ids == {
        "base-layout",
        "site-header",
        "site-footer",
        "hero",
        "warning-strip",
        "card-grid",
        "trust-panel",
        "process-timeline",
        "split-section",
        "lead-capture",
        "faq",
        "local-seo-block",
        "testimonial-grid",
        "pricing-or-packages",
        "rich-text-page",
    }
    for contract in contracts:
        props = set(contract["propsSchema"]["properties"])
        assert set(contract["editableFields"]).issubset(props)
        assert contract["fixtures"], contract["id"]


def test_fixture_renderer_outputs_stable_module_markers() -> None:
    rendered = render_all_fixtures()
    assert len(rendered) == 15
    html_by_module = {fixture.module_id: fixture.html for fixture in rendered}
    assert 'data-pwp-module="hero"' in html_by_module["hero"]
    assert 'data-component="Hero.astro"' in html_by_module["hero"]
    assert 'data-variant="split-media"' in html_by_module["hero"]
    assert "<h1>Secure IT asset recovery</h1>" in html_by_module["hero"]
    assert 'data-pwp-module="lead-capture"' in html_by_module["lead-capture"]


def test_unknown_fixture_variant_is_rejected() -> None:
    contract = next(contract for contract in load_module_contracts() if contract["id"] == "hero")
    bad_fixture = {"name": "bad", "variant": "interpretive-dance", "props": contract["fixtures"][0]["props"]}
    with pytest.raises(ModuleContractError, match="unknown fixture variant"):
        render_fixture(contract, bad_fixture)


def test_contract_rejects_editable_field_without_prop_definition() -> None:
    contract = next(contract for contract in load_module_contracts() if contract["id"] == "card-grid")
    broken = {**contract, "editableFields": [*contract["editableFields"], "inventedField"]}
    with pytest.raises(ModuleContractError, match="editable fields missing prop definitions"):
        validate_module_contract(broken)
