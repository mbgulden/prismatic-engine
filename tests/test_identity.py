"""
tests/test_identity.py — Unit tests for prismatic/identity.py

Covers:
- AgentIdentity construction, serialization, status transitions
- IdentityRegistry loading, lookup, scope checking
- Edge cases: unknown keys in config, unknown status fallback
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from prismatic.identity import (
    AgentIdentity,
    AgentStatus,
    IdentityRegistry,
)


# ═══════════════════════════════════════════════════════════════════
# AgentIdentity
# ═══════════════════════════════════════════════════════════════════


class TestAgentIdentity:
    def test_minimal_construction(self) -> None:
        ident = AgentIdentity(agent_id="ned")
        assert ident.agent_id == "ned"
        assert ident.git_email == ""
        assert ident.status == AgentStatus.IDLE
        assert ident.credential_scopes == []

    def test_full_construction(self) -> None:
        ident = AgentIdentity(
            agent_id="fred",
            git_email="fred@prismatic.internal",
            signing_key_id="key_fred_prod_01",
            credential_scopes=["prismatic-engine", "active-oahu-static"],
            status=AgentStatus.ACTIVE,
            metadata={"lane": "orchestrator"},
        )
        assert ident.git_email == "fred@prismatic.internal"
        assert ident.is_active
        assert ident.credential_scopes == ["prismatic-engine", "active-oahu-static"]

    def test_from_dict(self) -> None:
        raw = {
            "agent_id": "kai",
            "git_email": "kai@prismatic.internal",
            "status": "active",
            "credential_scopes": ["content"],
            "team": "writers",  # unknown — goes to metadata
        }
        ident = AgentIdentity.from_dict(raw)
        assert ident.agent_id == "kai"
        assert ident.git_email == "kai@prismatic.internal"
        assert ident.is_active
        assert ident.metadata.get("team") == "writers"

    def test_from_dict_kebab_keys(self) -> None:
        raw = {
            "agent_id": "ned",
            "signing-key-id": "key_ned_prod_01",
            "git-email": "ned@prismatic.internal",
        }
        ident = AgentIdentity.from_dict(raw)
        assert ident.signing_key_id == "key_ned_prod_01"
        assert ident.git_email == "ned@prismatic.internal"

    def test_status_transitions(self) -> None:
        ident = AgentIdentity(agent_id="test")
        assert ident.status == AgentStatus.IDLE

        ident.activate()
        assert ident.is_active
        assert not ident.is_suspended

        ident.suspend()
        assert ident.is_suspended
        assert not ident.is_active

        ident.mark_idle()
        assert ident.status == AgentStatus.IDLE
        assert not ident.is_active
        assert not ident.is_suspended

    def test_scope_all_access(self) -> None:
        """Empty credential_scopes means all workspaces."""
        ident = AgentIdentity(agent_id="admin")
        assert ident.has_scope("any-workspace")
        assert ident.has_scope("another-one")

    def test_scope_restricted(self) -> None:
        ident = AgentIdentity(
            agent_id="kai",
            credential_scopes=["content", "active-oahu-static"],
        )
        assert ident.has_scope("content")
        assert ident.has_scope("active-oahu-static")
        assert not ident.has_scope("prismatic-engine")
        assert not ident.has_scope("internal-admin")

    def test_to_dict(self) -> None:
        ident = AgentIdentity(
            agent_id="fred",
            git_email="fred@prismatic.internal",
            signing_key_id="key_fred_prod_01",
            credential_scopes=["prismatic-engine"],
            status=AgentStatus.ACTIVE,
            metadata={"foo": "bar"},
        )
        d = ident.to_dict()
        assert d["agent_id"] == "fred"
        assert d["status"] == "active"
        assert d["metadata"] == {"foo": "bar"}


# ═══════════════════════════════════════════════════════════════════
# AgentStatus
# ═══════════════════════════════════════════════════════════════════


class TestAgentStatus:
    def test_known_values(self) -> None:
        assert AgentStatus("active") == AgentStatus.ACTIVE
        assert AgentStatus("idle") == AgentStatus.IDLE
        assert AgentStatus("suspended") == AgentStatus.SUSPENDED

    def test_unknown_falls_back_to_idle(self) -> None:
        status = AgentStatus("unknown_value")
        assert status == AgentStatus.IDLE


# ═══════════════════════════════════════════════════════════════════
# IdentityRegistry
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_identities() -> list[AgentIdentity]:
    return [
        AgentIdentity(
            agent_id="fred",
            git_email="fred@prismatic.internal",
            signing_key_id="key_fred_prod_01",
            credential_scopes=["prismatic-engine", "active-oahu-static"],
            status=AgentStatus.ACTIVE,
        ),
        AgentIdentity(
            agent_id="ned",
            git_email="ned@prismatic.internal",
            signing_key_id="key_ned_prod_01",
            credential_scopes=["prismatic-engine"],
            status=AgentStatus.ACTIVE,
        ),
        AgentIdentity(
            agent_id="kai",
            git_email="kai@prismatic.internal",
            credential_scopes=["content", "active-oahu-static"],
            status=AgentStatus.IDLE,
        ),
    ]


@pytest.fixture
def sample_config() -> dict:
    return {
        "unified_identities": [
            {
                "agent_id": "fred",
                "git_email": "fred@prismatic.internal",
                "signing_key_id": "key_fred_prod_01",
                "credential_scopes": ["prismatic-engine", "active-oahu-static"],
                "status": "active",
            },
            {
                "agent_id": "ned",
                "git_email": "ned@prismatic.internal",
                "signing_key_id": "key_ned_prod_01",
                "credential_scopes": ["prismatic-engine"],
                "status": "active",
            },
            {
                "agent_id": "kai",
                "git_email": "kai@prismatic.internal",
                "credential_scopes": ["content", "active-oahu-static"],
                "status": "idle",
            },
        ]
    }


@pytest.fixture
def registry(sample_identities: list[AgentIdentity]) -> IdentityRegistry:
    return IdentityRegistry(sample_identities)


class TestIdentityRegistryConstruction:
    def test_empty(self) -> None:
        reg = IdentityRegistry()
        assert len(reg) == 0
        assert reg.identity_ids == []

    def test_with_list(self, registry: IdentityRegistry) -> None:
        assert len(registry) == 3
        assert "fred" in registry
        assert "ned" in registry
        assert "kai" in registry

    def test_from_config(self, sample_config: dict) -> None:
        reg = IdentityRegistry.from_config(sample_config)
        assert len(reg) == 3
        assert reg.get("fred") is not None
        assert reg.get("fred").git_email == "fred@prismatic.internal"

    def test_from_config_invalid_type(self) -> None:
        with pytest.raises(TypeError, match="must be a list"):
            IdentityRegistry.from_config({"unified_identities": "not-a-list"})

    def test_from_config_missing_key(self) -> None:
        reg = IdentityRegistry.from_config({})
        assert len(reg) == 0

    def test_from_yaml(self, sample_config: dict) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(sample_config, f)
            tmp_path = f.name
        try:
            reg = IdentityRegistry.from_yaml(tmp_path)
            assert len(reg) == 3
            assert "fred" in reg
        finally:
            os.unlink(tmp_path)

    def test_from_yaml_empty(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            tmp_path = f.name
        try:
            reg = IdentityRegistry.from_yaml(tmp_path)
            assert len(reg) == 0
        finally:
            os.unlink(tmp_path)

    def test_contains(self, registry: IdentityRegistry) -> None:
        assert "fred" in registry
        assert "nobody" not in registry

    def test_iteration(self, registry: IdentityRegistry) -> None:
        ids = [i.agent_id for i in registry]
        assert sorted(ids) == ["fred", "kai", "ned"]


class TestIdentityRegistryLookup:
    def test_get_found(self, registry: IdentityRegistry) -> None:
        ident = registry.get("fred")
        assert ident is not None
        assert ident.agent_id == "fred"

    def test_get_not_found(self, registry: IdentityRegistry) -> None:
        assert registry.get("nobody") is None

    def test_require_found(self, registry: IdentityRegistry) -> None:
        ident = registry.require("ned")
        assert ident.agent_id == "ned"

    def test_require_not_found(self, registry: IdentityRegistry) -> None:
        with pytest.raises(KeyError, match="Unknown agent"):
            registry.require("nobody")


class TestIdentityRegistryStatusFilter:
    def test_active_identities(self, registry: IdentityRegistry) -> None:
        active = registry.active_identities()
        ids = sorted(i.agent_id for i in active)
        assert ids == ["fred", "ned"]

    def test_get_by_status(self, registry: IdentityRegistry) -> None:
        idle = registry.get_by_status(AgentStatus.IDLE)
        assert len(idle) == 1
        assert idle[0].agent_id == "kai"

    def test_get_by_suspended(self, registry: IdentityRegistry) -> None:
        suspended = registry.get_by_status(AgentStatus.SUSPENDED)
        assert len(suspended) == 0


class TestIdentityRegistryScope:
    def test_agent_can_access_allowed_workspace(
        self, registry: IdentityRegistry
    ) -> None:
        assert registry.can_access_workspace(
            "fred", "prismatic-engine"
        )
        assert registry.can_access_workspace(
            "kai", "active-oahu-static"
        )

    def test_agent_cannot_access_restricted_workspace(
        self, registry: IdentityRegistry
    ) -> None:
        assert not registry.can_access_workspace(
            "kai", "prismatic-engine"
        )

    def test_unknown_agent_returns_false(
        self, registry: IdentityRegistry
    ) -> None:
        assert not registry.can_access_workspace(
            "nobody", "prismatic-engine"
        )

    def test_idle_agent_can_still_access(
        self, registry: IdentityRegistry
    ) -> None:
        """Status doesn't affect scope check — scope is about permission,
        not current availability."""
        assert registry.can_access_workspace("kai", "content")


class TestIdentityRegistryEdgeCases:
    def test_identity_ids_sorted(self, registry: IdentityRegistry) -> None:
        ids = registry.identity_ids
        assert ids == sorted(ids)

    def test_duplicate_agent_id_last_wins(self) -> None:
        identities = [
            AgentIdentity(agent_id="dup", git_email="first@example.com"),
            AgentIdentity(agent_id="dup", git_email="second@example.com"),
        ]
        reg = IdentityRegistry(identities)
        assert reg.get("dup").git_email == "second@example.com"
