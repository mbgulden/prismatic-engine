"""
prismatic/identity.py — Agent identity registry scaffolding.

Defines agent identity models, credential scope configuration, and
status tracking (active / idle / suspended). Provides a registry
that can be loaded from the ``unified_identities`` section of the
PRISMATIC_ENGINE.yaml config and queried at runtime.

Usage::

    registry = IdentityRegistry.from_config({
        "unified_identities": [
            {
                "agent_id": "fred",
                "git_email": "fred@prismatic.internal",
                "signing_key_id": "key_fred_prod_01",
                "credential_scopes": ["prismatic-engine", "active-oahu-static"],
                "status": "active",
            },
        ],
    })
    identity = registry.get("fred")
    print(identity.git_email)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.identity")


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class AgentStatus(Enum):
    """Operational status of an agent identity."""

    ACTIVE = "active"
    IDLE = "idle"
    SUSPENDED = "suspended"

    @classmethod
    def _missing_(cls, value: object) -> AgentStatus | None:
        """Default unknown statuses to ``IDLE`` for forward compatibility."""
        if isinstance(value, str):
            logger.warning("Unknown agent status %r — defaulting to IDLE", value)
            return cls.IDLE
        return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentIdentity:
    """Represents a single agent identity with credential scope metadata.

    Attributes:
        agent_id:         Unique logical name (e.g. ``fred``, ``ned``).
        git_email:        Email address used for git commits.
        signing_key_id:   Identifier for the SSH/GPG signing key.
        credential_scopes: List of workspace names this agent may access.
                          An empty list means **all** workspaces.
        status:           Current operational status.
        metadata:         Arbitrary key-value store for extensibility.
    """

    agent_id: str
    git_email: str = ""
    signing_key_id: str = ""
    credential_scopes: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.IDLE
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentIdentity:
        """Construct from a dictionary (typically from YAML config).

        Expected keys match the attribute names (camelCase or snake_case
        both accepted).  Unknown keys are stored in ``metadata``.
        """
        known = {"agent_id", "git_email", "signing_key_id",
                 "credential_scopes", "status", "metadata"}
        kwargs: dict[str, Any] = {}
        meta: dict[str, Any] = {}

        for key, value in raw.items():
            if key in known:
                kwargs[key] = value
            elif key.replace("-", "_") in known:
                kwargs[key.replace("-", "_")] = value
            else:
                meta[key] = value

        # Parse status
        status_raw = kwargs.get("status", "idle")
        if isinstance(status_raw, str):
            kwargs["status"] = AgentStatus(status_raw.lower())

        kwargs.setdefault("metadata", {})
        kwargs["metadata"].update(meta)

        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """``True`` if the agent is currently operational."""
        return self.status == AgentStatus.ACTIVE

    @property
    def is_suspended(self) -> bool:
        """``True`` if the agent has been suspended."""
        return self.status == AgentStatus.SUSPENDED

    def activate(self) -> None:
        """Set status to ``ACTIVE``."""
        self.status = AgentStatus.ACTIVE

    def suspend(self) -> None:
        """Set status to ``SUSPENDED``."""
        self.status = AgentStatus.SUSPENDED

    def mark_idle(self) -> None:
        """Set status to ``IDLE``."""
        self.status = AgentStatus.IDLE

    def has_scope(self, workspace_name: str) -> bool:
        """Check whether this agent may operate in a given workspace.

        An empty ``credential_scopes`` list grants access to **all**
        workspaces.
        """
        return not self.credential_scopes or workspace_name in self.credential_scopes

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "agent_id": self.agent_id,
            "git_email": self.git_email,
            "signing_key_id": self.signing_key_id,
            "credential_scopes": list(self.credential_scopes),
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class IdentityRegistry:
    """Thread-safe, immutable-after-construction registry of agent identities.

    Loads from a parsed ``unified_identities`` config list (as defined in
    ``PRISMATIC_ENGINE.yaml``) and provides lookup by ``agent_id``,
    status-filtered query, and scope-based filtering.
    """

    def __init__(self, identities: list[AgentIdentity] | None = None) -> None:
        self._by_id: dict[str, AgentIdentity] = {}

        if identities:
            for ident in identities:
                self._by_id[ident.agent_id] = ident

        logger.info(
            "IdentityRegistry loaded %d identity/ies: %s",
            len(self._by_id),
            sorted(self._by_id),
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> IdentityRegistry:
        """Load identities from the ``unified_identities`` key of a config dict.

        The expected structure is the YAML content::

            unified_identities:
              - agent_id: "fred"
                git_email: "fred@prismatic.internal"
                signing_key_id: "key_fred_prod_01"
                credential_scopes: ["prismatic-engine", "active-oahu-static"]
                status: "active"
        """
        raw_list = config.get("unified_identities", [])
        if not isinstance(raw_list, list):
            raise TypeError(
                f"'unified_identities' must be a list, "
                f"got {type(raw_list).__name__}"
            )
        identities = [AgentIdentity.from_dict(item) for item in raw_list]
        return cls(identities)

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> IdentityRegistry:
        """Load identities from a YAML file."""
        import yaml  # defer import

        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        if config is None:
            config = {}
        return cls.from_config(config)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._by_id

    def __iter__(self):
        return iter(self._by_id.values())

    @property
    def identity_ids(self) -> list[str]:
        """Return sorted list of registered agent IDs."""
        return sorted(self._by_id)

    def get(self, agent_id: str) -> AgentIdentity | None:
        """Look up an identity by ``agent_id``."""
        return self._by_id.get(agent_id)

    def require(self, agent_id: str) -> AgentIdentity:
        """Like ``get()`` but raises ``KeyError`` if not found."""
        ident = self._by_id.get(agent_id)
        if ident is None:
            raise KeyError(
                f"Unknown agent {agent_id!r}. "
                f"Known: {sorted(self._by_id)}"
            )
        return ident

    def get_by_status(self, status: AgentStatus) -> list[AgentIdentity]:
        """Return all identities with the given status."""
        return [i for i in self._by_id.values() if i.status == status]

    def active_identities(self) -> list[AgentIdentity]:
        """Return all identities with ``ACTIVE`` status."""
        return self.get_by_status(AgentStatus.ACTIVE)

    def can_access_workspace(self, agent_id: str, workspace_name: str) -> bool:
        """Check whether *agent_id* may access *workspace_name*.

        Returns ``False`` if the agent is unknown.
        """
        ident = self._by_id.get(agent_id)
        if ident is None:
            return False
        return ident.has_scope(workspace_name)
