"""L0 ReviewArtifact — the portable, content-hashed unit of the validation loop.

Every harness adapter produces exactly this; the deterministic floor (L1),
the judgment layer (L2), and the learn loop consume nothing else.

Spec: ``prismatic/review_factory/spec/review_artifact_v1.md`` (frozen).
Plan ref: ``jev-validation-loop-plan.md`` §2.

Invariants:
    * **Boundary validation is fail-closed.** :func:`validate` rejects unknown
      fields, oversize diffs, and any field that is ``null`` without being
      listed under ``explicit_gaps``. An adapter that cannot produce a field
      emits it as ``null`` *and* names it under ``explicit_gaps`` — it never
      fabricates a value.
    * **Content-addressed.** ``artifact_id`` is the sha256 of the canonical
      JSON of every other field. It is the join key for receipts, verdicts,
      and learn-loop outcomes.
    * **Write-once.** Stored artifacts are append-only; a stored artifact is
      never mutated.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "artifact-v1"

#: Harness ids admitted at the boundary. A new harness = a new adapter
#: module + one entry here; no core changes.
HARNESS_IDS = frozenset(
    {
        "claude-code-cli",
        "gemini-cli",
        "codex-cli",
        "hermes",
        "github-pr",
        "manual",
    }
)

#: Size caps (bytes / counts). Oversize payloads are rejected at the
#: boundary; adapters must truncate ``unified`` diffs before submitting.
MAX_UNIFIED_DIFF_BYTES = 1_048_576  # 1 MiB
MAX_FILES = 10_000
MAX_CHECKS = 1_000
MAX_GOALS = 1_000
MAX_PATHS = 100_000

#: Marker adapters append when truncating ``unified`` to fit the size cap.
TRUNCATION_MARKER = "\n... [diff truncated: exceeded size cap] ...\n"

#: Admitted values for ``diff.files[].change_type``. Adapters map
#: harness-specific change kinds into this closed vocabulary.
CHANGE_TYPES = frozenset({"added", "modified", "deleted", "renamed"})


class ArtifactValidationError(ValueError):
    """Raised when a ReviewArtifact payload fails fail-closed boundary validation."""


# ─────────────────────────────────────────────────────────────────────
# Dataclasses (exactly the §2 v1 fields, nothing more)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Intent:
    plan_ref: str | None
    brief: str | None
    goals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiffFile:
    path: str
    change_type: str
    lines_added: int
    lines_removed: int


@dataclass(frozen=True)
class Diff:
    base_tree: str | None
    head_tree: str | None
    unified: str | None
    files: list[DiffFile] = field(default_factory=list)


@dataclass(frozen=True)
class CheckResult:
    name: str
    exit_code: int
    log_sha256: str | None
    ran_at: str | None


@dataclass(frozen=True)
class NoveltyContext:
    first_seen_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewArtifact:
    schema_version: str
    artifact_id: str
    harness_id: str
    harness_run_id: str | None
    submitted_at: str
    intent: Intent
    diff: Diff
    checks: list[CheckResult]
    prior_receipts: list[str]
    novelty_context: NoveltyContext
    explicit_gaps: list[str]

    # ── construction ──────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        harness_id: str,
        harness_run_id: str | None,
        submitted_at: str,
        intent: Intent,
        diff: Diff,
        checks: list[CheckResult],
        prior_receipts: list[str],
        novelty_context: NoveltyContext,
        explicit_gaps: list[str],
    ) -> ReviewArtifact:
        """Build an artifact, computing ``artifact_id`` from the canonical JSON.

        The caller supplies every §2 field *except* ``artifact_id``; the id is
        derived, never chosen.
        """
        core = _core_dict(
            schema_version=SCHEMA_VERSION,
            harness_id=harness_id,
            harness_run_id=harness_run_id,
            submitted_at=submitted_at,
            intent=intent,
            diff=diff,
            checks=checks,
            prior_receipts=prior_receipts,
            novelty_context=novelty_context,
            explicit_gaps=explicit_gaps,
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            artifact_id=_hash_canonical(core),
            harness_id=harness_id,
            harness_run_id=harness_run_id,
            submitted_at=submitted_at,
            intent=intent,
            diff=diff,
            checks=list(checks),
            prior_receipts=list(prior_receipts),
            novelty_context=novelty_context,
            explicit_gaps=list(explicit_gaps),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ReviewArtifact:
        """Rebuild an artifact from a stored/raw mapping, validating it first.

        Fail-closed: unknown fields, oversize diffs, id mismatches, and
        fabricated (null-but-ungapped) fields all raise
        :class:`ArtifactValidationError`.
        """
        validate(raw)
        intent = raw["intent"]
        diff = raw["diff"]
        return cls(
            schema_version=raw["schema_version"],
            artifact_id=raw["artifact_id"],
            harness_id=raw["harness_id"],
            harness_run_id=raw["harness_run_id"],
            submitted_at=raw["submitted_at"],
            intent=Intent(
                plan_ref=intent["plan_ref"],
                brief=intent["brief"],
                goals=list(intent["goals"]),
            ),
            diff=Diff(
                base_tree=diff["base_tree"],
                head_tree=diff["head_tree"],
                unified=diff["unified"],
                files=[
                    DiffFile(
                        path=f["path"],
                        change_type=f["change_type"],
                        lines_added=f["lines_added"],
                        lines_removed=f["lines_removed"],
                    )
                    for f in diff["files"]
                ],
            ),
            checks=[
                CheckResult(
                    name=c["name"],
                    exit_code=c["exit_code"],
                    log_sha256=c["log_sha256"],
                    ran_at=c["ran_at"],
                )
                for c in raw["checks"]
            ],
            prior_receipts=list(raw["prior_receipts"]),
            novelty_context=NoveltyContext(
                first_seen_paths=list(raw["novelty_context"]["first_seen_paths"])
            ),
            explicit_gaps=list(raw["explicit_gaps"]),
        )

    # ── serialization ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Full mapping form, including ``artifact_id`` (stable key order)."""
        core = self._core()
        return {
            "schema_version": self.schema_version,
            **core,
            "artifact_id": self.artifact_id,
        }

    def canonical_json(self) -> str:
        """Deterministic serialization of everything *below* ``artifact_id``.

        Sorted keys, compact separators, ASCII-safe — byte-identical for
        identical logical content on any platform.
        """
        return json.dumps(
            self._core(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    def _core(self) -> dict[str, Any]:
        return _core_dict(
            schema_version=self.schema_version,
            harness_id=self.harness_id,
            harness_run_id=self.harness_run_id,
            submitted_at=self.submitted_at,
            intent=self.intent,
            diff=self.diff,
            checks=self.checks,
            prior_receipts=self.prior_receipts,
            novelty_context=self.novelty_context,
            explicit_gaps=self.explicit_gaps,
        )


def _core_dict(
    *,
    schema_version: str,
    harness_id: str,
    harness_run_id: str | None,
    submitted_at: str,
    intent: Intent,
    diff: Diff,
    checks: list[CheckResult],
    prior_receipts: list[str],
    novelty_context: NoveltyContext,
    explicit_gaps: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "harness_id": harness_id,
        "harness_run_id": harness_run_id,
        "submitted_at": submitted_at,
        "intent": {
            "plan_ref": intent.plan_ref,
            "brief": intent.brief,
            "goals": list(intent.goals),
        },
        "diff": {
            "base_tree": diff.base_tree,
            "head_tree": diff.head_tree,
            "unified": diff.unified,
            "files": [
                {
                    "path": f.path,
                    "change_type": f.change_type,
                    "lines_added": f.lines_added,
                    "lines_removed": f.lines_removed,
                }
                for f in diff.files
            ],
        },
        "checks": [
            {
                "name": c.name,
                "exit_code": c.exit_code,
                "log_sha256": c.log_sha256,
                "ran_at": c.ran_at,
            }
            for c in checks
        ],
        "prior_receipts": list(prior_receipts),
        "novelty_context": {
            "first_seen_paths": list(novelty_context.first_seen_paths),
        },
        "explicit_gaps": list(explicit_gaps),
    }


def _hash_canonical(core: dict[str, Any]) -> str:
    canonical = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Boundary validation (fail-closed)
# ─────────────────────────────────────────────────────────────────────

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_id",
        "harness_id",
        "harness_run_id",
        "submitted_at",
        "intent",
        "diff",
        "checks",
        "prior_receipts",
        "novelty_context",
        "explicit_gaps",
    }
)
_INTENT_FIELDS = frozenset({"plan_ref", "brief", "goals"})
_DIFF_FIELDS = frozenset({"base_tree", "head_tree", "unified", "files"})
_DIFF_FILE_FIELDS = frozenset({"path", "change_type", "lines_added", "lines_removed"})
_CHECK_FIELDS = frozenset({"name", "exit_code", "log_sha256", "ran_at"})
_NOVELTY_FIELDS = frozenset({"first_seen_paths"})

