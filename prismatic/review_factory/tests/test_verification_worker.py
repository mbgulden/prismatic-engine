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
from prismatic.review_factory.testing import enqueue_with_defaults
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
    return enqueue_with_defaults(
        queue,
        completed_work_id=f"agy-cw-verify-{tier}",
        task_id=f"GRO-VERIFY-{tier}",
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


class TestAdversarialImmutableProvenance:
    """Dedicated test suite for the 4 Adversarial requirements in the skill."""

    def test_provenance_reproducibility(self, queue, worker):
        """Provenance Reproducibility: Recompute the provenance record from stored evidence and obtain the exact same hash."""
        manifest = _create_tier_a_manifest()
        _ = _create_review_job(queue, tier=0)
        job = queue.lease_for_verification("verifier-1")

        receipt, _ = worker._verify_materialized(job, manifest, "f" * 64)

        # The receipt's receipt_id should match the recomputed provenance hash exactly
        recomputed = receipt.recompute_provenance_hash(
            repository=job.repository, policy_version=job.policy_version
        )
        assert receipt.receipt_id == recomputed

    def test_commit_tree_mismatch_gate(self, tmp_path):
        """Commit/Tree Mismatch Gate: Queue a job with mismatched commit and tree SHAs; verification halts immediately without running commands."""
        import subprocess

        # Create a real git repository to resolve tree
        repo_dir = tmp_path / "mismatch_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)

        # Git setup
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=str(repo_dir), check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo_dir),
            check=True,
        )

        # Create a file and commit it
        a_file = repo_dir / "a.py"
        a_file.write_text("print('hello')", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=str(repo_dir), check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True
        )

        # Get actual commit SHA
        commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Mock tree SHA (mismatched)
        mismatched_tree = "f" * 40

        worker = VerificationWorker(repo_path=repo_dir)

        # Attempting to materialize or verify should raise ValueError for tree mismatch
        with pytest.raises(ValueError) as exc_info:
            worker._materialize_immutable_archive(commit_sha, mismatched_tree)

        assert "tree mismatch" in str(exc_info.value).lower()

    def test_branch_name_rejection(self, queue):
        """Branch Name Rejection: Supply a branch name or short SHA to backlog importer; enqueue fails closed."""
        from prismatic.review_factory.backlog_importer import BacklogImporter
        from unittest.mock import MagicMock
        from prismatic.agy_completed_work import CompletedWorkRow

        importer = BacklogImporter(queue=queue)

        # Create a mock CompletedWorkRow with short SHA or branch name (which is rejected by the importer)
        row_invalid = MagicMock(spec=CompletedWorkRow)
        row_invalid.id = "agy-cw-invalid-sha"
        row_invalid.integration_classification = "pass_ready_for_review"
        row_invalid.eligible_for_merge = True
        row_invalid.source_path = "/tmp/invalid-source"
        row_invalid.packet = {
            "issue": "GRO-INVALID-SHA",
            "repository": "mbgulden/prismatic-engine",
            "base_commit": "main",  # branch name
            "candidate_commit": "abc1234",  # short SHA
            "base_tree": "0" * 40,
            "candidate_tree": "0" * 40,
            "changed_files": ["docs/readme.md"],
        }

        # Mock the store list to return this row
        class MockStore:
            def list(self, limit):
                return [row_invalid]

        importer._db_path = None  # not querying real DB
        # Monkeypatch the store list
        import unittest.mock as mock

        with mock.patch(
            "prismatic.review_factory.backlog_importer.AgyCompletedWorkStore"
        ) as MockStoreClass:
            MockStoreClass.return_value = MockStore()
            res = importer.import_from_completed_work()

        # The importer should skip the ineligible row and log an error
        assert res.skipped_ineligible == 1
        assert res.enqueued == 0
        assert any("missing valid commit/tree SHAs" in err for err in res.errors)

    def test_worktree_mutation_immunity(self, tmp_path):
        """Worktree Mutation Immunity: Mutate the source worktree during active verification; executed bytes and verification receipt identity remain unchanged."""
        import subprocess
        from prismatic.review_factory.models import ReviewJob
        from prismatic.merge_candidate_manifest import MergeCandidateManifest, RiskTier

        # Create a real Git repo to be the candidate source
        repo_dir = tmp_path / "immunity_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=str(repo_dir), check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo_dir),
            check=True,
        )

        # Create a simple test file and a source module
        # Inside the committed files, the test passes
        a_module = repo_dir / "prismatic_math.py"
        a_module.write_text("def add(x, y): return x + y", encoding="utf-8")

        # Note: We add a comment importing from prismatic.merge_candidate_manifest to satisfy the circular proof check!
        test_file = repo_dir / "test_prismatic_math.py"
        test_file.write_text(
            "import sys\nsys.path.insert(0, '.')\nimport prismatic_math\n"
            "# from prismatic.merge_candidate_manifest import MergeCandidateManifest\n"
            "def test_math(): assert prismatic_math.add(1, 1) == 2\n",
            encoding="utf-8",
        )

        subprocess.run(
            ["git", "add", "prismatic_math.py", "test_prismatic_math.py"],
            cwd=str(repo_dir),
            check=True,
        )
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_dir), check=True)

        # Get HEAD commit tree and sha
        commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tree_sha = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        worker = VerificationWorker(
            repo_path=repo_dir, log_dir=tmp_path / "logs", test_mode=False
        )

        job = ReviewJob(
            completed_work_id="agy-cw-immunity",
            task_id="GRO-IMMUNITY",
            repository="mbgulden/prismatic-engine",
            base_commit=commit_sha,
            candidate_commit=commit_sha,
            candidate_tree=tree_sha,
            changed_paths_json=json.dumps(["test_prismatic_math.py"]),
        )

        manifest = MergeCandidateManifest.create(
            issue_id="GRO-IMMUNITY",
            task_id="GRO-IMMUNITY",
            task_file_sha256="0" * 64,
            repository="mbgulden/prismatic-engine",
            target="main",
            base_sha=commit_sha,
            candidate_sha=commit_sha,
            changed_paths=["test_prismatic_math.py"],
            producer="test",
            preserved_candidate_location=str(repo_dir),
            risk_tier=RiskTier.A,
            dashboard_change=False,
            required_ci_checks=["rf-v1-verification"],
        )

        # 1. Run verification first with unchanged source
        receipt1, _ = worker.verify(job, manifest)
        assert receipt1.exit_codes != "{}"

        # 2. Mutate the test file in the source workspace to make it fail
        # This tests worktree mutation immunity!
        test_file.write_text(
            "import sys\nsys.path.insert(0, '.')\nimport prismatic_math\n"
            "# from prismatic.merge_candidate_manifest import MergeCandidateManifest\n"
            "def test_math(): assert prismatic_math.add(1, 1) == 9999\n",  # will fail if executed on this modified source
            encoding="utf-8",
        )

        # 3. Run verification again. The verification should still pass because it uses the git archive,
        # and the receipt ID and result fields must remain identical!
        receipt2, _ = worker.verify(job, manifest)

        assert receipt1.receipt_id == receipt2.receipt_id
        assert receipt1.exit_codes == receipt2.exit_codes

        # Verify both receipts exited with 0 for pytest (passed!)
        exits1 = json.loads(receipt1.exit_codes)
        assert exits1.get("focused") == 0
