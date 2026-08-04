"""External identity bindings and an in-memory dual-axis repository."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import threading
from typing import Optional

from prismatic.portability.capabilities import validate_capability_scope
from prismatic.portability.exceptions import BindingConflictError, ValidationError
from prismatic.portability.identity import (
    CANONICAL_ID_RE,
    CORE_NAMESPACE_RE,
    _check_secrets,
    _validate_iso_utc_timestamp,
    _validate_literal_one,
    _validate_positive_integer,
)

Axis1Key = tuple[str, str, str, int]
Axis2Key = tuple[str, str, str, str, int]

MAX_EXTERNAL_VERSION_BYTES = 256
MAX_EXTERNAL_VERSION_CHARS = 128
MAX_ETAG_BYTES = 256
MAX_ETAG_CHARS = 128


def _validate_required_string(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    _check_secrets(field_name, value)
    return value.strip()


def _validate_evidence(
    field_name: str,
    value: object,
    *,
    max_bytes: int,
    max_chars: int,
) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string or None")
    _check_secrets(field_name, value)
    if len(value) > max_chars or len(value.encode("utf-8")) > max_bytes:
        raise ValidationError(f"{field_name} exceeds its evidence bound")
    return value


@dataclass(frozen=True)
class ExternalIdentityBinding:
    """Immutable Core-to-provider identity binding V1."""

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
        _validate_literal_one(self.contract_version, field_name="contract_version")
        _validate_positive_integer(self.mapping_version, field_name="mapping_version")

        canonical_id = _validate_required_string("canonical_id", self.canonical_id)
        if not CANONICAL_ID_RE.fullmatch(canonical_id):
            raise ValidationError("canonical_id violates the Core canonical-ID grammar")
        object.__setattr__(self, "canonical_id", canonical_id)

        adapter_id = _validate_required_string("adapter_id", self.adapter_id)
        provider_namespace = _validate_required_string(
            "provider_namespace", self.provider_namespace
        )
        external_id = _validate_required_string("external_id", self.external_id)
        capability_scope = validate_capability_scope(
            _validate_required_string("capability_scope", self.capability_scope)
        )
        if CORE_NAMESPACE_RE.fullmatch(provider_namespace):
            raise ValidationError(
                "provider_namespace must not claim Core namespace authority"
            )
        object.__setattr__(self, "adapter_id", adapter_id)
        object.__setattr__(self, "provider_namespace", provider_namespace)
        object.__setattr__(self, "external_id", external_id)
        object.__setattr__(self, "capability_scope", capability_scope)

        created_at = _validate_iso_utc_timestamp(
            self.created_at, field_name="created_at"
        )
        updated_at = _validate_iso_utc_timestamp(
            self.updated_at, field_name="updated_at"
        )
        created_dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        updated_dt = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if updated_dt < created_dt:
            raise ValidationError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

        object.__setattr__(
            self,
            "external_version",
            _validate_evidence(
                "external_version",
                self.external_version,
                max_bytes=MAX_EXTERNAL_VERSION_BYTES,
                max_chars=MAX_EXTERNAL_VERSION_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "etag",
            _validate_evidence(
                "etag",
                self.etag,
                max_bytes=MAX_ETAG_BYTES,
                max_chars=MAX_ETAG_CHARS,
            ),
        )

    @property
    def axis1_key(self) -> Axis1Key:
        return (
            self.canonical_id,
            self.adapter_id,
            self.capability_scope,
            self.mapping_version,
        )

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
    """Thread-safe in-memory binding repository with atomic dual-axis uniqueness."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._axis1_index: dict[Axis1Key, ExternalIdentityBinding] = {}
        self._axis2_index: dict[Axis2Key, ExternalIdentityBinding] = {}

    def register_binding(
        self, binding: ExternalIdentityBinding
    ) -> ExternalIdentityBinding:
        a1_key = binding.axis1_key
        a2_key = binding.axis2_key
        with self._lock:
            if a1_key in self._axis1_index or a2_key in self._axis2_index:
                raise BindingConflictError(
                    "binding registration conflict; Prior truth preserved without mutation"
                )
            self._axis1_index[a1_key] = binding
            self._axis2_index[a2_key] = binding

        readback = self.lookup_binding(
            adapter_id=binding.adapter_id,
            provider_namespace=binding.provider_namespace,
            external_id=binding.external_id,
            capability_scope=binding.capability_scope,
            mapping_version=binding.mapping_version,
        )
        if readback is None or readback.canonical_id != binding.canonical_id:
            raise BindingConflictError("local binding readback verification failed")
        return readback

    def get_binding_by_canonical(
        self,
        canonical_id: str,
        adapter_id: str,
        capability_scope: str,
        mapping_version: int = 1,
    ) -> Optional[ExternalIdentityBinding]:
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
        key = (
            adapter_id,
            provider_namespace,
            external_id,
            capability_scope,
            mapping_version,
        )
        with self._lock:
            return self._axis2_index.get(key)

    def list_bindings_for_canonical(
        self, canonical_id: str
    ) -> list[ExternalIdentityBinding]:
        with self._lock:
            return [
                binding
                for binding in self._axis1_index.values()
                if binding.canonical_id == canonical_id
            ]

    def list_all_bindings(self) -> list[ExternalIdentityBinding]:
        with self._lock:
            return list(self._axis1_index.values())
