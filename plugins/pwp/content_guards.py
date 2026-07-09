"""Guards for EmDash-editable PWP theme/content records.

The PWP theme contract lets editors mutate copy and media, but not tenant identity,
compliance proof, or routing fields.  This module keeps that boundary explicit and
framework-neutral so it can be reused by a local fixture renderer, an API handler,
or an Astro build preflight without coupling the contract to Hermes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

DEFAULT_LOCKED_FIELDS = frozenset({"complianceClaims", "legalName", "schemaOrgType"})
DEFAULT_SYSTEM_ROUTING_FIELDS = frozenset(
    {
        "route",
        "slug",
        "path",
        "permalink",
        "canonicalUrl",
        "redirectTo",
        "redirects",
        "sitemapPriority",
        "sitemapChangefreq",
        "blockId",
        "moduleId",
        "component",
        "templateId",
        "themeFamily",
    }
)

_CERTIFICATION_PATTERN = re.compile(
    r"\b("
    r"certified|certification|accredited|accreditation|licensed|license|"
    r"hipaa|soc\s*2|iso\s*\d{4,5}|r2v?3?|e[- ]?stewards|"
    r"pci[- ]?dss|gdpr[- ]?compliant|ccpa[- ]?compliant"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardViolation:
    """A single rejected edit."""

    path: str
    reason: str
    value: Any = None


@dataclass(frozen=True)
class GuardResult:
    """Result of applying the guard to a proposed edit payload."""

    safe_edits: dict[str, Any]
    violations: tuple[GuardViolation, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.violations


class LockedFieldViolation(ValueError):
    """Raised when a caller requires hard-fail semantics for unsafe edits."""

    def __init__(self, violations: Iterable[GuardViolation]) -> None:
        self.violations = tuple(violations)
        details = "; ".join(f"{v.path}: {v.reason}" for v in self.violations)
        super().__init__(f"PWP locked-field guard rejected {len(self.violations)} edit(s): {details}")


def guard_emdash_edits(
    proposed_edits: Mapping[str, Any],
    *,
    edit_map: Mapping[str, Any] | None = None,
    allowed_compliance_claims: Iterable[str] | None = None,
    locked_fields: Iterable[str] | None = None,
    system_routing_fields: Iterable[str] | None = None,
) -> GuardResult:
    """Return only edits that may safely pass through EmDash.

    Args:
        proposed_edits: Nested patch/delta payload keyed by content field.
        edit_map: Optional PWP EmDash edit map.  ``lockedFields`` are honored, and
            explicit ``fields`` with ``editable: false`` are treated as locked.
        allowed_compliance_claims: Certification/compliance phrases that are
            already validated for this tenant/brief.  Matching is case-insensitive.
        locked_fields: Additional locked field names or dotted paths.
        system_routing_fields: Additional system-owned routing field names.

    The guard rejects edits when any path segment targets a locked field, any path
    segment targets a system routing field, or a string/array value contains an
    unsupported certification/compliance claim.
    """

    locked = set(DEFAULT_LOCKED_FIELDS)
    if locked_fields:
        locked.update(locked_fields)
    locked.update(_locked_fields_from_edit_map(edit_map))

    routing = set(DEFAULT_SYSTEM_ROUTING_FIELDS)
    if system_routing_fields:
        routing.update(system_routing_fields)

    allowed_claims = {_normalize_claim(c) for c in (allowed_compliance_claims or ()) if str(c).strip()}
    allowed_claims.update(_claims_from_edit_map(edit_map))

    safe: dict[str, Any] = {}
    violations: list[GuardViolation] = []
    for key, value in proposed_edits.items():
        _copy_if_safe(
            target=safe,
            key=str(key),
            value=value,
            path=(str(key),),
            locked=locked,
            routing=routing,
            allowed_claims=allowed_claims,
            violations=violations,
        )
    return GuardResult(safe_edits=safe, violations=tuple(violations))


def assert_safe_emdash_edits(
    proposed_edits: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Hard-fail wrapper around :func:`guard_emdash_edits`.

    API handlers can call this to reject the whole mutation atomically. Fixture
    renderers can use ``guard_emdash_edits`` directly when they want to show the
    sanitized patch and the rejected reasons side by side.
    """

    result = guard_emdash_edits(proposed_edits, **kwargs)
    if result.violations:
        raise LockedFieldViolation(result.violations)
    return result.safe_edits


def _copy_if_safe(
    *,
    target: dict[str, Any],
    key: str,
    value: Any,
    path: tuple[str, ...],
    locked: set[str],
    routing: set[str],
    allowed_claims: set[str],
    violations: list[GuardViolation],
) -> None:
    dotted = ".".join(path)
    if _matches_path(path, locked):
        violations.append(GuardViolation(dotted, "locked field is owned by tenant/system contract", value))
        return
    if _matches_path(path, routing):
        violations.append(GuardViolation(dotted, "routing/system field is not editor-editable", value))
        return

    claim = _unsupported_claim(value, allowed_claims)
    if claim:
        violations.append(GuardViolation(dotted, f"unsupported certification/compliance claim: {claim!r}", value))
        return

    if isinstance(value, Mapping):
        child: dict[str, Any] = {}
        for child_key, child_value in value.items():
            _copy_if_safe(
                target=child,
                key=str(child_key),
                value=child_value,
                path=(*path, str(child_key)),
                locked=locked,
                routing=routing,
                allowed_claims=allowed_claims,
                violations=violations,
            )
        if child:
            target[key] = child
        return

    target[key] = value


def _locked_fields_from_edit_map(edit_map: Mapping[str, Any] | None) -> set[str]:
    if not edit_map:
        return set()
    locked = {str(field) for field in edit_map.get("lockedFields", ())}
    blocks = edit_map.get("blocks", ())
    if isinstance(blocks, Iterable) and not isinstance(blocks, (str, bytes, Mapping)):
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            fields = block.get("fields", {})
            if not isinstance(fields, Mapping):
                continue
            for name, spec in fields.items():
                if isinstance(spec, Mapping) and spec.get("editable") is False:
                    locked.add(str(name))
    return locked


def _claims_from_edit_map(edit_map: Mapping[str, Any] | None) -> set[str]:
    if not edit_map:
        return set()
    claims: set[str] = set()
    for key in ("allowedComplianceClaims", "lockedClaims", "complianceClaims"):
        value = edit_map.get(key)
        if isinstance(value, str):
            claims.add(_normalize_claim(value))
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
            claims.update(_normalize_claim(v) for v in value if str(v).strip())
    return claims


def _matches_path(path: tuple[str, ...], protected: set[str]) -> bool:
    dotted = ".".join(path)
    return dotted in protected or any(segment in protected for segment in path)


def _unsupported_claim(value: Any, allowed_claims: set[str]) -> str | None:
    strings = list(_walk_strings(value))
    for text in strings:
        match = _CERTIFICATION_PATTERN.search(text)
        if not match:
            continue
        normalized = _normalize_claim(text)
        if normalized in allowed_claims:
            continue
        if any(allowed and allowed in normalized for allowed in allowed_claims):
            continue
        return match.group(0)
    return None


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for child in value:
            yield from _walk_strings(child)


def _normalize_claim(value: Any) -> str:
    return " ".join(str(value).casefold().split())
