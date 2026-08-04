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

    pass


class CanonicalIdentityError(PortabilityError):
    """Raised when canonical identity operations fail."""

    pass


class BindingConflictError(PortabilityError):
    """Raised when an external identity binding registration collides with existing state."""

    pass


class InvalidCapabilityError(ValidationError):
    """Raised when an unknown or malformed capability scope is encountered."""

    pass


class DuplicateAdapterError(PortabilityError):
    """Raised when registering an adapter with conflicting metadata."""

    pass


class AdapterNotFoundError(PortabilityError):
    """Raised when an adapter is not found in the registry."""

    pass


class UnsupportedCapabilityError(PortabilityError):
    """Raised when an operation requests a capability not supported by an adapter."""

    pass
