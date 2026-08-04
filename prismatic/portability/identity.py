"""
Prismatic Engine — Canonical Identity Envelope V1
=================================================

Immutable, serializable canonical identity envelope separating Core identity from providers.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime
import hashlib
import json
import re
from typing import Any, Dict, Optional

from prismatic.portability.exceptions import SecretDetectedError, ValidationError

# Regex patterns for detecting secret-shaped strings in identity records
_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{16,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_]{16,}", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (PRIVATE KEY|RSA PRIVATE KEY)-----", re.IGNORECASE),
]

_SECRET_KEY_KEYWORDS = {"password", "private_key", "secret_key", "api_key", "auth_token", "access_token"}

MAX_METADATA_KEYS = 32
MAX_METADATA_JSON_BYTES = 4096


def _check_secrets(key: str, value: Any) -> None:
    key_lower = key.lower()
    if any(keyword in key_lower for keyword in _SECRET_KEY_KEYWORDS):
        raise SecretDetectedError(f"Metadata key {key!r} appears to contain secret-shaped name.")

    if isinstance(value, str):
        val_lower = value.lower()
        if "bearer " in val_lower or "ghp_" in val_lower or "sk-" in val_lower:
            raise SecretDetectedError(f"Metadata value for key {key!r} contains secret pattern.")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                raise SecretDetectedError(f"Metadata value for key {key!r} matches secret signature.")


def _validate_iso_utc_timestamp(ts: str) -> str:
    if not isinstance(ts, str) or not ts.strip():
        raise ValidationError("created_at timestamp must be a non-empty string.")

    ts_clean = ts.strip()
    try:
        # Require 'Z' or ISO timestamp format
        if not (ts_clean.endswith("Z") or "+00:00" in ts_clean):
            raise ValidationError(f"Timestamp {ts_clean!r} must be in UTC (ending in 'Z' or '+00:00').")
        dt = datetime.datetime.fromisoformat(ts_clean.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() != datetime.timedelta(0):
            raise ValidationError(f"Timestamp {ts_clean!r} is not valid UTC.")
    except Exception as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"Invalid created_at ISO UTC timestamp {ts_clean!r}: {exc}")
    return ts_clean


@dataclass(frozen=True)
class CanonicalIdentityEnvelope:
    """Canonical Identity Envelope V1.

    Guarantees stable, provider-neutral Core identity representation.
    """

    canonical_id: str
    entity_kind: str
    namespace: str
    contract_version: int = 1
    mapping_version: int = 1
    created_at: str = ""
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        # 1. Contract Version must be literal 1
        if self.contract_version != 1:
            raise ValidationError(
                f"Invalid contract_version {self.contract_version!r}. Must be literal integer 1."
            )

        # 2. Canonical ID
        if not isinstance(self.canonical_id, str) or not self.canonical_id.strip():
            raise ValidationError("canonical_id must be a non-empty string.")

        # 3. Entity Kind
        if not isinstance(self.entity_kind, str) or not self.entity_kind.strip():
            raise ValidationError("entity_kind must be a non-empty string.")

        # 4. Namespace (Core-owned)
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValidationError("namespace must be a non-empty string.")

        # 5. Mapping Version
        if not isinstance(self.mapping_version, int) or self.mapping_version < 1:
            raise ValidationError(
                f"Invalid mapping_version {self.mapping_version!r}. Must be a positive integer >= 1."
            )

        # 6. Created At
        _validate_iso_utc_timestamp(self.created_at)

        # 7. Metadata validation
        if self.metadata is not None:
            if not isinstance(self.metadata, dict):
                raise ValidationError("metadata must be a dictionary or None.")

            if len(self.metadata) > MAX_METADATA_KEYS:
                raise ValidationError(
                    f"metadata key count {len(self.metadata)} exceeds maximum allowed limit of {MAX_METADATA_KEYS}."
                )

            for k, v in self.metadata.items():
                if not isinstance(k, str):
                    raise ValidationError(f"Metadata key {k!r} must be a string.")
                if isinstance(v, (dict, list, tuple, set)):
                    raise ValidationError(
                        f"Metadata value for key {k!r} is a nested container. Metadata must be scalar-only."
                    )
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    raise ValidationError(
                        f"Metadata value for key {k!r} has unsupported non-JSON scalar type {type(v).__name__}."
                    )
                _check_secrets(k, v)

            # Check json byte length
            json_bytes = json.dumps(self.metadata).encode("utf-8")
            if len(json_bytes) > MAX_METADATA_JSON_BYTES:
                raise ValidationError(
                    f"metadata size ({len(json_bytes)} bytes) exceeds max limit of {MAX_METADATA_JSON_BYTES} bytes."
                )

    def to_dict(self) -> Dict[str, Any]:
        """Convert envelope to dictionary representation."""
        result: Dict[str, Any] = {
            "contract_version": self.contract_version,
            "canonical_id": self.canonical_id,
            "entity_kind": self.entity_kind,
            "namespace": self.namespace,
            "mapping_version": self.mapping_version,
            "created_at": self.created_at,
        }
        if self.metadata is not None:
            result["metadata"] = self.metadata
        return result


def to_canonical_json(envelope: CanonicalIdentityEnvelope) -> str:
    """Serialize envelope to deterministic field-order-independent canonical JSON string."""
    return json.dumps(envelope.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_digest(envelope: CanonicalIdentityEnvelope) -> str:
    """Compute SHA-256 hex digest of the canonical JSON representation of an envelope."""
    canonical_str = to_canonical_json(envelope)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
