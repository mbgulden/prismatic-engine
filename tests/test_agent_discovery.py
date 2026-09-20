"""Tests for Prismatic Engine Dynamic Agent Discovery Service."""

import time
import pytest
from prismatic.agents.discovery import AgentDiscoveryService, AgentProfile, EPHEMERAL_TEST_AGENT_REGEX


def test_agent_discovery_basic():
    """Test that dynamic agent discovery discovers agents from host environment."""
    AgentDiscoveryService.invalidate_cache()
    agents = AgentDiscoveryService.get_agents()

    assert len(agents) > 0
    for a in agents:
        assert isinstance(a, AgentProfile)
        assert a.agent_id
        assert a.name
        assert a.role
        assert a.host
        assert a.status in {"idle", "active", "online", "busy", "offline", "unknown"}
        assert not EPHEMERAL_TEST_AGENT_REGEX.match(a.agent_id)


def test_agent_discovery_ttl_caching():
    """Test that in-memory TTL caching prevents redundant disk scans."""
    AgentDiscoveryService.invalidate_cache()
    AgentDiscoveryService.set_ttl(5.0)

    # First call - cache miss
    first_agents = AgentDiscoveryService.get_agents()
    assert first_agents is not None

    # Second call - cache hit (same object list)
    second_agents = AgentDiscoveryService.get_agents()
    assert len(first_agents) == len(second_agents)

    # Explicit invalidation
    AgentDiscoveryService.invalidate_cache()
    refreshed_agents = AgentDiscoveryService.get_agents()
    assert len(refreshed_agents) == len(first_agents)


def test_agent_discovery_get_agent_and_role_lookup():
    """Test finding agents by ID and role keyword."""
    AgentDiscoveryService.invalidate_cache()
    agents = AgentDiscoveryService.get_agents()
    assert len(agents) > 0

    first_agent = agents[0]
    found = AgentDiscoveryService.get_agent(first_agent.agent_id)
    assert found is not None
    assert found.agent_id == first_agent.agent_id

    # Non-existent agent returns None
    assert AgentDiscoveryService.get_agent("non_existent_super_agent_xyz_999") is None

    # Role matching returns matching agent or None
    role_match = AgentDiscoveryService.find_agent_for_role(first_agent.agent_id)
    assert role_match is not None
    assert role_match.agent_id == first_agent.agent_id


def test_agent_discovery_to_dict_contract():
    """Test dict output contract for dashboard APIs."""
    agents_dict = AgentDiscoveryService.get_agents_dict()
    assert isinstance(agents_dict, dict)
    assert len(agents_dict) > 0

    for aid, data in agents_dict.items():
        assert "agent_id" in data
        assert "name" in data
        assert "role" in data
        assert "active_model" in data
        assert "capabilities" in data
        assert "icon" in data
