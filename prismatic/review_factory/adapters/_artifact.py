"""Shared extraction helpers for the L0 harness adapters (plan §4).

Adapters import the real ``prismatic.review_factory.artifact`` module
(workstream A) plus these helpers and stdlib — never harness code, never
the judge. Pure translation: no network, no side effects, no judgment.

Strict-schema notes (the real ``artifact.validate`` is fail-closed):
* ``explicit_gaps`` entries must name nullable schema paths, and every null
  nullable field must be gapped — ``compute_gaps`` derives gaps from the
  built objects so the two can never disagree (the anti-fabrication rule).
* ``diff.files[].change_type`` is a closed vocabulary; entries whose kind
  cannot be determined are omitted from ``diff.files`` (their paths are
  still recorded in ``novelty_context.first_seen_paths``) — never guessed.
* ``checks[].exit_code`` must be an int; harness command records without a
  result are omitted (a check without a result is not a deterministic
  result, plan §2).
* Diffs are capped with ``artifact.truncate_unified`` (1 MiB, marker).
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..artifact import (
    CheckResult,
    Diff,
    DiffFile,
    Intent,
    NoveltyContext,
    ReviewArtifact,
    truncate_unified,
    validate,
)

__all__ = [
    "AdapterError",
    "build_artifact",
    "compute_gaps",
    "make_check",
    "make_diff_file",
    "normalize_change_type",
    "now_iso",
    "opt_str",
    "require_mapping",
    "run_id_from",
    "str_list",
    "truncate_unified",
]


class AdapterError(Exception):
    """Raised when harness output cannot be translated. Fail-closed."""


def require_mapping(raw: Any, what: str) -> dict[str, Any]:
    """Fail closed on non-mapping harness output."""
    if not isinstance(raw, dict):
        raise AdapterError(
            f"{what}: expected a JSON-like mapping, got {type(raw).__name__}"
        )
    return raw


def run_id_from(raw: dict[str, Any], *keys: str) -> str:
    """First present, non-blank id among ``keys``. Fail-closed when absent."""
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value)
    raise AdapterError(f"no run id found (looked for: {', '.join(keys)})")


def opt_str(mapping: dict[str, Any], key: str) -> str | None:
    """Optional string field: present-and-non-blank, else None (never coerced)."""
    value = mapping.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def str_list(value: Any, what: str) -> list[str]:
    """Optional list-of-strings field; None/absent becomes []."""
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise AdapterError(f"{what} must be a list of strings")


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_CHANGE_TYPE_ALIASES = {
    "create": "added",
    "created": "added",
    "add": "added",
    "new": "added",
    "modify": "modified",
    "update": "modified",
    "updated": "modified",
    "edit": "modified",
    "changed": "modified",
    "delete": "deleted",
    "remove": "deleted",
    "removed": "deleted",
    "rename": "renamed",
    "move": "renamed",
    "moved": "renamed",
}


def normalize_change_type(value: Any) -> str:
    """Map harness-specific operation words onto the closed §2 vocabulary.

    Unknown values pass through verbatim so the caller can test membership
    in ``CHANGE_TYPES``; blank/missing becomes ``"unknown"`` (never guessed
    as ``"modified"``).
    """
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    lowered = value.strip().lower()
    return _CHANGE_TYPE_ALIASES.get(lowered, value.strip())


def _opt_nonneg_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def make_diff_file(
    path: str, change_type: str | None, added: Any, removed: Any
) -> DiffFile | None:
    """Build a §2 diff.files entry, or None when it cannot be honestly built.

    Returns None when the change kind is outside the closed vocabulary or
    the line counts are not non-negative ints — the entry is then omitted
    (never guessed, never zero-filled).
    """
    from ..artifact import CHANGE_TYPES

    if change_type not in CHANGE_TYPES:
        return None
    lines_added = _opt_nonneg_int(added)
    lines_removed = _opt_nonneg_int(removed)
    if lines_added is None or lines_removed is None:
        return None
    return DiffFile(
        path=path,
        change_type=change_type,
        lines_added=lines_added,
        lines_removed=lines_removed,
    )


def make_check(
    name: str, exit_code: Any, log_text: str | None, ran_at: str | None
) -> CheckResult | None:
    """Build a §2 checks entry, or None when there is no integer exit code.

    ``log_sha256`` hashes whatever log text the harness captured — the empty
    string when it captured none. The hash is provenance for the captured
    text, not a claim about a log never seen.
    """
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return None
    digest = hashlib.sha256((log_text or "").encode("utf-8")).hexdigest()
    ran = ran_at if isinstance(ran_at, str) and ran_at else None
    return CheckResult(name=name, exit_code=exit_code, log_sha256=digest, ran_at=ran)


def compute_gaps(intent: Intent, diff: Diff, checks: list[CheckResult]) -> list[str]:
    """Derive ``explicit_gaps`` from the built objects.

    Every nullable schema path that is null is listed; nothing else is.
    This keeps the anti-fabrication invariant (nulls == gaps) true by
    construction — adapters never hand-write gap lists.
    """
    gaps: list[str] = []
    if intent.plan_ref is None:
        gaps.append("intent.plan_ref")
    if intent.brief is None:
        gaps.append("intent.brief")
    if diff.base_tree is None:
        gaps.append("diff.base_tree")
    if diff.head_tree is None:
        gaps.append("diff.head_tree")
    if diff.unified is None:
        gaps.append("diff.unified")
    if any(c.log_sha256 is None for c in checks):
        gaps.append("checks[].log_sha256")
    if any(c.ran_at is None for c in checks):
        gaps.append("checks[].ran_at")
    return gaps


def build_artifact(
    *,
    harness_id: str,
    harness_run_id: str,
    submitted_at: str,
    intent: Intent,
    diff: Diff,
    checks: list[CheckResult],
    prior_receipts: list[str],
    novelty_context: NoveltyContext,
) -> ReviewArtifact:
    """Assemble via ``ReviewArtifact.create`` (content-hashed) and validate.

    Raises AdapterError / ArtifactValidationError — never returns invalid.
    """
    artifact = ReviewArtifact.create(
        harness_id=harness_id,
        harness_run_id=harness_run_id,
        submitted_at=submitted_at,
        intent=intent,
        diff=diff,
        checks=checks,
        prior_receipts=prior_receipts,
        novelty_context=novelty_context,
        explicit_gaps=compute_gaps(intent, diff, checks),
    )
    validate(artifact.to_dict())  # belt and braces on the boundary
    return artifact
