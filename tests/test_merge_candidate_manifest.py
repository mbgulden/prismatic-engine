from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    ManifestValidationError,
    MergeCandidateManifest,
    PromotionState,
    ReleaseEvidence,
    RiskTier,
    VerificationEvidence,
)

BASE = "1" * 40
HEAD = "2" * 40
NEW_HEAD = "3" * 40
MERGE = "4" * 40
TASK_DIGEST = "a" * 64
LOG_DIGEST = "b" * 64
REVIEW_REQUIRED_CHECKS = ("build package", "test py3.12")


def make_manifest(
    *, tier: RiskTier | str = RiskTier.A, dashboard: bool = False
) -> MergeCandidateManifest:
    return MergeCandidateManifest.create(
        issue_id="GRO-5000",
        task_id="merge-candidate-manifest-v1",
        task_file_sha256=TASK_DIGEST,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha=BASE,
        candidate_sha=HEAD,
        changed_paths=["docs/contract.md", "prismatic/contract.py"],
        producer="agent:producer",
        preserved_candidate_location="/archive/candidates/GRO-5000",
        risk_tier=tier,
        dashboard_change=dashboard,
        required_ci_checks=list(REVIEW_REQUIRED_CHECKS),
        non_claims=["no deploy", "no Linear mutation"],
    )


def proof(proof_class: str) -> VerificationEvidence:
    return VerificationEvidence(
        proof_class=proof_class,
        command=f"python -m verify {proof_class}",
        summary=f"{proof_class} passed",
        result="PASS",
        log_path=f"/tmp/{proof_class}.log",
        log_sha256=LOG_DIGEST,
    )


def proofs_for(tier: RiskTier, dashboard: bool = False) -> list[VerificationEvidence]:
    names = {
        RiskTier.A: {"focused"},
        RiskTier.B: {"focused", "canonical", "package"},
        RiskTier.C: {
            "focused",
            "canonical",
            "package",
            "failure",
            "recovery",
            "rollback",
        },
    }[tier]
    if dashboard:
        names |= {"browser", "real_data"}
    return [proof(name) for name in sorted(names)]


def clean_review(manifest: MergeCandidateManifest) -> IndependentReview:
    return IndependentReview(
        reviewer="agent:independent-reviewer",
        review_id="review-5000",
        verdict="CLEAN",
        reviewed_sha=manifest.candidate_sha,
        reviewed_manifest_digest=manifest.digest(),
        scope_clean=True,
        conflict_free=True,
    )


def ci_checks(head: str = HEAD) -> list[CICheck]:
    return [
        CICheck(
            name=name,
            run_id=index + 100,
            conclusion="SUCCESS",
            head_sha=head,
            details_url=f"https://github.com/mbgulden/prismatic-engine/actions/runs/{index + 100}",
        )
        for index, name in enumerate(REVIEW_REQUIRED_CHECKS)
    ]


def merge_eligible() -> MergeCandidateManifest:
    requested = make_manifest().request_review(proofs_for(RiskTier.A))
    clean = requested.record_review(clean_review(requested))
    return clean.record_ci(ci_checks()).mark_merge_eligible()


def test_happy_path_through_release_verified_and_factory_bindings():
    candidate = make_manifest()
    assert candidate.state is PromotionState.CANDIDATE
    with pytest.raises(ManifestValidationError, match="factory bindings require"):
        candidate.factory_bindings()

    requested = candidate.request_review(proofs_for(RiskTier.A))
    assert requested.state is PromotionState.REVIEW_REQUIRED
    clean = requested.record_review(clean_review(requested))
    assert clean.state is PromotionState.CLEAN
    green = clean.record_ci(ci_checks())
    eligible = green.mark_merge_eligible()
    assert eligible.state is PromotionState.MERGE_ELIGIBLE

    bindings = eligible.factory_bindings()
    assert bindings == {
        "issue_id": "GRO-5000",
        "base_sha": BASE,
        "candidate_sha": HEAD,
        "manifest_digest": eligible.digest(),
        "evidence_digest": eligible.evidence_digest(),
        "repository": "mbgulden/prismatic-engine",
        "target": "main",
    }
    assert len(bindings["manifest_digest"]) == 64
    assert len(bindings["evidence_digest"]) == 64

    merged = eligible.mark_merged(candidate_sha=HEAD, merge_sha=MERGE)
    release = merged.mark_release_verified(
        release_id="release-4",
        evidence=[
            ReleaseEvidence(
                release_id="release-4",
                merge_sha=MERGE,
                command="python -m build && verify-installed-wheel",
                summary="immutable release passed",
                log_path="/tmp/release.log",
                log_sha256=LOG_DIGEST,
            )
        ],
    )
    assert release.state is PromotionState.RELEASE_VERIFIED
    assert release.merge_sha == MERGE


@pytest.mark.parametrize("tier", list(RiskTier))
def test_risk_tier_proof_matrix_is_fail_closed(tier: RiskTier):
    manifest = make_manifest(tier=tier)
    required = proofs_for(tier)
    with pytest.raises(ManifestValidationError, match="risk-tier proof"):
        manifest.request_review(required[:-1])
    assert manifest.request_review(required).state is PromotionState.REVIEW_REQUIRED


