"""Capability-scoped, provider-neutral adapter registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import json
import math
import threading
from types import MappingProxyType
from typing import Any

from prismatic.portability.capabilities import validate_capability_scope
from prismatic.portability.exceptions import (
    AdapterNotFoundError,
    DuplicateAdapterError,
    UnsupportedCapabilityError,
    ValidationError,
)
from prismatic.portability.identity import (
    MAX_METADATA_JSON_BYTES,
    MAX_METADATA_KEYS,
    _check_secrets,
    _validate_iso_utc_timestamp,
    _validate_literal_one,
)


class QualificationState(str, Enum):
    """Qualification state for one adapter capability."""

    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    SUSPENDED = "SUSPENDED"
    DEGRADED = "DEGRADED"
    PENDING = "PENDING"


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError("metadata keys must be strings")
            _check_secrets(key, child)
            frozen[key] = _freeze_json(child)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    if value is None or isinstance(value, (str, int, bool)):
        if isinstance(value, str):
            _check_secrets("metadata_value", value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("metadata contains a non-finite number")
        return value
    raise ValidationError("metadata contains a non-JSON value")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True)
class AvailabilityObservation:
    """Non-authoritative availability telemetry."""

    status: str
    observed_at: str
    details: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValidationError("availability status must be a non-empty string")
        _check_secrets("availability_status", self.status)
        object.__setattr__(self, "status", self.status.strip())
        object.__setattr__(
            self,
            "observed_at",
            _validate_iso_utc_timestamp(self.observed_at, field_name="observed_at"),
        )
        if self.details is not None:
            if not isinstance(self.details, str):
                raise ValidationError("availability details must be a string or None")
            _check_secrets("availability_details", self.details)


@dataclass(frozen=True)
class ProviderAdapterRecord:
    """Immutable provider adapter record V1."""

    adapter_id: str
    adapter_kind: str
    declared_capabilities: tuple[str, ...]
    qualification_state: tuple[tuple[str, QualificationState], ...]
    availability_observation: AvailabilityObservation
    contract_version: int = 1
    entry_point: str | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _validate_literal_one(self.contract_version, field_name="contract_version")
        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise ValidationError("adapter_id must be a non-empty string")
        if not isinstance(self.adapter_kind, str) or not self.adapter_kind.strip():
            raise ValidationError("adapter_kind must be a non-empty string")
        _check_secrets("adapter_id", self.adapter_id)
        _check_secrets("adapter_kind", self.adapter_kind)
        object.__setattr__(self, "adapter_id", self.adapter_id.strip())
        object.__setattr__(self, "adapter_kind", self.adapter_kind.strip())

        if isinstance(self.declared_capabilities, str) or not isinstance(
            self.declared_capabilities, Iterable
        ):
            raise ValidationError("declared_capabilities must be a non-empty iterable")
        capabilities = tuple(
            sorted(
                {validate_capability_scope(cap) for cap in self.declared_capabilities}
            )
        )
        if not capabilities:
            raise ValidationError("declared_capabilities must not be empty")
        object.__setattr__(self, "declared_capabilities", capabilities)

        if isinstance(self.qualification_state, Mapping):
            qualification_items = self.qualification_state.items()
        else:
            try:
                qualification_items = tuple(self.qualification_state)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "qualification_state must be a mapping or pairs"
                ) from exc
        normalized_qualifications: list[tuple[str, QualificationState]] = []
        for capability, state in qualification_items:
            normalized_capability = validate_capability_scope(capability)
            if normalized_capability not in capabilities:
                raise ValidationError(
                    "qualification_state contains an undeclared capability"
                )
            if not isinstance(state, QualificationState):
                raise ValidationError("qualification_state values must be typed enums")
            normalized_qualifications.append((normalized_capability, state))
        object.__setattr__(
            self,
            "qualification_state",
            tuple(sorted(normalized_qualifications, key=lambda item: item[0])),
        )

        if not isinstance(self.availability_observation, AvailabilityObservation):
            raise ValidationError("availability_observation has the wrong type")
        if self.entry_point is not None:
            if not isinstance(self.entry_point, str):
                raise ValidationError("entry_point must be a string or None")
            _check_secrets("entry_point", self.entry_point)

        if self.metadata is None:
            return
        if not isinstance(self.metadata, Mapping):
            raise ValidationError("metadata must be a mapping or None")
        copied = dict(self.metadata)
        if len(copied) > MAX_METADATA_KEYS:
            raise ValidationError("metadata exceeds the key-count bound")
        frozen = _freeze_json(copied)
        try:
            encoded = json.dumps(
                _thaw_json(frozen),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValidationError("metadata is not strict JSON") from exc
        if len(encoded) > MAX_METADATA_JSON_BYTES:
            raise ValidationError("metadata exceeds the encoded byte bound")
        object.__setattr__(self, "metadata", frozen)


def _copy_record(record: ProviderAdapterRecord) -> ProviderAdapterRecord:
    return ProviderAdapterRecord(
        adapter_id=record.adapter_id,
        adapter_kind=record.adapter_kind,
        declared_capabilities=tuple(record.declared_capabilities),
        qualification_state=tuple(record.qualification_state),
        availability_observation=record.availability_observation,
        contract_version=record.contract_version,
        entry_point=record.entry_point,
        metadata=None if record.metadata is None else _thaw_json(record.metadata),
    )


class ProviderRegistry:
    """Thread-safe registry that stores immutable adapter truth."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._adapters: dict[str, ProviderAdapterRecord] = {}

    def register(self, record: ProviderAdapterRecord) -> ProviderAdapterRecord:
        stored = _copy_record(record)
        with self._lock:
            if stored.adapter_id in self._adapters:
                raise DuplicateAdapterError(
                    "duplicate adapter registration rejected; prior truth preserved"
                )
            self._adapters[stored.adapter_id] = stored
        return _copy_record(stored)

    def get(self, adapter_id: str) -> ProviderAdapterRecord:
        with self._lock:
            record = self._adapters.get(adapter_id)
        if record is None:
            raise AdapterNotFoundError("adapter is not registered")
        return _copy_record(record)

    def get_qualification(
        self, adapter_id: str, capability_scope: str
    ) -> QualificationState:
        normalized_capability = validate_capability_scope(capability_scope)
        adapter = self.get(adapter_id)
        if normalized_capability not in adapter.declared_capabilities:
            raise UnsupportedCapabilityError(
                "adapter does not declare the requested capability"
            )
        return dict(adapter.qualification_state).get(
            normalized_capability, QualificationState.UNQUALIFIED
        )

    def unregister(self, adapter_id: str) -> None:
        with self._lock:
            self._adapters.pop(adapter_id, None)

    def list_all(self) -> list[ProviderAdapterRecord]:
        with self._lock:
            records = tuple(self._adapters.values())
        return [_copy_record(record) for record in records]
