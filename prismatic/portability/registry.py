"""
Prismatic Engine — Capability-Scoped Provider Registry
======================================================

Provider-neutral, capability-scoped registry separating adapter identity, qualification, and telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
from typing import Any, Dict, Optional, Set

from prismatic.portability.capabilities import validate_capability_scope
from prismatic.portability.exceptions import (
    AdapterNotFoundError,
    DuplicateAdapterError,
    UnsupportedCapabilityError,
    ValidationError,
)


class QualificationState(str, Enum):
    """Qualification state per capability for a provider adapter."""

    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    SUSPENDED = "SUSPENDED"
    DEGRADED = "DEGRADED"
    PENDING = "PENDING"


@dataclass(frozen=True)
class AvailabilityObservation:
    """Non-authoritative availability observation / health telemetry."""

    status: str  # e.g., "HEALTHY", "DEGRADED", "OUTAGE", "UNKNOWN"
    observed_at: str  # ISO UTC timestamp
    details: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValidationError("AvailabilityObservation status must be a non-empty string.")
        if not isinstance(self.observed_at, str) or not self.observed_at.strip():
            raise ValidationError("AvailabilityObservation observed_at must be a non-empty string.")


@dataclass(frozen=True)
class ProviderAdapterRecord:
    """Provider Adapter Record V1.

    Maintains capability-scoped qualification state and non-authoritative telemetry.
    """

    adapter_id: str
    adapter_kind: str
    declared_capabilities: Set[str]
    qualification_state: Dict[str, QualificationState]
    availability_observation: AvailabilityObservation
    contract_version: int = 1
    entry_point: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValidationError(
                f"Invalid contract_version {self.contract_version!r}. Must be literal integer 1."
            )

        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise ValidationError("adapter_id must be a non-empty string.")

        if not isinstance(self.adapter_kind, str) or not self.adapter_kind.strip():
            raise ValidationError("adapter_kind must be a non-empty string.")

        if not isinstance(self.declared_capabilities, set) or not self.declared_capabilities:
            raise ValidationError("declared_capabilities must be a non-empty set of capability strings.")

        # Validate all declared capabilities against closed vocabulary
        validated_caps = set()
        for cap in self.declared_capabilities:
            validated_caps.add(validate_capability_scope(cap))

        # Check qualification_state mapping
        if not isinstance(self.qualification_state, dict):
            raise ValidationError("qualification_state must be a dictionary.")

        for cap, qual in self.qualification_state.items():
            validate_capability_scope(cap)
            if cap not in validated_caps:
                raise ValidationError(
                    f"Qualification state provided for capability {cap!r} which is not declared in declared_capabilities."
                )
            if not isinstance(qual, QualificationState):
                raise ValidationError(
                    f"Qualification for {cap!r} must be a QualificationState enum value, got {type(qual).__name__}."
                )


class ProviderRegistry:
    """Capability-scoped registry for provider adapters.

    Guarantees fail-closed semantics, capability-scoped qualification, and non-authoritative telemetry.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._adapters: Dict[str, ProviderAdapterRecord] = {}

    def register(self, record: ProviderAdapterRecord) -> ProviderAdapterRecord:
        """Register a provider adapter record.

        Fails closed on duplicate registration or conflicting metadata without mutating prior state.

        Raises:
            DuplicateAdapterError: If an adapter with adapter_id is already registered.
        """
        with self._lock:
            if record.adapter_id in self._adapters:
                raise DuplicateAdapterError(
                    f"Adapter {record.adapter_id!r} is already registered. Duplicate registration fails closed."
                )
            self._adapters[record.adapter_id] = record
            return record

    def get(self, adapter_id: str) -> ProviderAdapterRecord:
        """Retrieve a registered adapter record.

        Raises:
            AdapterNotFoundError: If adapter_id is not registered.
        """
        with self._lock:
            if adapter_id not in self._adapters:
                raise AdapterNotFoundError(f"Adapter {adapter_id!r} is not registered.")
            return self._adapters[adapter_id]

    def get_qualification(self, adapter_id: str, capability_scope: str) -> QualificationState:
        """Query qualification state for a specific adapter and capability scope.

        Fails closed if adapter or capability scope is unknown or unsupported.

        Raises:
            AdapterNotFoundError: If adapter_id is unknown.
            InvalidCapabilityError: If capability_scope is invalid.
            UnsupportedCapabilityError: If capability_scope is not declared by adapter.
        """
        normalized_cap = validate_capability_scope(capability_scope)
        adapter = self.get(adapter_id)

        if normalized_cap not in adapter.declared_capabilities:
            raise UnsupportedCapabilityError(
                f"Adapter {adapter_id!r} does not declare support for capability {normalized_cap!r}."
            )

        return adapter.qualification_state.get(normalized_cap, QualificationState.UNQUALIFIED)

    def unregister(self, adapter_id: str) -> None:
        """Remove adapter registration from registry.

        Removes registry availability and qualification state ONLY. Existing canonical
        identities and external bindings in BindingRepository remain completely unchanged.
        """
        with self._lock:
            if adapter_id in self._adapters:
                del self._adapters[adapter_id]

    def list_all(self) -> list[ProviderAdapterRecord]:
        """List all registered provider adapters."""
        with self._lock:
            return list(self._adapters.values())
