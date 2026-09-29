"""RF-4 Integration Tests: Merge Executor.

Tests exercise real PE integration by driving a
``MergeCandidateManifest`` through the full promotion lifecycle:
    CANDIDATE → REVIEW_REQUIRED → CLEAN → CI_GREEN → MERGE_ELIGIBLE

Uses dry_run=True since we can't run ``integrate_pipeline_run()``
in a test without a real git repository.
"""

import subprocess
from pathlib import Path

import pytest

from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    PromotionState,
    RiskTier,
    VerificationEvidence,
)
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.merge_executor import MergeExecutor
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewVerdict,
    VerificationReceipt,
)
from prismatic.review_factory.policy import PolicyEngine
from prismatic.review_factory.queue import ReviewQueue


# ── Helpers ──────────────────────────────────────────────────────────


def _make_git_repo(tmp_path):
    """Build a real git repo: base commit B, candidate commit C (child of B).

    Pre-creates the no-ff merge commit M of C into main, then resets main back
    to B. The fixture can then hand the executor a real merge SHA whose tree
    equals the candidate tree — exactly what the strict result-tree check
    requires — while the target head still sits at the authorized base.
    """
    repo = tmp_path / "merge-repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-b", "main")
    git("config", "user.email", "rf-test@example.com")
    git("config", "user.name", "RF Test")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("# fixture\n")
    git("add", ".")
    git("commit", "-m", "base")
    base_commit = git("rev-parse", "HEAD").stdout.strip()

    git("checkout", "-b", "candidate")
    (repo / "docs" / "readme.md").write_text("# fixture\n\ncandidate change\n")
    git("add", ".")
    git("commit", "-m", "candidate")
    candidate_commit = git("rev-parse", "HEAD").stdout.strip()
    candidate_tree = git("rev-parse", f"{candidate_commit}^{{tree}}").stdout.strip()

    git("checkout", "main")
    git("merge", "--no-ff", "candidate", "-m", "merge candidate")
    merge_sha = git("rev-parse", "HEAD").stdout.strip()
    # Restore main to the base so the executor sees the pre-merge target head.
    git("reset", "--hard", base_commit)

    return {
        "repo": repo,
        "base_commit": base_commit,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "merge_sha": merge_sha,
    }


def _v1_policy_queue(db):
    """ReviewQueue whose policy stamps policy_version "v1".

    The strict executor binding requires f"v{manifest.proof_policy_version}"
    ("v1", pinned by PROOF_POLICY_VERSION) to equal job.policy_version, so the
    fixture must enqueue under the spec v1 policy rather than the
    "v1-builtin" default policy.
    """
    policy_path = Path(__file__).resolve().parents[1] / "spec" / "policy_file_v1.yaml"
    return ReviewQueue(db=db, policy=PolicyEngine.from_yaml(policy_path))


def _create_merge_ready_manifest(
    base_sha="a" * 40, candidate_sha="b" * 40
) -> MergeCandidateManifest:
    """Create a manifest promoted through CANDIDATE → CLEAN."""
    manifest = MergeCandidateManifest.create(
        issue_id="GRO-TEST-MERGE",
        task_id="GRO-TEST-MERGE",
        task_file_sha256="a" * 64,
        repository="mbgulden/prismatic-engine",
        target="main",
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        changed_paths=["docs/readme.md"],
        producer="agy",
        preserved_candidate_location="/tmp/test-merge",
        risk_tier=RiskTier.A,
        dashboard_change=False,
        required_ci_checks=["rf-v1-verification"],
    )

    # CANDIDATE → REVIEW_REQUIRED
    evidence = [
        VerificationEvidence(
            proof_class="focused",
            command="pytest tests/ -x -q",
            summary="5 tests passed",
            result="PASS",
            log_path="/tmp/focused.log",
            log_sha256="f" * 64,
        )
    ]
    manifest = manifest.request_review(evidence)

    # REVIEW_REQUIRED → CLEAN
    review = IndependentReview(
        reviewer="antigravity-rf-v1",
        review_id="review-test-1",
        verdict="CLEAN",
        reviewed_sha=candidate_sha,
        reviewed_manifest_digest=manifest._digest_without_review(),
        scope_clean=True,
        conflict_free=True,
    )
    manifest = manifest.record_review(review)

    return manifest


