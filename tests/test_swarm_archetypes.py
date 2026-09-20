"""Tests for Prismatic Engine Sovereign Archetypes & Manifestation Contracts."""

import pytest
from prismatic.swarm.archetypes import (
    Archetype,
    ArchetypeContract,
    ArchetypeRegistry,
    ProjectDeliverable,
)


def test_archetype_detection():
    # Web Property
    assert ArchetypeRegistry.detect_archetype("Build modern developer tooling landing page and web portal") == Archetype.WEB_PROPERTY
    assert ArchetypeRegistry.detect_archetype("Build high-converting storefront with Stripe checkout and product catalog") == Archetype.WEB_PROPERTY
    assert ArchetypeRegistry.detect_archetype("Deploy responsive Astro website with interactive code sandbox") == Archetype.WEB_PROPERTY

    # Operations
    assert ArchetypeRegistry.detect_archetype("Draft operating agreement and legal incorporation for LLC") == Archetype.OPERATIONS
    assert ArchetypeRegistry.detect_archetype("Establish pricing tiers, merchant agreements, and compliance matrix") == Archetype.OPERATIONS

    # Publication
    assert ArchetypeRegistry.detect_archetype("Write comprehensive research report and whitepaper on AI agent governance") == Archetype.PUBLICATION
    assert ArchetypeRegistry.detect_archetype("Produce book manuscript and SEO content calendar") == Archetype.PUBLICATION

    # Software (default)
    assert ArchetypeRegistry.detect_archetype("Implement FastAPI backend with SQLite WAL and unit tests") == Archetype.SOFTWARE


def test_archetype_decomposition_web_property():
    prompt = "Launch high-converting developer documentation and tooling portal with subscription billing"
    contracts = ArchetypeRegistry.decompose(prompt, archetype=Archetype.WEB_PROPERTY, task_id="GRO-4854")

    assert len(contracts) == 4
    roles = [c.role for c in contracts]
    agents = [c.agent_id for c in contracts]

    assert "kai" in agents
    assert "ned" in agents
    assert "autobot" in agents
    assert "george" in agents

    # Check content contract
    kai_contract = next(c for c in contracts if c.agent_id == "kai")
    assert "hero_copy" in kai_contract.deliverable_keys

    # Check web compiler contract
    ned_contract = next(c for c in contracts if c.agent_id == "ned")
    assert "site_html" in ned_contract.deliverable_keys


def test_archetype_decomposition_software():
    prompt = "Build sovereign Hypervisor microkernel with Merkle DAG ledger"
    contracts = ArchetypeRegistry.decompose(prompt, archetype=Archetype.SOFTWARE, task_id="GRO-4854")

    assert len(contracts) == 2
    agents = [c.agent_id for c in contracts]
    assert "ned" in agents
    assert "george" in agents