#: Dotted paths of every field that may legally be ``null``. Anything null
#: that is NOT listed here — or listed under ``explicit_gaps`` without being
#: null — fails validation (the anti-fabrication rule).
_NULLABLE_PATHS = frozenset(
    {
        "harness_run_id",
        "intent.plan_ref",
        "intent.brief",
        "diff.base_tree",
        "diff.head_tree",
        "diff.unified",
        "checks[].log_sha256",
        "checks[].ran_at",
    }
)


def _reject_unknown(
    mapping: dict[str, Any], allowed: frozenset[str], where: str
) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ArtifactValidationError(f"unknown field(s) at {where}: {sorted(unknown)}")


def _null_paths(raw: dict[str, Any]) -> set[str]:
    """Dotted paths of every null-valued nullable field in the payload."""
    nulls: set[str] = set()
    if raw.get("harness_run_id") is None:
        nulls.add("harness_run_id")
    intent = raw["intent"]
    if intent.get("plan_ref") is None:
        nulls.add("intent.plan_ref")
    if intent.get("brief") is None:
        nulls.add("intent.brief")
    diff = raw["diff"]
    for key in ("base_tree", "head_tree", "unified"):
        if diff.get(key) is None:
            nulls.add(f"diff.{key}")
    for check in raw["checks"]:
        for key in ("log_sha256", "ran_at"):
            if check.get(key) is None:
                nulls.add(f"checks[].{key}")
    return nulls


