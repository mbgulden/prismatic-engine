"""Tests for Prismatic Engine Sovereign Archetypes & Manifestation Contracts."""

import pytest
from prismatic.swarm.archetypes import (
    Archetype,
    ArchetypeRegistry,
)


@pytest.fixture(autouse=True)
def _pin_agent_roster(monkeypatch):
    """Pin the dynamic agent roster to deterministic test doubles.

    Production resolves role -> agent via AgentDiscoveryService (which finds
    nothing in CI, so _resolve_agent_for_role falls back to generic ids).
    Pin the fallbacks directly so the contract-structure assertions below
    are hermetic and independent of whatever the discovery service sees.
    """

    def _fake_resolve(cls, preferred_id, fallback_id):
        return {
            "content": "content_specialist",
            "compiler": "compiler_engineer",
            "deployer": "deployer_engineer",
            "reviewer": "review_sentinel",
            "orchestrator": "fleet_orchestrator",
        }[preferred_id]

    monkeypatch.setattr(
        ArchetypeRegistry, "_resolve_agent_for_role", classmethod(_fake_resolve)
    )


def test_archetype_detection():
    # Web Property
    assert (
        ArchetypeRegistry.detect_archetype(
            "Build modern developer tooling landing page and web portal"
        )
        == Archetype.WEB_PROPERTY
    )
    assert (
        ArchetypeRegistry.detect_archetype(
            "Build high-converting storefront with Stripe checkout and product catalog"
        )
        == Archetype.WEB_PROPERTY
    )
    assert (
        ArchetypeRegistry.detect_archetype(
            "Deploy responsive Astro website with interactive code sandbox"
        )
        == Archetype.WEB_PROPERTY
    )

    # Operations
    assert (
        ArchetypeRegistry.detect_archetype(
            "Draft operating agreement and legal incorporation for LLC"
        )
        == Archetype.OPERATIONS
    )
    assert (
        ArchetypeRegistry.detect_archetype(
            "Establish pricing tiers, merchant agreements, and compliance matrix"
        )
        == Archetype.OPERATIONS
    )

    # Publication
    assert (
        ArchetypeRegistry.detect_archetype(
            "Write comprehensive research report and whitepaper on AI agent governance"
        )
        == Archetype.PUBLICATION
    )
    assert (
        ArchetypeRegistry.detect_archetype(
            "Produce book manuscript and SEO content calendar"
        )
        == Archetype.PUBLICATION
    )

    # Software (default)
    assert (
        ArchetypeRegistry.detect_archetype(
            "Implement FastAPI backend with SQLite WAL and unit tests"
        )
        == Archetype.SOFTWARE
    )


def test_archetype_decomposition_web_property():
    prompt = "Launch high-converting developer documentation and tooling portal with subscription billing"
    contracts = ArchetypeRegistry.decompose(
        prompt, archetype=Archetype.WEB_PROPERTY, task_id="GRO-4854"
    )

    assert len(contracts) == 4
    agents = [c.agent_id for c in contracts]

    assert "content_specialist" in agents
    assert "compiler_engineer" in agents
    assert "deployer_engineer" in agents
    assert "review_sentinel" in agents

    # Check content contract
    content_contract = next(c for c in contracts if c.agent_id == "content_specialist")
    assert "hero_copy" in content_contract.deliverable_keys

    # Check web compiler contract
    compiler_contract = next(c for c in contracts if c.agent_id == "compiler_engineer")
    assert "site_html" in compiler_contract.deliverable_keys


def test_archetype_decomposition_software():
    prompt = "Build sovereign Hypervisor microkernel with Merkle DAG ledger"
    contracts = ArchetypeRegistry.decompose(
        prompt, archetype=Archetype.SOFTWARE, task_id="GRO-4854"
    )

    assert len(contracts) == 2
    agents = [c.agent_id for c in contracts]
    assert "compiler_engineer" in agents
    assert "review_sentinel" in agents