def _create_merge_ready_job(
    queue: ReviewQueue,
    tier: int = 0,
    base_commit: str | None = None,
    candidate_commit: str | None = None,
    candidate_tree: str | None = None,
) -> str:
    """Create a job and push it through to merge_ready."""
    paths = {
        0: ["docs/readme.md"],
        1: ["prismatic/core/router.py"],
        2: ["prismatic/auth/oauth.py"],
    }

    base_commit = base_commit or "a" * 40
    candidate_commit = candidate_commit or "b" * 40
    candidate_tree = candidate_tree or candidate_commit

    job_id = queue.enqueue_completed_work(
        completed_work_id=f"agy-cw-merge-{tier}-{id(queue)}",
        task_id="GRO-TEST-MERGE",
        repository="mbgulden/prismatic-engine",
        base_commit=base_commit,
        candidate_commit=candidate_commit,
        candidate_tree=candidate_tree,
        changed_paths=paths.get(tier, ["docs/readme.md"]),
    )

    # Verify
    _ = queue.lease_for_verification("verifier-1")
    receipt = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit=candidate_commit,
        candidate_tree=candidate_tree,
    )
    queue.complete_verification(job_id, receipt, worker_id="verifier-1")

    # Review (multi-witness for high tiers)
    job_obj = queue.db.get_review_job(job_id)
    needed = job_obj.required_witnesses if job_obj.required_witnesses > 0 else 1
    for witness_n in range(needed):
        leased = queue.lease_for_review(f"reviewer-{witness_n}")
        if leased is not None:
            decision = ReviewDecision(
                review_job_id=job_id,
                reviewer_id=f"reviewer-{witness_n}",
                candidate_commit=candidate_commit,
                candidate_tree=candidate_tree,
                receipt_id=receipt.receipt_id,
                verdict=ReviewVerdict.CLEAN.value,
            )
            queue.submit_verdict(job_id, decision, reviewer_id=f"reviewer-{witness_n}")

    return job_id


@pytest.fixture
def queue(tmp_path):
    db = ReviewFactoryDB(db_path=tmp_path / "test_merge.db")
    db.ensure_tables()
    q = ReviewQueue(db=db)
    yield q
    q.close()


@pytest.fixture
def executor(queue):
    return MergeExecutor(queue=queue, dry_run=True)


# ── Manifest promotion tests ────────────────────────────────────────


class TestManifestPromotion:
    """Verify we can drive a manifest through the full lifecycle."""

    def test_full_manifest_promotion(self):
        """CANDIDATE → REVIEW_REQUIRED → CLEAN → CI_GREEN → MERGE_ELIGIBLE."""
        manifest = _create_merge_ready_manifest()
        assert manifest.state == PromotionState.CLEAN

        # CLEAN → CI_GREEN
        ci_checks = [
            CICheck(
                name="rf-v1-verification",
                run_id=1000,
                conclusion="SUCCESS",
                head_sha="b" * 40,
                details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
            )
        ]
        manifest = manifest.record_ci(ci_checks)
        assert manifest.state == PromotionState.CI_GREEN

        # CI_GREEN → MERGE_ELIGIBLE
        manifest = manifest.mark_merge_eligible()
        assert manifest.state == PromotionState.MERGE_ELIGIBLE

        # Get factory bindings
        bindings = manifest.factory_bindings()
        assert "base_sha" in bindings
        assert "candidate_sha" in bindings
        assert "manifest_digest" in bindings
        assert "evidence_digest" in bindings

    def test_merge_eligible_requires_ci_green(self):
        """Cannot mark_merge_eligible from CLEAN (must be CI_GREEN)."""
        manifest = _create_merge_ready_manifest()
        assert manifest.state == PromotionState.CLEAN

        from prismatic.merge_candidate_manifest import ManifestValidationError

        with pytest.raises(ManifestValidationError):
            manifest.mark_merge_eligible()