def validate(raw: Any) -> None:
    """Fail-closed boundary validation of a raw artifact mapping.

    Rejects: non-dict payloads, unknown fields (at any depth), wrong
    ``schema_version``, bad ``harness_id``, oversize diffs / lists, type
    violations, ``artifact_id`` mismatches, and — the anti-fabrication
    rule — any ``null`` field not listed under ``explicit_gaps`` (or any
    ``explicit_gaps`` entry naming a non-null field).
    """
    if not isinstance(raw, dict):
        raise ArtifactValidationError("artifact must be a mapping")
    _reject_unknown(raw, _TOP_LEVEL_FIELDS, "artifact")
    missing = _TOP_LEVEL_FIELDS - set(raw)
    if missing:
        raise ArtifactValidationError(f"missing field(s): {sorted(missing)}")

    if raw["schema_version"] != SCHEMA_VERSION:
        raise ArtifactValidationError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {raw['schema_version']!r}"
        )
    if raw["harness_id"] not in HARNESS_IDS:
        raise ArtifactValidationError(f"unknown harness_id: {raw['harness_id']!r}")
    if not isinstance(raw["submitted_at"], str) or not raw["submitted_at"]:
        raise ArtifactValidationError("submitted_at must be a non-empty string")

    intent = raw["intent"]
    if not isinstance(intent, dict):
        raise ArtifactValidationError("intent must be a mapping")
    _reject_unknown(intent, _INTENT_FIELDS, "intent")
    if intent["plan_ref"] is not None and not isinstance(intent["plan_ref"], str):
        raise ArtifactValidationError("intent.plan_ref must be a string or null")
    if intent["brief"] is not None and not isinstance(intent["brief"], str):
        raise ArtifactValidationError("intent.brief must be a string or null")
    if not isinstance(intent["goals"], list) or not all(
        isinstance(g, str) for g in intent["goals"]
    ):
        raise ArtifactValidationError("intent.goals must be a list of strings")
    if len(intent["goals"]) > MAX_GOALS:
        raise ArtifactValidationError(f"intent.goals exceeds cap ({MAX_GOALS})")

    diff = raw["diff"]
    if not isinstance(diff, dict):
        raise ArtifactValidationError("diff must be a mapping")
    _reject_unknown(diff, _DIFF_FIELDS, "diff")
    for key in ("base_tree", "head_tree"):
        if diff[key] is not None and not isinstance(diff[key], str):
            raise ArtifactValidationError(f"diff.{key} must be a string or null")
    unified = diff["unified"]
    if unified is not None:
        if not isinstance(unified, str):
            raise ArtifactValidationError("diff.unified must be a string or null")
        if len(unified.encode("utf-8")) > MAX_UNIFIED_DIFF_BYTES:
            raise ArtifactValidationError(
                f"diff.unified exceeds size cap ({MAX_UNIFIED_DIFF_BYTES} bytes); "
                "adapters must truncate with the truncation marker"
            )
    files = diff["files"]
    if not isinstance(files, list):
        raise ArtifactValidationError("diff.files must be a list")
    if len(files) > MAX_FILES:
        raise ArtifactValidationError(f"diff.files exceeds cap ({MAX_FILES})")
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise ArtifactValidationError(f"diff.files[{i}] must be a mapping")
        _reject_unknown(f, _DIFF_FILE_FIELDS, f"diff.files[{i}]")
        if not isinstance(f["path"], str) or not f["path"]:
            raise ArtifactValidationError(f"diff.files[{i}].path must be non-empty")
        if f["change_type"] not in CHANGE_TYPES:
            raise ArtifactValidationError(
                f"diff.files[{i}].change_type must be one of {sorted(CHANGE_TYPES)}"
            )
        for key in ("lines_added", "lines_removed"):
            if not isinstance(f[key], int) or isinstance(f[key], bool) or f[key] < 0:
                raise ArtifactValidationError(
                    f"diff.files[{i}].{key} must be a non-negative integer"
                )

    checks = raw["checks"]
    if not isinstance(checks, list):
        raise ArtifactValidationError("checks must be a list")
    if len(checks) > MAX_CHECKS:
        raise ArtifactValidationError(f"checks exceeds cap ({MAX_CHECKS})")
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            raise ArtifactValidationError(f"checks[{i}] must be a mapping")
        _reject_unknown(c, _CHECK_FIELDS, f"checks[{i}]")
        if not isinstance(c["name"], str) or not c["name"]:
            raise ArtifactValidationError(f"checks[{i}].name must be non-empty")
        if not isinstance(c["exit_code"], int) or isinstance(c["exit_code"], bool):
            raise ArtifactValidationError(f"checks[{i}].exit_code must be an integer")
        for key in ("log_sha256", "ran_at"):
            if c[key] is not None and not isinstance(c[key], str):
                raise ArtifactValidationError(
                    f"checks[{i}].{key} must be a string or null"
                )

    if not isinstance(raw["prior_receipts"], list) or not all(
        isinstance(r, str) for r in raw["prior_receipts"]
    ):
        raise ArtifactValidationError("prior_receipts must be a list of strings")

    novelty = raw["novelty_context"]
    if not isinstance(novelty, dict):
        raise ArtifactValidationError("novelty_context must be a mapping")
    _reject_unknown(novelty, _NOVELTY_FIELDS, "novelty_context")
    paths = novelty["first_seen_paths"]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ArtifactValidationError(
            "novelty_context.first_seen_paths must be a list of strings"
        )
    if len(paths) > MAX_PATHS:
        raise ArtifactValidationError(f"first_seen_paths exceeds cap ({MAX_PATHS})")

    gaps = raw["explicit_gaps"]
    if not isinstance(gaps, list) or not all(isinstance(g, str) for g in gaps):
        raise ArtifactValidationError("explicit_gaps must be a list of strings")
    unknown_gaps = set(gaps) - _NULLABLE_PATHS
    if unknown_gaps:
        raise ArtifactValidationError(
            f"explicit_gaps names unknown field(s): {sorted(unknown_gaps)}"
        )

    # Anti-fabrication: nulls and gaps must match exactly, in both directions.
    nulls = _null_paths(raw)
    ungapped = nulls - set(gaps)
    if ungapped:
        raise ArtifactValidationError(
            f"null field(s) not listed under explicit_gaps (fabrication risk): "
            f"{sorted(ungapped)}"
        )
    non_null_gaps = set(gaps) - nulls
    if non_null_gaps:
        raise ArtifactValidationError(
            f"explicit_gaps names non-null field(s): {sorted(non_null_gaps)}"
        )

    # Content-address check: artifact_id must be the hash of everything else.
    core = {k: v for k, v in raw.items() if k != "artifact_id"}
    expected = hashlib.sha256(
        json.dumps(
            core, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    if raw["artifact_id"] != expected:
        raise ArtifactValidationError(
            "artifact_id does not match sha256(canonical_json); payload was "
            "mutated or mis-hashed"
        )


def truncate_unified(diff_text: str, limit: int = MAX_UNIFIED_DIFF_BYTES) -> str:
    """Cap a unified diff at ``limit`` bytes, appending the truncation marker.

    Adapters call this before building the artifact so the payload always
    fits the boundary cap.
    """
    encoded = diff_text.encode("utf-8")
    if len(encoded) <= limit:
        return diff_text
    marker = TRUNCATION_MARKER.encode("utf-8")
    cut = encoded[: limit - len(marker)]
    # Avoid splitting a UTF-8 sequence at the cut point.
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return (cut + marker).decode("utf-8")


# ─────────────────────────────────────────────────────────────────────
# Write-once JSONL store
# ─────────────────────────────────────────────────────────────────────


def _audit_dir() -> str:
    return os.environ.get(
        "PRISMATIC_AUDIT_DIR", os.path.expanduser("~/.prismatic/audit")
    )


def _artifact_store_path() -> str:
    return os.path.join(_audit_dir(), "review-artifacts.jsonl")


def store_artifact(artifact: ReviewArtifact) -> str:
    """Append an artifact to the store. Write-once: re-storing the same
    ``artifact_id`` is a no-op. Returns the ``artifact_id``."""
    validate(artifact.to_dict())
    path = _artifact_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if artifact_exists(artifact.artifact_id):
        return artifact.artifact_id
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(artifact.to_dict(), sort_keys=True, ensure_ascii=True) + "\n"
        )
    return artifact.artifact_id


def artifact_exists(artifact_id: str) -> bool:
    """True if an artifact with this id is already stored."""
    path = _artifact_store_path()
    if not os.path.exists(path):
        return False
    needle = json.dumps(artifact_id).encode("utf-8")
    with open(path, "rb") as fh:
        for line in fh:
            if needle in line:
                try:
                    if json.loads(line)["artifact_id"] == artifact_id:
                        return True
                except (json.JSONDecodeError, KeyError):
                    continue
    return False


def load_artifact(artifact_id: str) -> ReviewArtifact:
    """Load a stored artifact by id (validates on the way in)."""
    path = _artifact_store_path()
    if not os.path.exists(path):
        raise KeyError(f"artifact not found: {artifact_id}")
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and raw.get("artifact_id") == artifact_id:
                return ReviewArtifact.from_dict(raw)
    raise KeyError(f"artifact not found: {artifact_id}")
