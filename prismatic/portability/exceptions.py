"""
Prismatic Engine — Portability Exceptions
=========================================

Typed error hierarchy for canonical identity, external bindings, and capability registry.
"""

from __future__ import annotations


class PortabilityError(Exception):
    """Base exception for all portability module errors."""

    pass


class ValidationError(PortabilityError):
    """Raised when record validation fails (malformed fields, invalid types, unbounded metadata)."""

    pass


class SecretDetectedError(ValidationError):
    """Raised when a secret-shaped value or key is detected in canonical records."""

    _CODES = frozenset({"secret_key_detected", "secret_value_detected"})

    def __init__(self, code: str, *, length: int | None = None) -> None:
        if code not in self._CODES:
            raise ValueError("invalid secret error code")
        length_summary = "<redacted>" if length is None else str(length)
        super().__init__(f"{code}: field=<redacted>; length={length_summary}")


class CanonicalIdentityError(PortabilityError):
    """Raised when canonical identity operations fail."""

    pass


class BindingConflictError(PortabilityError):
    """Raised when an external identity binding registration collides with existing state."""

    def __init__(self) -> None:
        super().__init__("binding_conflict: field=<redacted>; length=<redacted>")


class InvalidCapabilityError(ValidationError):
    """Raised when an unknown or malformed capability scope is encountered."""

    def __init__(self) -> None:
        super().__init__("invalid_capability: field=<redacted>; length=<redacted>")


class InvalidEntityKindError(ValidationError):
    """Raised when an entity kind is outside the closed Core vocabulary."""

    def __init__(self) -> None:
        super().__init__("invalid_event_kind: field=<redacted>; length=<redacted>")


class InvalidNamespaceError(ValidationError):
    """Raised when a namespace violates Core ownership rules."""

    def __init__(self) -> None:
        super().__init__("invalid_namespace: field=<redacted>; length=<redacted>")


class DuplicateAdapterError(PortabilityError):
    """Raised when registering an adapter with conflicting metadata."""

    pass


class AdapterNotFoundError(PortabilityError):
    """Raised when an adapter is not found in the registry."""

    pass


class UnsupportedCapabilityError(PortabilityError):
    """Raised when an operation requests a capability not supported by an adapter."""

    pass
