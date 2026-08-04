"""
Prismatic Engine — Fake Offline Adapter Fixture
==============================================

Test fixture proving network-free capability negotiation, offline identity creation,
binding conflict rejection, and outage isolation.
"""

from __future__ import annotations

import datetime
from typing import Optional, Set

from prismatic.portability.binding import BindingRepository, ExternalIdentityBinding
from prismatic.portability.capabilities import CapabilityScope
from prismatic.portability.identity import CanonicalIdentityEnvelope
from prismatic.portability.registry import (
    AvailabilityObservation,
    ProviderAdapterRecord,
    ProviderRegistry,
    QualificationState,
)


class FakeOfflineAdapter:
    """Fake/Offline provider adapter for network-free testing and verification."""

    def __init__(
        self,
        adapter_id: str = "fake_offline_adapter",
        adapter_kind: str = "fake_vcs",
        declared_capabilities: Optional[Set[str]] = None,
    ):
        self.adapter_id = adapter_id
        self.adapter_kind = adapter_kind
        self.declared_capabilities = declared_capabilities or {
            CapabilityScope.VCS_READ.value,
            CapabilityScope.ISSUE_READ.value,
        }

    def register(
        self,
        registry: ProviderRegistry,
        status: str = "HEALTHY",
        qualifications: Optional[dict[str, QualificationState]] = None,
    ) -> ProviderAdapterRecord:
        """Register adapter with the given registry in a network-free manner."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        quals = qualifications or {
            cap: QualificationState.QUALIFIED for cap in self.declared_capabilities
        }

        record = ProviderAdapterRecord(
            adapter_id=self.adapter_id,
            adapter_kind=self.adapter_kind,
            declared_capabilities=self.declared_capabilities,
            qualification_state=quals,
            availability_observation=AvailabilityObservation(
                status=status,
                observed_at=now,
                details="Offline test fixture — no network used",
            ),
            contract_version=1,
            entry_point="prismatic.portability.testing.FakeOfflineAdapter",
        )
        return registry.register(record)

    def create_canonical_identity(
        self,
        canonical_id: str,
        entity_kind: str = "issue",
        namespace: str = "prismatic:core:testing",
        created_at: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> CanonicalIdentityEnvelope:
        """Create a CanonicalIdentityEnvelope offline without any provider interaction."""
        ts = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        return CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id=canonical_id,
            entity_kind=entity_kind,
            namespace=namespace,
            mapping_version=1,
            created_at=ts,
            metadata=metadata,
        )

    def bind_external_identity(
        self,
        repository: BindingRepository,
        canonical_id: str,
        provider_namespace: str,
        external_id: str,
        capability_scope: str,
        created_at: Optional[str] = None,
    ) -> ExternalIdentityBinding:
        """Bind an external identity to a canonical identity offline."""
        ts = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        binding = ExternalIdentityBinding(
            contract_version=1,
            canonical_id=canonical_id,
            adapter_id=self.adapter_id,
            provider_namespace=provider_namespace,
            external_id=external_id,
            capability_scope=capability_scope,
            mapping_version=1,
            created_at=ts,
            updated_at=ts,
        )
        return repository.register_binding(binding)
