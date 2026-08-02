"""RF-2 Integration Tests: Verification Worker.

These tests exercise real PE integration by creating
``MergeCandidateManifest`` objects and calling
``manifest.request_review(VerificationEvidence(...))``.
"""

import json

import pytest

from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    PromotionState,
    RiskTier,
)
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.verifier import VerificationWorker

# ── Helpers ──────────────────────────────────────────────────────────


def _create_tier_a_manifest() -> MergeCandidateManifest:
    """Create a RiskTier A manifest (requires: focused)."""
    return MergeCandidateManifest.create(
        issue_id="GRO-TEST-VERIFY",
        task_id="GRO-TEST-VERIFY",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="a" * 40,
        candidate_sha="b" * 40,
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location="/tmp/test-verify",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )


def _create_tier_b_manifest() -> MergeCandidateManifest:
    """Create a RiskTier B manifest (requires: focused, canonical, package)."""
    return MergeCandidateManifest.create(
        issue_id="GRO-TEST-VERIFY-B",
        task_id="GRO-TEST-VERIFY-B",
        task_file_sha256="b" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha="c" * 40,
        candidate_sha="d" * 40,
        changed_paths=["prismatic/core/router.py"],
        producer="agy",
        preserved_candidate_location="/tmp/test-verify-b",
        risk_tier=RiskTier.B,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )


def _create_review_job(queue: ReviewQueue, tier: int = 0) -> str:
    """Create and return a queued review job ID."""
    paths = {
        0: ["docs/readme.md"],
        1: ["prismatic/core/router.py"],
    }
    return queue.enqueue_completed_work(
        completed_work_id=f"agy-cw-verify-{tier}",
        task_id=f"GRO-VERIFY-{tier}",
        repository="mbgulden/prismatic-engine",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        changed_paths=paths.get(tier, ["docs/readme.md"]),
    )


@pytest.fixture
def queue(tmp_path):
    db = ReviewFactoryDB(db_path=tmp_path / "test_verify.db")
    db.ensure_tables()
    q = ReviewQueue(db=db)
    yield q
    q.close()


