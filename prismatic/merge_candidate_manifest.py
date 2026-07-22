"""Pure, immutable merge-candidate evidence and promotion-state contract.

This module validates and serializes an exact candidate artifact.  It does not
instantiate :class:`MergeFactoryStore`, submit decisions, acquire locks, call
GitHub, mutate Linear, merge, release, or deploy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

SCHEMA_VERSION = 1
PROOF_POLICY_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NATIVE_PATH_TYPE = type(Path())


class ManifestValidationError(ValueError):
    """Raised when candidate evidence is malformed or semantically stale."""


class PromotionState(str, Enum):
    CANDIDATE = "CANDIDATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CLEAN = "CLEAN"
    CI_GREEN = "CI_GREEN"
    MERGE_ELIGIBLE = "MERGE_ELIGIBLE"
    MERGED = "MERGED"
    RELEASE_VERIFIED = "RELEASE_VERIFIED"


class RiskTier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


_ALLOWED_PROOF_CLASSES = frozenset(
    {
        "focused",
        "canonical",
        "package",
        "failure",
        "recovery",
        "rollback",
        "browser",
        "real_data",
    }
)
_TIER_PROOFS = {
    RiskTier.A: frozenset({"focused"}),
    RiskTier.B: frozenset({"focused", "canonical", "package"}),
    RiskTier.C: frozenset(
        {"focused", "canonical", "package", "failure", "recovery", "rollback"}
    ),
}
_DASHBOARD_PROOFS = frozenset({"browser", "real_data"})


def _exact_str(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise ManifestValidationError(f"{name} must be an exact string")
    if _CONTROL_RE.search(value):
        raise ManifestValidationError(f"{name} contains control characters")
    if value != value.strip():
        raise ManifestValidationError(f"{name} must not contain outer whitespace")
    if unicodedata.normalize("NFC", value) != value:
        raise ManifestValidationError(f"{name} must use NFC normalization")
    if not allow_empty and not value:
        raise ManifestValidationError(f"{name} must not be empty")
    return value


def _sha(value: Any, name: str) -> str:
    value = _exact_str(value, name)
    if not _SHA_RE.fullmatch(value):
        raise ManifestValidationError(f"{name} must be a lowercase 40-hex commit SHA")
    return value


def _digest(value: Any, name: str) -> str:
    value = _exact_str(value, name)
    if not _DIGEST_RE.fullmatch(value):
        raise ManifestValidationError(f"{name} must be a lowercase 64-hex SHA-256")
    return value


def _exact_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ManifestValidationError(f"{name} must be an exact boolean")
    return value


def _exact_int(value: Any, name: str, *, expected: int | None = None) -> int:
    if type(value) is not int:
        raise ManifestValidationError(f"{name} must be an exact integer")
    if expected is not None and value != expected:
        raise ManifestValidationError(f"{name} must equal {expected}")
    if expected is None and value <= 0:
        raise ManifestValidationError(f"{name} must be positive")
    return value


def _strict_keys(value: Any, expected: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ManifestValidationError(f"{name} must be an exact JSON object")
    keys = set(value)
    if any(type(key) is not str for key in keys):
        raise ManifestValidationError(f"{name} keys must be exact strings")
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing or unknown:
        pieces = []
        if missing:
            pieces.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            pieces.append(f"unknown fields: {', '.join(unknown)}")
        raise ManifestValidationError(f"{name} {'; '.join(pieces)}")
    return value


def _strict_list(value: Any, name: str) -> list[Any]:
    if type(value) is not list:
        raise ManifestValidationError(f"{name} must be an exact JSON array")
    return value


def _validate_changed_path(value: Any, name: str) -> str:
    path = _exact_str(value, name)
    if "\\" in path:
        raise ManifestValidationError(f"{name} must use POSIX separators")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ManifestValidationError(
            f"{name} must be a normalized repository-relative path"
        )
    if pure.as_posix() != path:
        raise ManifestValidationError(
            f"{name} must be a normalized repository-relative path"
        )
    return path


def _canonical_tuple(
    values: Any,
    name: str,
    validator: Any,
    *,
    non_empty: bool = True,
) -> tuple[Any, ...]:
    if type(values) is not tuple:
        raise ManifestValidationError(f"{name} must be an exact tuple")
    result = tuple(
        validator(value, f"{name}[{index}]") for index, value in enumerate(values)
    )
    if non_empty and not result:
        raise ManifestValidationError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise ManifestValidationError(f"{name} contains duplicates")
    if result != tuple(sorted(result)):
        raise ManifestValidationError(f"{name} must be sorted canonically")
    return result


def _normalized_input_tuple(
    values: Any,
    name: str,
    validator: Any,
    *,
    non_empty: bool = True,
) -> tuple[Any, ...]:
    if type(values) not in {list, tuple}:
        raise ManifestValidationError(f"{name} input must be a list or tuple")
    result = tuple(
        validator(value, f"{name}[{index}]") for index, value in enumerate(values)
    )
    if non_empty and not result:
        raise ManifestValidationError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise ManifestValidationError(f"{name} contains duplicates")
    return tuple(sorted(result))


def _normalized_records(
    values: Any,
    name: str,
    expected_type: type[Any],
    key: Any,
) -> tuple[Any, ...]:
    if type(values) not in {list, tuple}:
        raise ManifestValidationError(f"{name} input must be a list or tuple")
    if any(type(value) is not expected_type for value in values):
        raise ManifestValidationError(f"{name} contains an invalid value")
    return tuple(sorted(values, key=key))


def _final_class(cls: type[Any]) -> None:
    raise TypeError(f"{cls.__name__} is final and cannot be subclassed")


@dataclass(frozen=True)
class VerificationEvidence:
    proof_class: str
    command: str
    summary: str
    result: str
    log_path: str
    log_sha256: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        _final_class(cls)

    def __post_init__(self) -> None:
        proof_class = _exact_str(self.proof_class, "proof_class")
        if proof_class not in _ALLOWED_PROOF_CLASSES:
            raise ManifestValidationError(f"unknown proof_class: {proof_class}")
        _exact_str(self.command, "command")
        _exact_str(self.summary, "summary")
        if _exact_str(self.result, "result") != "PASS":
            raise ManifestValidationError("verification result must be PASS")
        _exact_str(self.log_path, "log_path")
        _digest(self.log_sha256, "log_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "log_path": self.log_path,
            "log_sha256": self.log_sha256,
            "proof_class": self.proof_class,
            "result": self.result,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Any) -> VerificationEvidence:
        data = _strict_keys(
            value,
            {"proof_class", "command", "summary", "result", "log_path", "log_sha256"},
            "verification evidence",
        )
        return cls(**data)


@dataclass(frozen=True)
class IndependentReview:
    reviewer: str
    review_id: str
    verdict: str
    reviewed_sha: str
    reviewed_manifest_digest: str
    scope_clean: bool
    conflict_free: bool

    def __init_subclass__(cls, **kwargs: Any) -> None:
        _final_class(cls)

    def __post_init__(self) -> None:
        _exact_str(self.reviewer, "reviewer")
        _exact_str(self.review_id, "review_id")
        if _exact_str(self.verdict, "verdict") != "CLEAN":
            raise ManifestValidationError("independent review verdict must be CLEAN")
        _sha(self.reviewed_sha, "reviewed_sha")
        _digest(self.reviewed_manifest_digest, "reviewed_manifest_digest")
        if _exact_bool(self.scope_clean, "scope_clean") is not True:
            raise ManifestValidationError("scope_clean must be true")
        if _exact_bool(self.conflict_free, "conflict_free") is not True:
            raise ManifestValidationError("conflict_free must be true")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_free": self.conflict_free,
            "review_id": self.review_id,
            "reviewed_manifest_digest": self.reviewed_manifest_digest,
            "reviewed_sha": self.reviewed_sha,
            "reviewer": self.reviewer,
            "scope_clean": self.scope_clean,
            "verdict": self.verdict,
        }

    @classmethod
    def from_dict(cls, value: Any) -> IndependentReview:
        data = _strict_keys(
            value,
            {
                "reviewer",
                "review_id",
                "verdict",
                "reviewed_sha",
                "reviewed_manifest_digest",
                "scope_clean",
                "conflict_free",
            },
            "independent review",
        )
        return cls(**data)


@dataclass(frozen=True)
class CICheck:
    name: str
    run_id: int
    conclusion: str
    head_sha: str
    details_url: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        _final_class(cls)

    def __post_init__(self) -> None:
        _exact_str(self.name, "CI check name")
        _exact_int(self.run_id, "CI run_id")
        if _exact_str(self.conclusion, "CI conclusion") != "SUCCESS":
            raise ManifestValidationError("CI conclusion must be SUCCESS")
        _sha(self.head_sha, "CI head_sha")
        url = _exact_str(self.details_url, "CI details_url")
        if not url.startswith("https://github.com/"):
            raise ManifestValidationError("CI details_url must be a GitHub URL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "details_url": self.details_url,
            "head_sha": self.head_sha,
            "name": self.name,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CICheck:
        data = _strict_keys(
            value,
            {"name", "run_id", "conclusion", "head_sha", "details_url"},
            "CI check",
        )
        return cls(**data)


@dataclass(frozen=True)
class ReleaseEvidence:
    release_id: str
    merge_sha: str
    command: str
    summary: str
    log_path: str
    log_sha256: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        _final_class(cls)

    def __post_init__(self) -> None:
        _exact_str(self.release_id, "release_id")
        _sha(self.merge_sha, "release merge_sha")
        _exact_str(self.command, "release command")
        _exact_str(self.summary, "release summary")
        _exact_str(self.log_path, "release log_path")
        _digest(self.log_sha256, "release log_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "log_path": self.log_path,
            "log_sha256": self.log_sha256,
            "merge_sha": self.merge_sha,
            "release_id": self.release_id,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ReleaseEvidence:
        data = _strict_keys(
            value,
            {"release_id", "merge_sha", "command", "summary", "log_path", "log_sha256"},
            "release evidence",
        )
        return cls(**data)


@dataclass(frozen=True)
class MergeCandidateManifest:
    schema_version: int
    proof_policy_version: int
    state: PromotionState
    issue_id: str
    task_id: str
    task_file_sha256: str
    repository: str
    target: str
    base_sha: str
    candidate_sha: str
    changed_paths: tuple[str, ...]
    producer: str
    preserved_candidate_location: str
    risk_tier: RiskTier
    dashboard_change: bool
    required_ci_checks: tuple[str, ...]
    verification_evidence: tuple[VerificationEvidence, ...] = ()
    independent_review: IndependentReview | None = None
    ci_checks: tuple[CICheck, ...] = ()
    non_claims: tuple[str, ...] = ()
    invalidation_reason: str | None = None
    merge_sha: str | None = None
    release_id: str | None = None
    release_evidence: tuple[ReleaseEvidence, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        _final_class(cls)

    def __post_init__(self) -> None:
        _exact_int(self.schema_version, "schema_version", expected=SCHEMA_VERSION)
        _exact_int(
            self.proof_policy_version,
            "proof_policy_version",
            expected=PROOF_POLICY_VERSION,
        )
        if type(self.state) is not PromotionState:
            raise ManifestValidationError("state must be a PromotionState")
        _exact_str(self.issue_id, "issue_id")
        _exact_str(self.task_id, "task_id")
        _digest(self.task_file_sha256, "task_file_sha256")
        _exact_str(self.repository, "repository")
        _exact_str(self.target, "target")
        _sha(self.base_sha, "base_sha")
        _sha(self.candidate_sha, "candidate_sha")
        _canonical_tuple(self.changed_paths, "changed_paths", _validate_changed_path)
        producer = _exact_str(self.producer, "producer")
        _exact_str(self.preserved_candidate_location, "preserved_candidate_location")
        if type(self.risk_tier) is not RiskTier:
            raise ManifestValidationError("risk_tier must be a RiskTier")
        _exact_bool(self.dashboard_change, "dashboard_change")
        _canonical_tuple(self.required_ci_checks, "required_ci_checks", _exact_str)
        _canonical_tuple(self.non_claims, "non_claims", _exact_str, non_empty=False)

        if type(self.verification_evidence) is not tuple:
            raise ManifestValidationError(
                "verification_evidence must be an exact tuple"
            )
        if any(
            type(item) is not VerificationEvidence
            for item in self.verification_evidence
        ):
            raise ManifestValidationError(
                "verification_evidence contains an invalid value"
            )
        proof_classes = tuple(item.proof_class for item in self.verification_evidence)
        if len(set(proof_classes)) != len(proof_classes):
            raise ManifestValidationError(
                "verification_evidence contains duplicate proof classes"
            )
        if proof_classes != tuple(sorted(proof_classes)):
            raise ManifestValidationError(
                "verification_evidence must be sorted by proof class"
            )

        if (
            self.independent_review is not None
            and type(self.independent_review) is not IndependentReview
        ):
            raise ManifestValidationError("independent_review has an invalid value")
        if self.independent_review is not None:
            if self.independent_review.reviewed_sha != self.candidate_sha:
                raise ManifestValidationError(
                    "independent review is bound to a stale candidate SHA"
                )
            if self.independent_review.reviewer.casefold() == producer.casefold():
                raise ManifestValidationError(
                    "independent reviewer must differ from producer"
                )

        if type(self.ci_checks) is not tuple:
            raise ManifestValidationError("ci_checks must be an exact tuple")
        if any(type(item) is not CICheck for item in self.ci_checks):
            raise ManifestValidationError("ci_checks contains an invalid value")
        check_names = tuple(item.name for item in self.ci_checks)
        check_run_ids = tuple(item.run_id for item in self.ci_checks)
        if len(set(check_names)) != len(check_names):
            raise ManifestValidationError("ci_checks contains duplicate names")
        if len(set(check_run_ids)) != len(check_run_ids):
            raise ManifestValidationError("ci_checks contains duplicate run IDs")
        if check_names != tuple(sorted(check_names)):
            raise ManifestValidationError("ci_checks must be sorted by name")
        if any(item.head_sha != self.candidate_sha for item in self.ci_checks):
            raise ManifestValidationError("CI check is bound to a stale candidate SHA")

        if self.invalidation_reason is not None:
            _exact_str(self.invalidation_reason, "invalidation_reason")
        if self.merge_sha is not None:
            _sha(self.merge_sha, "merge_sha")
        if self.release_id is not None:
            _exact_str(self.release_id, "release_id")
        if type(self.release_evidence) is not tuple:
            raise ManifestValidationError("release_evidence must be an exact tuple")
        if any(type(item) is not ReleaseEvidence for item in self.release_evidence):
            raise ManifestValidationError("release_evidence contains an invalid value")
        release_ids = tuple(item.release_id for item in self.release_evidence)
        if len(set(release_ids)) != len(release_ids):
            raise ManifestValidationError(
                "release_evidence contains duplicate release IDs"
            )
        if release_ids != tuple(sorted(release_ids)):
            raise ManifestValidationError(
                "release_evidence must be sorted by release ID"
            )

        self._validate_state()

    def _required_proofs(self) -> frozenset[str]:
        required = _TIER_PROOFS[self.risk_tier]
        if self.dashboard_change:
            required = required | _DASHBOARD_PROOFS
        return required

    def _has_required_proofs(self) -> bool:
        return self._required_proofs().issubset(
            {item.proof_class for item in self.verification_evidence}
        )

    def _validate_state(self) -> None:
        rank = list(PromotionState).index(self.state)
        review_rank = list(PromotionState).index(PromotionState.CLEAN)
        ci_rank = list(PromotionState).index(PromotionState.CI_GREEN)
        merged_rank = list(PromotionState).index(PromotionState.MERGED)
        release_rank = list(PromotionState).index(PromotionState.RELEASE_VERIFIED)

        if rank >= list(PromotionState).index(PromotionState.REVIEW_REQUIRED):
            if not self._has_required_proofs():
                raise ManifestValidationError(
                    "state requires all risk-tier proof classes"
                )
        if rank < review_rank and self.independent_review is not None:
            raise ManifestValidationError("review evidence is premature for state")
        if rank >= review_rank:
            if self.independent_review is None:
                raise ManifestValidationError(
                    "state requires an independent CLEAN review"
                )
            expected = self._digest_without_review()
            if self.independent_review.reviewed_manifest_digest != expected:
                raise ManifestValidationError(
                    "review is bound to a stale manifest digest"
                )
        if rank < ci_rank and self.ci_checks:
            raise ManifestValidationError("CI evidence is premature for state")
        if rank >= ci_rank:
            if not self.ci_checks:
                raise ManifestValidationError("state requires CI checks")
            if set(item.name for item in self.ci_checks) != set(
                self.required_ci_checks
            ):
                raise ManifestValidationError(
                    "CI checks do not exactly match required_ci_checks"
                )
        if rank < merged_rank and self.merge_sha is not None:
            raise ManifestValidationError("merge_sha is premature for state")
        if rank >= merged_rank and self.merge_sha is None:
            raise ManifestValidationError("state requires merge_sha")
        if rank < release_rank and (
            self.release_id is not None or self.release_evidence
        ):
            raise ManifestValidationError("release evidence is premature for state")
        if rank >= release_rank:
            if self.release_id is None or not self.release_evidence:
                raise ManifestValidationError(
                    "state requires release identity and evidence"
                )
            if any(
                item.release_id != self.release_id or item.merge_sha != self.merge_sha
                for item in self.release_evidence
            ):
                raise ManifestValidationError(
                    "release evidence is bound to a stale merge"
                )
        if (
            self.state is not PromotionState.CANDIDATE
            and self.invalidation_reason is not None
        ):
            raise ManifestValidationError(
                "invalidation_reason is only valid for CANDIDATE"
            )

    @classmethod
    def create(
        cls,
        *,
        issue_id: str,
        task_id: str,
        task_file_sha256: str,
        repository: str,
        target: str,
        base_sha: str,
        candidate_sha: str,
        changed_paths: Iterable[str],
        producer: str,
        preserved_candidate_location: str,
        risk_tier: RiskTier | str,
        dashboard_change: bool,
        required_ci_checks: Iterable[str],
        non_claims: Iterable[str] = (),
    ) -> MergeCandidateManifest:
        normalized_paths = _normalized_input_tuple(
            changed_paths, "changed_paths", _validate_changed_path
        )
        normalized_required_checks = _normalized_input_tuple(
            required_ci_checks, "required_ci_checks", _exact_str
        )
        normalized_non_claims = _normalized_input_tuple(
            non_claims, "non_claims", _exact_str, non_empty=False
        )
        if type(risk_tier) is str:
            try:
                risk_tier = RiskTier(risk_tier)
            except ValueError as exc:
                raise ManifestValidationError("unknown risk_tier") from exc
        elif type(risk_tier) is not RiskTier:
            raise ManifestValidationError(
                "risk_tier must be a RiskTier or exact string"
            )
        validated_risk_tier: RiskTier = risk_tier
        return cls(
            schema_version=SCHEMA_VERSION,
            proof_policy_version=PROOF_POLICY_VERSION,
            state=PromotionState.CANDIDATE,
            issue_id=issue_id,
            task_id=task_id,
            task_file_sha256=task_file_sha256,
            repository=repository,
            target=target,
            base_sha=base_sha,
            candidate_sha=candidate_sha,
            changed_paths=normalized_paths,
            producer=producer,
            preserved_candidate_location=preserved_candidate_location,
            risk_tier=validated_risk_tier,
            dashboard_change=dashboard_change,
            required_ci_checks=normalized_required_checks,
            non_claims=normalized_non_claims,
        )

    def request_review(
        self, evidence: Iterable[VerificationEvidence]
    ) -> MergeCandidateManifest:
        if self.state is not PromotionState.CANDIDATE:
            raise ManifestValidationError("review can only be requested from CANDIDATE")
        ordered = _normalized_records(
            evidence,
            "verification evidence",
            VerificationEvidence,
            lambda item: item.proof_class,
        )
        return replace(
            self,
            state=PromotionState.REVIEW_REQUIRED,
            verification_evidence=ordered,
            invalidation_reason=None,
        )

    def record_review(self, review: IndependentReview) -> MergeCandidateManifest:
        if self.state is not PromotionState.REVIEW_REQUIRED:
            raise ManifestValidationError(
                "review can only be recorded for REVIEW_REQUIRED"
            )
        return replace(self, state=PromotionState.CLEAN, independent_review=review)

    def record_ci(self, checks: Iterable[CICheck]) -> MergeCandidateManifest:
        if self.state is not PromotionState.CLEAN:
            raise ManifestValidationError("CI can only be recorded for CLEAN")
        ordered = _normalized_records(
            checks, "CI checks", CICheck, lambda item: item.name
        )
        return replace(
            self,
            state=PromotionState.CI_GREEN,
            ci_checks=ordered,
        )

    def mark_merge_eligible(self) -> MergeCandidateManifest:
        if self.state is not PromotionState.CI_GREEN:
            raise ManifestValidationError("merge eligibility requires CI_GREEN")
        return replace(self, state=PromotionState.MERGE_ELIGIBLE)

    def mark_merged(
        self, *, candidate_sha: str, merge_sha: str
    ) -> MergeCandidateManifest:
        if self.state is not PromotionState.MERGE_ELIGIBLE:
            raise ManifestValidationError("merge recording requires MERGE_ELIGIBLE")
        if _sha(candidate_sha, "merged candidate_sha") != self.candidate_sha:
            raise ManifestValidationError(
                "merge result is bound to a stale candidate SHA"
            )
        return replace(
            self,
            state=PromotionState.MERGED,
            merge_sha=_sha(merge_sha, "merge_sha"),
        )

    def mark_release_verified(
        self, *, release_id: str, evidence: Iterable[ReleaseEvidence]
    ) -> MergeCandidateManifest:
        if self.state is not PromotionState.MERGED:
            raise ManifestValidationError("release verification requires MERGED")
        ordered = _normalized_records(
            evidence,
            "release evidence",
            ReleaseEvidence,
            lambda item: item.release_id,
        )
        return replace(
            self,
            state=PromotionState.RELEASE_VERIFIED,
            release_id=_exact_str(release_id, "release_id"),
            release_evidence=ordered,
        )

    def rebind_candidate(
        self,
        *,
        base_sha: str,
        candidate_sha: str,
        task_file_sha256: str,
        changed_paths: Iterable[str],
    ) -> MergeCandidateManifest:
        new_paths = _normalized_input_tuple(
            changed_paths, "changed_paths", _validate_changed_path
        )
        validated_base_sha = _sha(base_sha, "base_sha")
        validated_candidate_sha = _sha(candidate_sha, "candidate_sha")
        validated_task_file_sha256 = _digest(task_file_sha256, "task_file_sha256")
        changes = []
        for name, old, new in (
            ("base_sha", self.base_sha, validated_base_sha),
            ("candidate_sha", self.candidate_sha, validated_candidate_sha),
            ("task_file_sha256", self.task_file_sha256, validated_task_file_sha256),
            ("changed_paths", self.changed_paths, new_paths),
        ):
            if old != new:
                changes.append(name)
        if not changes:
            raise ManifestValidationError(
                "rebind requires at least one changed binding"
            )
        return replace(
            self,
            state=PromotionState.CANDIDATE,
            base_sha=validated_base_sha,
            candidate_sha=validated_candidate_sha,
            task_file_sha256=validated_task_file_sha256,
            changed_paths=new_paths,
            verification_evidence=(),
            independent_review=None,
            ci_checks=(),
            invalidation_reason="bindings_changed:" + ",".join(sorted(changes)),
            merge_sha=None,
            release_id=None,
            release_evidence=(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "changed_paths": list(self.changed_paths),
            "ci_checks": [item.to_dict() for item in self.ci_checks],
            "dashboard_change": self.dashboard_change,
            "independent_review": (
                self.independent_review.to_dict() if self.independent_review else None
            ),
            "invalidation_reason": self.invalidation_reason,
            "issue_id": self.issue_id,
            "merge_sha": self.merge_sha,
            "non_claims": list(self.non_claims),
            "preserved_candidate_location": self.preserved_candidate_location,
            "producer": self.producer,
            "proof_policy_version": self.proof_policy_version,
            "release_evidence": [item.to_dict() for item in self.release_evidence],
            "release_id": self.release_id,
            "repository": self.repository,
            "required_ci_checks": list(self.required_ci_checks),
            "risk_tier": self.risk_tier.value,
            "schema_version": self.schema_version,
            "state": self.state.value,
            "target": self.target,
            "task_file_sha256": self.task_file_sha256,
            "task_id": self.task_id,
            "verification_evidence": [
                item.to_dict() for item in self.verification_evidence
            ],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def evidence_digest(self) -> str:
        projection = {
            "ci_checks": [item.to_dict() for item in self.ci_checks],
            "independent_review": (
                self.independent_review.to_dict() if self.independent_review else None
            ),
            "non_claims": list(self.non_claims),
            "release_evidence": [item.to_dict() for item in self.release_evidence],
            "verification_evidence": [
                item.to_dict() for item in self.verification_evidence
            ],
        }
        raw = json.dumps(
            projection,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def factory_bindings(self) -> dict[str, str]:
        if self.state is not PromotionState.MERGE_ELIGIBLE:
            raise ManifestValidationError("factory bindings require MERGE_ELIGIBLE")
        return {
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "evidence_digest": self.evidence_digest(),
            "issue_id": self.issue_id,
            "manifest_digest": self.digest(),
            "repository": self.repository,
            "target": self.target,
        }

    def _digest_without_review(self) -> str:
        candidate = replace(
            self,
            state=PromotionState.REVIEW_REQUIRED,
            independent_review=None,
            ci_checks=(),
            merge_sha=None,
            release_id=None,
            release_evidence=(),
        )
        return candidate.digest()

    @classmethod
    def from_dict(cls, value: Any) -> MergeCandidateManifest:
        fields = {
            "schema_version",
            "proof_policy_version",
            "state",
            "issue_id",
            "task_id",
            "task_file_sha256",
            "repository",
            "target",
            "base_sha",
            "candidate_sha",
            "changed_paths",
            "producer",
            "preserved_candidate_location",
            "risk_tier",
            "dashboard_change",
            "required_ci_checks",
            "verification_evidence",
            "independent_review",
            "ci_checks",
            "non_claims",
            "invalidation_reason",
            "merge_sha",
            "release_id",
            "release_evidence",
        }
        data = _strict_keys(value, fields, "merge candidate manifest")
        state_value = _exact_str(data["state"], "state")
        risk_tier_value = _exact_str(data["risk_tier"], "risk_tier")
        try:
            state = PromotionState(state_value)
            risk_tier = RiskTier(risk_tier_value)
        except (TypeError, ValueError) as exc:
            raise ManifestValidationError("unknown state or risk_tier") from exc
        review = data["independent_review"]
        return cls(
            schema_version=data["schema_version"],
            proof_policy_version=data["proof_policy_version"],
            state=state,
            issue_id=data["issue_id"],
            task_id=data["task_id"],
            task_file_sha256=data["task_file_sha256"],
            repository=data["repository"],
            target=data["target"],
            base_sha=data["base_sha"],
            candidate_sha=data["candidate_sha"],
            changed_paths=tuple(_strict_list(data["changed_paths"], "changed_paths")),
            producer=data["producer"],
            preserved_candidate_location=data["preserved_candidate_location"],
            risk_tier=risk_tier,
            dashboard_change=data["dashboard_change"],
            required_ci_checks=tuple(
                _strict_list(data["required_ci_checks"], "required_ci_checks")
            ),
            verification_evidence=tuple(
                VerificationEvidence.from_dict(item)
                for item in _strict_list(
                    data["verification_evidence"], "verification_evidence"
                )
            ),
            independent_review=(
                IndependentReview.from_dict(review) if review is not None else None
            ),
            ci_checks=tuple(
                CICheck.from_dict(item)
                for item in _strict_list(data["ci_checks"], "ci_checks")
            ),
            non_claims=tuple(_strict_list(data["non_claims"], "non_claims")),
            invalidation_reason=data["invalidation_reason"],
            merge_sha=data["merge_sha"],
            release_id=data["release_id"],
            release_evidence=tuple(
                ReleaseEvidence.from_dict(item)
                for item in _strict_list(data["release_evidence"], "release_evidence")
            ),
        )

    @classmethod
    def from_json(cls, value: str) -> MergeCandidateManifest:
        if type(value) is not str:
            raise ManifestValidationError("JSON input must be an exact string")

        def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ManifestValidationError(f"duplicate JSON key: {key}")
                result[key] = item
            return result

        def no_constants(value: str) -> None:
            raise ManifestValidationError(f"non-finite JSON value: {value}")

        try:
            data = json.loads(
                value,
                object_pairs_hook=no_duplicates,
                parse_constant=no_constants,
            )
        except json.JSONDecodeError as exc:
            raise ManifestValidationError("invalid JSON") from exc
        return cls.from_dict(data)

    def write(self, path: str | Path) -> Path:
        if type(path) not in {str, _NATIVE_PATH_TYPE}:
            raise ManifestValidationError("path must be an exact str or Path")
        target = Path(path)
        if target.name != "merge_candidate.json":
            raise ManifestValidationError(
                "manifest filename must be merge_candidate.json"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ManifestValidationError("manifest target must not be a symlink")
        fd, tmp_name = tempfile.mkstemp(
            prefix=".merge_candidate.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            payload = self.canonical_json() + "\n"
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return target

    @classmethod
    def read(cls, path: str | Path) -> MergeCandidateManifest:
        if type(path) not in {str, _NATIVE_PATH_TYPE}:
            raise ManifestValidationError("path must be an exact str or Path")
        target = Path(path)
        if target.name != "merge_candidate.json":
            raise ManifestValidationError(
                "manifest filename must be merge_candidate.json"
            )
        if target.is_symlink():
            raise ManifestValidationError("manifest target must not be a symlink")
        return cls.from_json(target.read_text(encoding="utf-8"))