def test_dashboard_requires_browser_and_real_data_proof():
    manifest = make_manifest(tier=RiskTier.B, dashboard=True)
    with pytest.raises(ManifestValidationError, match="risk-tier proof"):
        manifest.request_review(proofs_for(RiskTier.B))
    assert (
        manifest.request_review(proofs_for(RiskTier.B, dashboard=True)).state
        is PromotionState.REVIEW_REQUIRED
    )


def test_rebind_clears_every_candidate_bound_artifact_and_changes_digest():
    eligible = merge_eligible()
    merged = eligible.mark_merged(candidate_sha=HEAD, merge_sha=MERGE)
    released = merged.mark_release_verified(
        release_id="release-4",
        evidence=[
            ReleaseEvidence(
                release_id="release-4",
                merge_sha=MERGE,
                command="verify release",
                summary="passed",
                log_path="/tmp/release.log",
                log_sha256=LOG_DIGEST,
            )
        ],
    )
    old_digest = released.digest()
    rebound = released.rebind_candidate(
        base_sha=BASE,
        candidate_sha=NEW_HEAD,
        task_file_sha256="c" * 64,
        changed_paths=["prismatic/contract.py"],
    )
    assert rebound.state is PromotionState.CANDIDATE
    assert rebound.verification_evidence == ()
    assert rebound.independent_review is None
    assert rebound.ci_checks == ()
    assert rebound.merge_sha is None
    assert rebound.release_id is None
    assert rebound.release_evidence == ()
    assert rebound.invalidation_reason == (
        "bindings_changed:candidate_sha,changed_paths,task_file_sha256"
    )
    assert rebound.digest() != old_digest
    with pytest.raises(ManifestValidationError, match="rebind requires"):
        rebound.rebind_candidate(
            base_sha=BASE,
            candidate_sha=NEW_HEAD,
            task_file_sha256="c" * 64,
            changed_paths=["prismatic/contract.py"],
        )


def test_stale_review_ci_and_merge_are_rejected():
    requested = make_manifest().request_review(proofs_for(RiskTier.A))
    stale_review = IndependentReview(
        reviewer="agent:reviewer",
        review_id="review-stale",
        verdict="CLEAN",
        reviewed_sha=NEW_HEAD,
        reviewed_manifest_digest=requested.digest(),
        scope_clean=True,
        conflict_free=True,
    )
    with pytest.raises(ManifestValidationError, match="stale candidate SHA"):
        requested.record_review(stale_review)

    wrong_digest = replace(clean_review(requested), reviewed_manifest_digest="d" * 64)
    with pytest.raises(ManifestValidationError, match="stale manifest digest"):
        requested.record_review(wrong_digest)

    clean = requested.record_review(clean_review(requested))
    with pytest.raises(ManifestValidationError, match="stale candidate SHA"):
        clean.record_ci(ci_checks(NEW_HEAD))
    with pytest.raises(ManifestValidationError, match="exactly match"):
        clean.record_ci(ci_checks()[:-1])

    eligible = clean.record_ci(ci_checks()).mark_merge_eligible()
    with pytest.raises(ManifestValidationError, match="stale candidate SHA"):
        eligible.mark_merged(candidate_sha=NEW_HEAD, merge_sha=MERGE)


def test_no_state_skips_or_premature_fields():
    candidate = make_manifest()
    with pytest.raises(ManifestValidationError, match="CI_GREEN"):
        candidate.mark_merge_eligible()
    with pytest.raises(ManifestValidationError, match="independent CLEAN review"):
        replace(
            candidate,
            state=PromotionState.CLEAN,
            verification_evidence=tuple(proofs_for(RiskTier.A)),
        )
    with pytest.raises(ManifestValidationError, match="CI evidence is premature"):
        replace(candidate, ci_checks=tuple(ci_checks()))
    with pytest.raises(ManifestValidationError, match="merge_sha is premature"):
        replace(candidate, merge_sha=MERGE)


def test_review_must_be_independent_and_affirmative():
    requested = make_manifest().request_review(proofs_for(RiskTier.A))
    with pytest.raises(ManifestValidationError, match="must differ from producer"):
        requested.record_review(
            replace(clean_review(requested), reviewer="AGENT:PRODUCER")
        )
    with pytest.raises(ManifestValidationError, match="verdict must be CLEAN"):
        replace(clean_review(requested), verdict="REPAIR")
    with pytest.raises(ManifestValidationError, match="scope_clean must be true"):
        replace(clean_review(requested), scope_clean=False)