@pytest.fixture
def worker(tmp_path):
    return VerificationWorker(
        repo_path=tmp_path,
        log_dir=tmp_path / "logs",
        test_mode=True,
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestVerificationWithManifest:
    """Verify that the worker produces VerificationEvidence that
    satisfies the manifest's risk tier and advances it to REVIEW_REQUIRED.
    """

    def test_tier_a_produces_focused_evidence(self, queue, worker):
        """RiskTier A requires only 'focused' proof class."""
        manifest = _create_tier_a_manifest()
        assert manifest.state == PromotionState.CANDIDATE

        job_id = _create_review_job(queue, tier=0)
        job = queue.lease_for_verification("verifier-1")
        assert job is not None

        receipt, updated = worker._verify_materialized(job, manifest, "f" * 64)

        # Manifest advanced to REVIEW_REQUIRED
        assert updated.state == PromotionState.REVIEW_REQUIRED

        # Evidence contains required proof classes
        evidence_classes = {e.proof_class for e in updated.verification_evidence}
        assert "focused" in evidence_classes

        # Receipt is valid
        assert receipt.review_job_id == job_id
        assert receipt.candidate_commit == job.candidate_commit
        assert receipt.changed_path_invariance_proof != ""

    def test_tier_b_produces_three_evidence_classes(self, queue, worker):
        """RiskTier B requires focused + canonical + package."""
        manifest = _create_tier_b_manifest()
        assert manifest.state == PromotionState.CANDIDATE

        _ = _create_review_job(queue, tier=1)
        job = queue.lease_for_verification("verifier-1")
        assert job is not None

        receipt, updated = worker._verify_materialized(job, manifest, "f" * 64)

        # Manifest advanced
        assert updated.state == PromotionState.REVIEW_REQUIRED

        # All 3 proof classes present
        evidence_classes = {e.proof_class for e in updated.verification_evidence}
        assert evidence_classes == {"focused", "canonical", "package"}

    def test_evidence_has_correct_structure(self, queue, worker):
        """Each VerificationEvidence has all required fields."""
        manifest = _create_tier_a_manifest()
        _ = _create_review_job(queue, tier=0)
        job = queue.lease_for_verification("verifier-1")

        receipt, updated = worker._verify_materialized(job, manifest, "f" * 64)

        for evidence in updated.verification_evidence:
            assert evidence.proof_class in (
                "focused",
                "canonical",
                "package",
                "failure",
                "recovery",
                "rollback",
                "browser",
                "real_data",
            )
            assert evidence.command != ""
            assert evidence.summary != ""
            assert evidence.result == "PASS"
            assert evidence.log_path != ""
            assert len(evidence.log_sha256) == 64  # SHA-256 hex

    def test_receipt_captures_non_claims(self, queue, worker):
        """Receipt honestly records what was NOT tested."""
        manifest = _create_tier_a_manifest()
        _ = _create_review_job(queue, tier=0)
        job = queue.lease_for_verification("verifier-1")

        receipt, _ = worker._verify_materialized(job, manifest, "f" * 64)

        non_claims = json.loads(receipt.explicit_non_claims)
        assert isinstance(non_claims, list)
        assert len(non_claims) > 0
        # Must mention what wasn't tested
        assert any("not" in c.lower() for c in non_claims)

    def test_invariance_proof_is_deterministic(self, queue, worker):
        """Same changed paths → same invariance proof."""
        manifest = _create_tier_a_manifest()
        _ = _create_review_job(queue, tier=0)
        job = queue.lease_for_verification("verifier-1")

        receipt1, _ = worker._verify_materialized(job, manifest, "f" * 64)

        # Create a second run with the same paths
        proof1 = receipt1.changed_path_invariance_proof
        assert len(proof1) == 64  # SHA-256

        # Recompute directly
        import hashlib

        canonical = json.dumps(sorted(["docs/readme.md"]), sort_keys=True)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert proof1 == expected


class TestIntegrityInvariants:
    """Dedicated tests for the 3 integrity invariants in RF-2 verifier."""

    def test_missing_integration_import_flagged(self, tmp_path):
        """Invariant 1: A module missing its required PE import fails check."""
        worker = VerificationWorker(repo_path=tmp_path)
        dummy_file = tmp_path / "prismatic/review_factory/verifier.py"
        dummy_file.parent.mkdir(parents=True, exist_ok=True)
        # Write stub content without PE manifest import
        dummy_file.write_text("def hello(): return 42", encoding="utf-8")

        res = worker._check_integration_imports(
            "prismatic/review_factory/verifier.py",
            ["from prismatic.merge_candidate_manifest import"],
        )
        assert not res.passed
        assert "missing expected import" in res.stderr or "not found" in res.stderr

    def test_valid_integration_import_passes(self, tmp_path):
        """Invariant 1: A module containing its required PE import passes."""
        worker = VerificationWorker(repo_path=tmp_path)
        dummy_file = tmp_path / "prismatic/review_factory/verifier.py"
        dummy_file.parent.mkdir(parents=True, exist_ok=True)
        dummy_file.write_text(
            "from prismatic.merge_candidate_manifest import MergeCandidateManifest",
            encoding="utf-8",
        )

        res = worker._check_integration_imports(
            "prismatic/review_factory/verifier.py",
            ["from prismatic.merge_candidate_manifest import"],
        )
        assert res.passed

    def test_circular_proof_detected(self, tmp_path):
        """Invariant 2: A test importing only review_factory fails circular proof check."""
        worker = VerificationWorker(repo_path=tmp_path)
        test_file = tmp_path / "prismatic/review_factory/tests/test_verifier.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "from prismatic.review_factory.verifier import VerificationWorker\ndef test_stub(): assert True",
            encoding="utf-8",
        )

        res = worker._check_circular_proof(
            "prismatic/review_factory/tests/test_verifier.py"
        )
        assert not res.passed
        assert "circular proof" in res.stderr.lower()

    def test_non_circular_proof_passes(self, tmp_path):
        """Invariant 2: A test importing external PE surface passes circular proof check."""
        worker = VerificationWorker(repo_path=tmp_path)
        test_file = tmp_path / "prismatic/review_factory/tests/test_verifier.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "from prismatic.merge_candidate_manifest import MergeCandidateManifest\nfrom prismatic.review_factory.verifier import VerificationWorker",
            encoding="utf-8",
        )

        res = worker._check_circular_proof(
            "prismatic/review_factory/tests/test_verifier.py"
        )
        assert res.passed

    def test_missing_callsite_flagged(self, tmp_path):
        """Invariant 3: Module importing PE surface but not calling the target function fails."""
        worker = VerificationWorker(repo_path=tmp_path)
        dummy_file = tmp_path / "prismatic/review_factory/verifier.py"
        dummy_file.parent.mkdir(parents=True, exist_ok=True)
        dummy_file.write_text(
            "from prismatic.merge_candidate_manifest import MergeCandidateManifest",
            encoding="utf-8",
        )

        res = worker._check_callsites(
            "prismatic/review_factory/verifier.py",
            ["request_review"],
        )
        assert not res.passed
        assert "Integration contract violation" in res.stderr

    def test_valid_callsite_passes(self, tmp_path):
        """Invariant 3: Module importing and calling PE surface function passes."""
        worker = VerificationWorker(repo_path=tmp_path)
        dummy_file = tmp_path / "prismatic/review_factory/verifier.py"
        dummy_file.parent.mkdir(parents=True, exist_ok=True)
        dummy_file.write_text(
            "from prismatic.merge_candidate_manifest import MergeCandidateManifest\nmanifest.request_review(evidence)",
            encoding="utf-8",
        )

        res = worker._check_callsites(
            "prismatic/review_factory/verifier.py",
            ["request_review"],
        )
        assert res.passed
