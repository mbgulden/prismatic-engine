"""
Prismatic Engine — Portability Package
======================================

Canonical identity envelopes, external identity bindings, and provider registry foundation.
"""

from prismatic.portability.binding import BindingRepository, ExternalIdentityBinding
from prismatic.portability.capabilities import (
    CapabilityScope,
    validate_capability_scope,
)
from prismatic.portability.exceptions import (
    AdapterNotFoundError,
    BindingConflictError,
    CanonicalIdentityError,
    DuplicateAdapterError,
    InvalidCapabilityError,
    PortabilityError,
    SecretDetectedError,
    UnsupportedCapabilityError,
    ValidationError,
)
from prismatic.portability.identity import (
    CanonicalIdentityEnvelope,
    EntityKind,
    canonical_digest,
    to_canonical_json,
)
from prismatic.portability.registry import (
    AvailabilityObservation,
    ProviderAdapterRecord,
    ProviderRegistry,
    QualificationState,
)
from prismatic.portability.testing import FakeOfflineAdapter

__all__ = [
    "CanonicalIdentityEnvelope",
    "EntityKind",
    "to_canonical_json",
    "canonical_digest",
    "ExternalIdentityBinding",
    "BindingRepository",
    "ProviderRegistry",
    "ProviderAdapterRecord",
    "QualificationState",
    "AvailabilityObservation",
    "CapabilityScope",
    "validate_capability_scope",
    "PortabilityError",
    "ValidationError",
    "SecretDetectedError",
    "CanonicalIdentityError",
    "BindingConflictError",
    "InvalidCapabilityError",
    "DuplicateAdapterError",
    "AdapterNotFoundError",
    "UnsupportedCapabilityError",
    "FakeOfflineAdapter",
]
