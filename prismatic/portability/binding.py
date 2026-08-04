"""
Prismatic Engine — External Identity Binding V1 & Repository
============================================================

Typed external identity bindings and in-memory dual-axis uniqueness repository.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Dict, List, Optional, Tuple

from prismatic.portability.capabilities import validate_capability_scope
from prismatic.portability.exceptions import (
    BindingConflictError,
    SecretDetectedError,
    ValidationError,
)
from prismatic.portability.identity import _check_secrets, _validate_iso_utc_timestamp


Axis1Key = Tuple[str, str, str, int]
# (canonical_id, adapter_id, capability_scope, mapping_version)

Axis2Key = Tuple[str, str, str, str, int]
# (adapter_id, provider_namespace, external_id, capability_scope, mapping_version)


@dataclass(frozen=True)
class ExternalIdentityBinding:
    """External Identity Binding V1.

    Binds a Core canonical_id to an external provider identity record.
    """

    canonical_id: str
    adapter_id: str
    provider_namespace: str
    external_id: str
    capability_scope: str
    created_at: str
    updated_at: str
    contract_version: int = 1
    mapping_version: int = 1
    external_version: Optional[str] = None
    etag: Optional[str] = None

    def __post_init__(self) -> None:
        if self.contract_version != 1:
            raise ValidationError(
                f"Invalid contract_version {self.contract_version!r}. Must be literal integer 1."
            )

        if not isinstance(self.canonical_id, str) or not self.canonical_id.strip():
            raise ValidationError("canonical_id must be a non-empty string.")

        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise ValidationError("adapter_id must be a non-empty string.")

        if not isinstance(self.provider_namespace, str) or not self.provider_namespace.strip():
            raise ValidationError("provider_namespace must be a non-empty string.")

        if not isinstance(self.external_id, str) or not self.external_id.strip():
            raise ValidationError("external_id must be a non-empty string.")

        validate_capability_scope(self.capability_scope)

        if not isinstance(self.mapping_version, int) or self.mapping_version < 1:
            raise ValidationError(
                f"Invalid mapping_version {self.mapping_version!r}. Must be a positive integer >= 1."
            )

        _validate_iso_utc_timestamp(self.created_at)
        _validate_iso_utc_timestamp(self.updated_at)

        # Secret scanning on external attributes
        _check_secrets("external_id", self.external_id)
        _check_secrets("provider_namespace", self.provider_namespace)
        if self.external_version:
            _check_secrets("external_version", self.external_version)
        if self.etag:
            _check_secrets("etag", self.etag)

    @property
    def axis1_key(self) -> Axis1Key:
        return (self.canonical_id, self.adapter_id, self.capability_scope, self.mapping_version)

    @property
    def axis2_key(self) -> Axis2Key:
        return (
            self.adapter_id,
            self.provider_namespace,
            self.external_id,
            self.capability_scope,
            self.mapping_version,
        )


class BindingRepository:
    """Thread-safe in-memory repository for ExternalIdentityBinding records.

    Enforces dual-axis uniqueness atomically and performs local readback verification.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._axis1_index: Dict[Axis1Key, ExternalIdentityBinding] = {}
        self._axis2_index: Dict[Axis2Key, ExternalIdentityBinding] = {}

    def register_binding(self, binding: ExternalIdentityBinding) -> ExternalIdentityBinding:
        """Atomically register a new binding enforcing both uniqueness axes.

        Performs immediate readback verification post-commit.

        Raises:
            BindingConflictError: If a collision occurs on Axis 1 or Axis 2.
        """
        a1_key = binding.axis1_key
        a2_key = binding.axis2_key

        with self._lock:
            existing_a1 = self._axis1_index.get(a1_key)
            existing_a2 = self._axis2_index.get(a2_key)

            if existing_a1 is not None or existing_a2 is not None:
                raise BindingConflictError(
                    f"Binding registration conflict for canonical_id={binding.canonical_id!r}, "
                    f"adapter_id={binding.adapter_id!r}, external_id={binding.external_id!r}. "
                    "Prior truth preserved without mutation."
                )

            # Atomic double-index commit
            self._axis1_index[a1_key] = binding
            self._axis2_index[a2_key] = binding

        # Local readback verification (PORT-02 Receipt Invariant)
        readback = self.lookup_binding(
            adapter_id=binding.adapter_id,
            provider_namespace=binding.provider_namespace,
            external_id=binding.external_id,
            capability_scope=binding.capability_scope,
            mapping_version=binding.mapping_version,
        )
        if readback is None or readback.canonical_id != binding.canonical_id:
            raise BindingConflictError(
                f"Local readback receipt verification failed for binding canonical_id={binding.canonical_id!r}"
            )

        return readback

    def get_binding_by_canonical(
        self,
        canonical_id: str,
        adapter_id: str,
        capability_scope: str,
        mapping_version: int = 1,
    ) -> Optional[ExternalIdentityBinding]:
        """Look up binding by Axis 1 key."""
        key = (canonical_id, adapter_id, capability_scope, mapping_version)
        with self._lock:
            return self._axis1_index.get(key)

    def lookup_binding(
        self,
        adapter_id: str,
        provider_namespace: str,
        external_id: str,
        capability_scope: str,
        mapping_version: int = 1,
    ) -> Optional[ExternalIdentityBinding]:
        """Look up binding by Axis 2 key."""
        key = (adapter_id, provider_namespace, external_id, capability_scope, mapping_version)
        with self._lock:
            return self._axis2_index.get(key)

    def list_bindings_for_canonical(self, canonical_id: str) -> List[ExternalIdentityBinding]:
        """List all bindings associated with a canonical_id."""
        with self._lock:
            return [b for b in self._axis1_index.values() if b.canonical_id == canonical_id]

    def list_all_bindings(self) -> List[ExternalIdentityBinding]:
        """List all registered bindings in repository."""
        with self._lock:
            return list(self._axis1_index.values())