# ── Dry-run merge tests ─────────────────────────────────────────────


class TestMergeExecution:
    """Test merge execution flow (dry-run)."""

    def test_dry_run_merge_succeeds(self, queue, executor):
        """Dry-run merge for a Tier 0 job produces a MergeResult."""
        job_id = _create_merge_ready_job(queue, tier=0)

        # Authorize explicitly
        auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-0")
        assert auth_id is not None

        # Build a CLEAN manifest for the executor
        manifest = _create_merge_ready_manifest()
        # Advance through CI
        ci_checks = [
            CICheck(
                name="rf-v1-verification",
                run_id=1000,
                conclusion="SUCCESS",
                head_sha="b" * 40,
                details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
            )
        ]
        manifest = manifest.record_ci(ci_checks)
        manifest = manifest.mark_merge_eligible()

        result = executor.execute(job_id, manifest=manifest)
        assert result.success
        assert result.merge_sha == "dry-run-sha"

    def test_no_auth_fails(self, queue, executor):
        """Merge without authorization fails."""
        job_id = _create_merge_ready_job(queue, tier=0)
        # Don't authorize

        manifest = _create_merge_ready_manifest()
        ci_checks = [
            CICheck(
                name="rf-v1-verification",
                run_id=1000,
                conclusion="SUCCESS",
                head_sha="b" * 40,
                details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
            )
        ]
        manifest = manifest.record_ci(ci_checks)
        manifest = manifest.mark_merge_eligible()

        result = executor.execute(job_id, manifest=manifest)
        assert not result.success
        assert "authorization" in result.error.lower()

    def test_non_dry_run_merge_executes_attestation_and_lock(self, tmp_path):
        """Verify non-dry-run merge calls submit_attestation, acquire_lock, and release_lock."""
        from unittest.mock import patch, MagicMock
        from prismatic.core.merge_factory import MergeFactoryStore

        # Strict production requires real git state: a repo whose main sits at
        # the authorized base, plus a job enqueued under the spec v1 policy so
        # the manifest's proof_policy_version ("v1") binds to the job.
        git_ids = _make_git_repo(tmp_path)
        db = ReviewFactoryDB(db_path=tmp_path / "test_merge_real.db")
        db.ensure_tables()
        queue = _v1_policy_queue(db)
        try:
            mf_store = MergeFactoryStore(db_path=tmp_path / "test_mf.db")
            executor = MergeExecutor(
                queue=queue,
                dry_run=False,
                mf_store=mf_store,
                repo_path=git_ids["repo"],
                merge_receipts_path=tmp_path / "merge-receipts.jsonl",
            )

            job_id = _create_merge_ready_job(
                queue,
                tier=0,
                base_commit=git_ids["base_commit"],
                candidate_commit=git_ids["candidate_commit"],
                candidate_tree=git_ids["candidate_tree"],
            )
            auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-0")
            assert auth_id is not None

            manifest = _create_merge_ready_manifest(
                base_sha=git_ids["base_commit"],
                candidate_sha=git_ids["candidate_commit"],
            )
            ci_checks = [
                CICheck(
                    name="rf-v1-verification",
                    run_id=1000,
                    conclusion="SUCCESS",
                    head_sha=git_ids["candidate_commit"],
                    details_url="https://github.com/mbgulden/prismatic-engine/actions/runs/1000",
                    provider_receipt_id="receipt-123",
                    provider_receipt_sha256="a" * 64,
                    provider_policy_sha256="b" * 64,
                )
            ]
            manifest = manifest.record_ci(ci_checks)
            manifest = manifest.mark_merge_eligible()

            mock_integration_manifest = MagicMock()
            mock_integration_manifest.merge_sha = git_ids["merge_sha"]

            # Mock the verification receipt store to return a valid receipt.
            # The strict contract only admits provider-neutral receipts whose
            # digest matches the CI check's declared receipt digest.
            mock_receipt = MagicMock()
            mock_receipt.receipt_id = "receipt-123"
            mock_receipt.receipt_sha256 = "a" * 64
            mock_receipt.policy_sha256 = "b" * 64
            mock_receipt.task_id = "test-task"
            mock_receipt.candidate_commit = git_ids["candidate_commit"]
            mock_receipt.candidate_tree = git_ids["candidate_tree"]
            mock_receipt.base_commit = git_ids["base_commit"]
            mock_receipt.base_tree = git_ids["base_commit"]
            mock_store = MagicMock()
            mock_store.get.return_value = mock_receipt
            executor.verification_receipt_store = mock_store

            with patch(
                "prismatic.review_factory.merge_executor.integrate_pipeline_run",
                return_value=mock_integration_manifest,
            ):
                result = executor.execute(job_id, manifest=manifest)

            assert result.success, result.error
            assert result.merge_sha == git_ids["merge_sha"]

            # Verify attestation was written to mf_store
            decision_hist = mf_store.get_decision_history("GRO-TEST-MERGE")
            assert len(decision_hist) == 1
            assert decision_hist[0]["decision"] == "APPROVE_MERGE"

            # Verify the signed merge receipt was emitted at merge time.
            assert result.merge_receipt_id
            from prismatic.verification.merge_receipt import (
                MERGE_RECEIPT_MARKER,
                find_merge_receipts,
            )

            receipts = find_merge_receipts(
                merge_sha=git_ids["merge_sha"],
                log_path=tmp_path / "merge-receipts.jsonl",
            )
            assert len(receipts) == 1
            receipt = receipts[0]
            assert receipt["receipt_id"] == result.merge_receipt_id
            assert receipt["marker"] == MERGE_RECEIPT_MARKER
            assert receipt["candidate_sha"] == git_ids["candidate_commit"]
            assert receipt["merge_sha"] == git_ids["merge_sha"]
            assert receipt["verifier_id"] == "rf-merge-executor"
            assert receipt["verified_receipt_refs"] == [
                {"receipt_id": "receipt-123", "receipt_sha256": "a" * 64}
            ]
            assert receipt["explicit_non_claims"]
        finally:
            queue.close()


