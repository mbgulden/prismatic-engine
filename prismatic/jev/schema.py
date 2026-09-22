"""Wire schema versioning for the typed-decision primitive.

Every request carries a top-level ``schema_version``; every question carries a
per-type ``wire_version`` (e.g. ``choice.v1``) so one question type can evolve
without migrating all callers.

SemVer rules for future changes:

- MAJOR (breaking): removing/renaming/retyping a field, removing an enum
  value, tightening validation, changing an error shape callers match on.
  Bumps the top-level ``schema_version``; old versions are rejected.
- MINOR (compatible): new optional fields, new question types, new answer
  fields. Bumps the per-type wire version only; readers ignore unknown
  fields.
- PATCH: docs/metadata only; no version change.

Responses carrying an unrecognized ``schema_version`` are rejected
(fail-closed) — never parsed hopefully.
"""

from __future__ import annotations

from .errors import DecisionError

SCHEMA_VERSION = "1.0"

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})

WIRE_VERSIONS = {
    "noul": "noul.v1",
    "choice": "choice.v1",
    "score": "score.v1",
}


def wire_version_for(kind: str) -> str:
    """Per-type wire version. ``kind`` is a closed set; KeyError is a bug."""
    return WIRE_VERSIONS[kind]


def check_response_schema_version(data: object) -> None:
    """Fail closed on responses carrying an unknown schema_version."""
    if isinstance(data, dict):
        version = data.get("schema_version")
        if version is not None and version not in SUPPORTED_SCHEMA_VERSIONS:
            raise DecisionError(
                f"unsupported response schema_version: {version!r}; "
                f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
