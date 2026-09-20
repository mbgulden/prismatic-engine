"""Canonical, provider-neutral identity envelopes for Prismatic Core."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from prismatic.portability.exceptions import (
    InvalidEntityKindError,
    InvalidNamespaceError,
    SecretDetectedError,
    ValidationError,
)

_SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]{16,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_]{16,}", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9_\-.]{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:PRIVATE KEY|RSA PRIVATE KEY)-----", re.IGNORECASE),
)
_SECRET_KEY_KEYWORDS = frozenset(
    {"password", "private_key", "secret_key", "api_key", "auth_token", "access_token"}
)

MAX_METADATA_KEYS = 32
MAX_METADATA_JSON_BYTES = 4096
CORE_ENTITY_KINDS = frozenset({"issue", "pull_request", "release", "asset", "comment"})
CORE_NAMESPACE_RE = re.compile(r"^prismatic:core:[a-z][a-z0-9_]{0,63}$")
CANONICAL_ID_RE = re.compile(r"^canon_[a-z][a-z0-9_]{1,63}$")
_PROVIDER_TOKENS = frozenset(
    {"github", "linear", "gitlab", "slack", "stripe", "aws", "azure", "gcp"}
)


class EntityKind(str, Enum):
    """Closed vocabulary of Core-owned canonical entity kinds."""

    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    RELEASE = "release"
    ASSET = "asset"
    COMMENT = "comment"


def _check_secrets(key: str, value: Any) -> None:
    """Reject secret-shaped caller data without reflecting it in exceptions."""

    lowered_key = key.lower()
    if (
        any(keyword in lowered_key for keyword in _SECRET_KEY_KEYWORDS)
        or "bearer " in lowered_key
        or "ghp_" in lowered_key
        or "sk-" in lowered_key
        or any(pattern.search(key) for pattern in _SECRET_PATTERNS)
    ):
        raise SecretDetectedError("secret_key_detected", length=len(key))
    if not isinstance(value, str):
        return
    lowered = value.lower()
    if "bearer " in lowered or "ghp_" in lowered or "sk-" in lowered:
        raise SecretDetectedError("secret_value_detected", length=len(value))
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise SecretDetectedError("secret_value_detected", length=len(value))


def _validate_iso_utc_timestamp(ts: str, *, field_name: str = "timestamp") -> str:
    """Validate an ISO-8601 UTC timestamp and return one canonical ``Z`` spelling."""

    if not isinstance(ts, str) or not ts.strip():
        raise ValidationError(f"{field_name} must be a non-empty UTC timestamp")
    cleaned = ts.strip()
    try:
        parsed = datetime.datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{field_name} must be a valid ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise ValidationError(f"{field_name} must use UTC")
    normalized = parsed.astimezone(datetime.timezone.utc).isoformat(
        timespec="microseconds"
    )
    return normalized.removesuffix("+00:00") + "Z"


def _validate_int_version(value: Any, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field_name} must be a non-Boolean integer >= 1")


def _validate_literal_one(value: Any, *, field_name: str) -> None:
    """Compatibility validator for unchanged V1-only registry records."""

    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise ValidationError(f"{field_name} must be the literal integer 1")


@dataclass(frozen=True)
class CanonicalIdentityEnvelope:
    """Immutable, serializable Core identity envelope V1."""

    canonical_id: str
    entity_kind: EntityKind
    namespace: str
    contract_version: int = 1
    mapping_version: int = 1
    created_at: str = ""
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _validate_int_version(self.contract_version, field_name="contract_version")
        _validate_int_version(self.mapping_version, field_name="mapping_version")

        if not isinstance(self.canonical_id, str):
            raise ValidationError("canonical_id must be a string")
        _check_secrets("canonical_id", self.canonical_id)
        canonical_id = self.canonical_id.strip()
        if not CANONICAL_ID_RE.fullmatch(canonical_id):
            raise ValidationError("canonical_id violates the Core canonical-ID grammar")
        object.__setattr__(self, "canonical_id", canonical_id)

        _check_secrets("entity_kind", self.entity_kind)
        try:
            entity_kind = EntityKind(self.entity_kind)
        except (TypeError, ValueError):
            raise InvalidEntityKindError() from None
        if entity_kind.value not in CORE_ENTITY_KINDS:
            raise InvalidEntityKindError()
        object.__setattr__(self, "entity_kind", entity_kind)

        if not isinstance(self.namespace, str):
            raise ValidationError("namespace must be a string")
        _check_secrets("namespace", self.namespace)
        namespace = self.namespace.strip()
        if not CORE_NAMESPACE_RE.fullmatch(namespace):
            raise InvalidNamespaceError()
        namespace_suffix = namespace.removeprefix("prismatic:core:")
        if any(token in namespace_suffix.split("_") for token in _PROVIDER_TOKENS):
            raise InvalidNamespaceError()
        object.__setattr__(self, "namespace", namespace)

        normalized_created_at = _validate_iso_utc_timestamp(
            self.created_at, field_name="created_at"
        )
        object.__setattr__(self, "created_at", normalized_created_at)

        if self.metadata is None:
            return
        if not isinstance(self.metadata, Mapping):
            raise ValidationError("metadata must be a mapping or None")
        copied = dict(self.metadata)
        if len(copied) > MAX_METADATA_KEYS:
            raise ValidationError("metadata exceeds the key-count bound")
        for key, value in copied.items():
            if not isinstance(key, str):
                raise ValidationError("metadata keys must be strings")
            _check_secrets(key, value)
            if isinstance(value, (dict, list, tuple, set)):
                raise ValidationError("metadata must contain JSON scalar values only")
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValidationError("metadata contains an unsupported scalar type")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValidationError("metadata contains a non-finite number")
        try:
            encoded = json.dumps(
                copied,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValidationError("metadata is not strict JSON") from exc
        if len(encoded) > MAX_METADATA_JSON_BYTES:
            raise ValidationError("metadata exceeds the encoded byte bound")
        object.__setattr__(self, "metadata", MappingProxyType(copied))

    def to_dict(self) -> dict[str, Any]:
        """Return a caller-owned dictionary representation."""

        result: dict[str, Any] = {
            "contract_version": self.contract_version,
            "canonical_id": self.canonical_id,
            "entity_kind": self.entity_kind.value,
            "namespace": self.namespace,
            "mapping_version": self.mapping_version,
            "created_at": self.created_at,
        }
        if self.metadata is not None:
            result["metadata"] = dict(self.metadata)
        return result


def to_canonical_json(envelope: CanonicalIdentityEnvelope) -> str:
    """Serialize an envelope as strict, deterministic canonical JSON."""

    return json.dumps(
        envelope.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_digest(envelope: CanonicalIdentityEnvelope) -> str:
    """Return the SHA-256 digest of canonical JSON bytes."""

    return hashlib.sha256(to_canonical_json(envelope).encode("utf-8")).hexdigest()