class TestMergeReceiptEmission:
    """Merge-time receipt emission is best-effort: it must never change the
    merge result (same contract as the trust-ledger recording)."""

    def _executor(self, tmp_path, **overrides):
        from unittest.mock import MagicMock

        job = MagicMock()
        job.repository = "mbgulden/prismatic-engine"
        job.candidate_commit = "a" * 40
        job.candidate_tree = "b" * 40
        job.base_commit = "c" * 40
        job.review_job_id = "job-1"
        job.task_id = "task-1"
        job.policy_version = "v1"
        job.change_class = "docs"
        auth = MagicMock()
        auth.actor = "merge-authority"
        auth.authorization_id = "auth-1"
        manifest = MagicMock()
        manifest.digest.return_value = "m" * 64
        executor = MergeExecutor(
            dry_run=True, merge_receipts_path=tmp_path / "receipts.jsonl"
        )
        return executor, job, auth, manifest

    def test_emission_writes_receipt_with_bindings(self, tmp_path):
        executor, job, auth, manifest = self._executor(tmp_path)
        receipt_id = executor._emit_merge_receipt(job, auth, manifest, "d" * 40, ())
        assert receipt_id
        from prismatic.verification.merge_receipt import find_merge_receipts

        receipts = find_merge_receipts(log_path=tmp_path / "receipts.jsonl")
        assert len(receipts) == 1
        assert receipts[0]["receipt_id"] == receipt_id
        assert receipts[0]["merge_sha"] == "d" * 40

    def test_emission_failure_returns_empty_id_never_raises(self, tmp_path):
        executor, job, auth, manifest = self._executor(tmp_path)
        # A directory as the log path cannot be appended to.
        executor.merge_receipts_path = tmp_path
        assert executor._emit_merge_receipt(job, auth, manifest, "d" * 40, ()) == ""