def test_direct_construction_rejects_subclasses_bool_and_bad_paths():
    class BadString(str):
        pass

    with pytest.raises(ManifestValidationError, match="exact string"):
        make_manifest().create(
            issue_id=BadString("GRO-5000"),
            task_id="task",
            task_file_sha256=TASK_DIGEST,
            repository="repo",
            target="main",
            base_sha=BASE,
            candidate_sha=HEAD,
            changed_paths=["a.py"],
            producer="producer",
            preserved_candidate_location="/archive/a",
            risk_tier="A",
            dashboard_change=False,
            required_ci_checks=["test"],
        )
    data = make_manifest().to_dict()
    data["schema_version"] = True
    with pytest.raises(ManifestValidationError, match="exact integer"):
        MergeCandidateManifest.from_dict(data)
    for path in ("../secret", "/etc/passwd", "a\\b.py", "a/../b.py"):
        with pytest.raises(ManifestValidationError, match="repository-relative|POSIX"):
            make_manifest().create(
                issue_id="GRO-5000",
                task_id="task",
                task_file_sha256=TASK_DIGEST,
                repository="repo",
                target="main",
                base_sha=BASE,
                candidate_sha=HEAD,
                changed_paths=[path],
                producer="producer",
                preserved_candidate_location="/archive/a",
                risk_tier="A",
                dashboard_change=False,
                required_ci_checks=["test"],
            )
    with pytest.raises(TypeError, match="final"):

        class BadManifest(MergeCandidateManifest):
            pass


def test_duplicate_paths_proofs_checks_and_release_ids_reject():
    with pytest.raises(ManifestValidationError, match="duplicates"):
        make_manifest().create(
            issue_id="GRO-5000",
            task_id="task",
            task_file_sha256=TASK_DIGEST,
            repository="repo",
            target="main",
            base_sha=BASE,
            candidate_sha=HEAD,
            changed_paths=["a.py", "a.py"],
            producer="producer",
            preserved_candidate_location="/archive/a",
            risk_tier="A",
            dashboard_change=False,
            required_ci_checks=["test"],
        )
    with pytest.raises(ManifestValidationError, match="duplicate proof"):
        make_manifest().request_review([proof("focused"), proof("focused")])
    requested = make_manifest().request_review(proofs_for(RiskTier.A))
    clean = requested.record_review(clean_review(requested))
    with pytest.raises(ManifestValidationError, match="duplicate names"):
        clean.record_ci([ci_checks()[0], ci_checks()[0]])


def test_untrusted_subclass_hooks_are_rejected_before_sorting():
    class HookString(str):
        def __lt__(self, other):
            raise AssertionError("untrusted string comparison hook executed")

    with pytest.raises(ManifestValidationError, match="exact string"):
        MergeCandidateManifest.create(
            issue_id="GRO-5000",
            task_id="task",
            task_file_sha256=TASK_DIGEST,
            repository="repo",
            target="main",
            base_sha=BASE,
            candidate_sha=HEAD,
            changed_paths=[HookString("z.py"), "a.py"],
            producer="producer",
            preserved_candidate_location="/archive/a",
            risk_tier="A",
            dashboard_change=False,
            required_ci_checks=["test"],
        )

    class HookEvidence:
        @property
        def proof_class(self):
            raise AssertionError("untrusted evidence property executed")

    with pytest.raises(ManifestValidationError, match="invalid value"):
        make_manifest().request_review([HookEvidence()])  # type: ignore[list-item]


def test_strict_deserialization_unknown_nested_duplicate_json_and_nonfinite():
    manifest = make_manifest()
    data = manifest.to_dict()
    data["unexpected"] = True
    with pytest.raises(ManifestValidationError, match="unknown fields"):
        MergeCandidateManifest.from_dict(data)

    requested = manifest.request_review(proofs_for(RiskTier.A))
    nested = requested.to_dict()
    nested["verification_evidence"][0]["unexpected"] = True
    with pytest.raises(ManifestValidationError, match="unknown fields"):
        MergeCandidateManifest.from_dict(nested)

    with pytest.raises(ManifestValidationError, match="keys must be exact strings"):
        MergeCandidateManifest.from_dict({1: "not-a-json-object-key"})

    with pytest.raises(ManifestValidationError, match="duplicate JSON key"):
        MergeCandidateManifest.from_json('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ManifestValidationError, match="non-finite"):
        MergeCandidateManifest.from_json('{"schema_version":NaN}')


def test_canonical_json_digest_round_trip_and_atomic_file(tmp_path: Path):
    manifest = merge_eligible()
    raw = manifest.canonical_json()
    reversed_json = json.dumps(
        dict(reversed(list(manifest.to_dict().items()))),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    restored = MergeCandidateManifest.from_json(reversed_json)
    assert restored == manifest
    assert restored.canonical_json() == raw
    assert restored.digest() == manifest.digest()

    path = tmp_path / "merge_candidate.json"
    manifest.write(str(path))
    assert MergeCandidateManifest.read(str(path)) == manifest
    assert not list(tmp_path.glob(".merge_candidate.*.tmp"))
    with pytest.raises(ManifestValidationError, match="filename"):
        manifest.write(str(tmp_path / "other.json"))


def test_json_state_and_enum_values_require_exact_strings():
    class BadString(str):
        pass

    data = make_manifest().to_dict()
    data["state"] = BadString("CANDIDATE")
    with pytest.raises(ManifestValidationError, match="exact string"):
        MergeCandidateManifest.from_dict(data)
    data = make_manifest().to_dict()
    data["risk_tier"] = BadString("A")
    with pytest.raises(ManifestValidationError, match="exact string"):
        MergeCandidateManifest.from_dict(data)
