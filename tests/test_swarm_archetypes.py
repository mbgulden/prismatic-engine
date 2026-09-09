"""Tests for Prismatic Engine Sovereign Archetypes & Manifestation Contracts."""

import pytest
from prismatic.swarm.archetypes import (
    Archetype,
    ArchetypeContract,
    ArchetypeRegistry,
    ProjectDeliverable,
)
from prismatic.swarm.manifest_oahu import generate_active_oahu_deliverable


def test_archetype_detection():
    # Web Property
    assert ArchetypeRegistry.detect_archetype("Launch self-serve kayak and SUP rentals at 134B Hamakua Dr") == Archetype.WEB_PROPERTY
    assert ArchetypeRegistry.detect_archetype("Build high-converting landing page for coffee roasters with Stripe checkout") == Archetype.WEB_PROPERTY
    assert ArchetypeRegistry.detect_archetype("Replace activeoahu.com with modern Astro site") == Archetype.WEB_PROPERTY

    # Operations
    assert ArchetypeRegistry.detect_archetype("Draft operating agreement and legal incorporation for LLC") == Archetype.OPERATIONS
    assert ArchetypeRegistry.detect_archetype("Establish pricing tiers, merchant agreements, and compliance matrix") == Archetype.OPERATIONS

    # Publication
    assert ArchetypeRegistry.detect_archetype("Write comprehensive research report and whitepaper on AI agent governance") == Archetype.PUBLICATION
    assert ArchetypeRegistry.detect_archetype("Produce book manuscript and SEO content calendar") == Archetype.PUBLICATION

    # Software (default)
    assert ArchetypeRegistry.detect_archetype("Implement FastAPI backend with SQLite WAL and unit tests") == Archetype.SOFTWARE


def test_archetype_decomposition_web_property():
    prompt = "Launch high-converting self-serve rental booking platform for Oahu kayaks at 134B Hamakua Dr"
    contracts = ArchetypeRegistry.decompose(prompt, archetype=Archetype.WEB_PROPERTY, task_id="GRO-4854")

    assert len(contracts) == 4
    roles = [c.role for c in contracts]
    agents = [c.agent_id for c in contracts]

    assert "kai" in agents
    assert "ned" in agents
    assert "autobot" in agents
    assert "george" in agents

    # Check Kai's contract
    kai_contract = next(c for c in contracts if c.agent_id == "kai")
    assert "Hawaiian" in kai_contract.task_description or "diacritics" in kai_contract.task_description
    assert "hero_copy" in kai_contract.deliverable_keys

    # Check Ned's contract
    ned_contract = next(c for c in contracts if c.agent_id == "ned")
    assert "Astro" in ned_contract.task_description or "HTML" in ned_contract.task_description
    assert "stripe_config" in ned_contract.deliverable_keys


def test_archetype_decomposition_software():
    prompt = "Build sovereign Hypervisor microkernel with Merkle DAG ledger"
    contracts = ArchetypeRegistry.decompose(prompt, archetype=Archetype.SOFTWARE, task_id="GRO-4854")

    assert len(contracts) == 2
    agents = [c.agent_id for c in contracts]
    assert "ned" in agents
    assert "george" in agents


def test_active_oahu_canonical_deliverable():
    deliv = generate_active_oahu_deliverable()

    assert deliv.project_slug == "active-oahu"
    assert deliv.archetype == Archetype.WEB_PROPERTY
    assert "134B Hamakua Dr" in deliv.location
    assert deliv.target_domain == "activeoahu.growthwebdev.com"

    artifacts = deliv.artifacts
    assert "html_bundle" in artifacts
    assert "134B Hamakua Dr" in artifacts["html_bundle"]
    assert "Mokulua" in artifacts["html_bundle"]

    schema = artifacts["schema_ld"]
    assert schema["@type"] == "SportsActivityLocation"
    assert schema["address"]["streetAddress"] == "134B Hamakua Dr"
    assert schema["geo"]["latitude"] == 21.3938

    products = artifacts["stripe_products"]
    assert len(products) >= 4
    single_kayak = next(p for p in products if "Single Ocean Kayak" in p["name"])
    assert single_kayak["amount_cents"] == 4500
