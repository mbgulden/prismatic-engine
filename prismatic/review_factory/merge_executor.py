"""RF-4: Merge Executor — Deep PE Integration.

Drives two parallel promotion tracks:
1. ``MergeCandidateManifest``: CI_GREEN → MERGE_ELIGIBLE → MERGED
2. ``PipelineStateMachine``: REVIEW → INTEGRATE → COMPLETED

Uses ``MergeFactoryStore`` for attestation and merge lock acquisition,
and ``integrate_pipeline_run()`` for the actual git merge.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from prismatic.core.merge_factory import MergeFactoryStore, Principal
from prismatic.integrate import (
    IntegrationManifest,
    integrate_pipeline_run,
)
from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
    PromotionState,
)
from prismatic.review_factory.models import (
    MergeAuthorization,
    ReviewJob,
    ReviewJobState,
)
from prismatic.review_factory.queue import ReviewQueue

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Outcome of a merge execution."""

    job_id: str
    success: bool
    merge_sha: str = ""
    error: str = ""
    integration_manifest: IntegrationManifest | None = None
    final_manifest_state: str = ""


class MergeExecutor:
    """RF-4: Execute merges through the PE promotion pipeline."""

    def __init__(
        self,
        queue: ReviewQueue | None = None,
        dry_run: bool = False,
        repo_path: Path | None = None,
        mf_store: MergeFactoryStore | None = None,
    ):
        self.queue = queue or ReviewQueue()
        self.dry_run = dry_run
        self.repo_path = repo_path
        self.mf_store = mf_store or MergeFactoryStore()

    def execute(
        self,
        job_id: str,
        manifest: MergeCandidateManifest | None = None,
    ) -> MergeResult:
        """Execute the merge for an authorized job."""
        job = self.queue.db.get_review_job(job_id)
        if job is None:
            return MergeResult(job_id=job_id, success=False, error="Job not found")

        auth = self.queue.db.get_authorization_for_job(job_id)
        if auth is None:
            return MergeResult(
                job_id=job_id,
                success=False,
                error="No merge authorization found",
            )

        if auth.is_expired:
            return MergeResult(
                job_id=job_id,
                success=False,
                error="Authorization expired",
            )

        if auth.is_consumed:
            return MergeResult(
                job_id=job_id,
                success=False,
                error="Authorization already consumed",
            )

        # Validate exact authorization bindings against job
        if auth.repository and auth.repository != job.repository:
            return MergeResult(
                job_id=job_id,
                success=False,
                error=f"Authorization repository mismatch: expected {job.repository}, got {auth.repository}",
            )
        if auth.pr_head_commit and auth.pr_head_commit != job.candidate_commit:
            return MergeResult(
                job_id=job_id,
                success=False,
                error=f"Authorization head commit mismatch: expected {job.candidate_commit}, got {auth.pr_head_commit}",
            )
        if auth.pr_base_commit and auth.pr_base_commit != job.base_commit:
            return MergeResult(
                job_id=job_id,
                success=False,
                error=f"Authorization base commit mismatch: expected {job.base_commit}, got {auth.pr_base_commit}",
            )
        if auth.candidate_tree and auth.candidate_tree != (
            job.candidate_tree or job.candidate_commit
        ):
            return MergeResult(
                job_id=job_id,
                success=False,
                error=f"Authorization candidate tree mismatch: expected {job.candidate_tree}, got {auth.candidate_tree}",
            )
        if auth.expected_merge_tree and auth.expected_merge_tree != (
            job.candidate_tree or job.candidate_commit
        ):
            return MergeResult(
                job_id=job_id,
                success=False,
                error=f"Authorization expected merge tree mismatch: expected {job.candidate_tree}, got {auth.expected_merge_tree}",
            )

        # Load manifest if not provided
        if manifest is None:
            manifest = self._load_manifest(job)

        # Dry-run check MUST happen BEFORE any state mutation or manifest promotion
        if self.dry_run:
            logger.info(
                "DRY RUN: strictly read-only execution for %s", job.review_job_id
            )
            return MergeResult(
                job_id=job.review_job_id,
                success=True,
                merge_sha="dry-run-sha",
                final_manifest_state=manifest.state.value if manifest else "DRY_RUN",
            )

        # Drive manifest & execution pipeline
        try:
            result = self._execute_merge(job, manifest, auth)
        except Exception as exc:
            logger.error("Merge failed for %s: %s", job_id, exc)
            self.queue.db.update_review_job_state(
                job_id, ReviewJobState.MERGE_VERIFICATION_FAILED
            )
            return MergeResult(job_id=job_id, success=False, error=str(exc))

        return result

    def _execute_merge(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
        auth: MergeAuthorization,
    ) -> MergeResult:
        # Step 1: Record CI checks (if manifest is in CLEAN state)
        if manifest.state == PromotionState.CLEAN:
            ci_checks = self._build_ci_checks(job, manifest)
            manifest = manifest.record_ci(ci_checks)

        # Step 2: Mark merge eligible (CI_GREEN → MERGE_ELIGIBLE)
        if manifest.state == PromotionState.CI_GREEN:
            manifest = manifest.mark_merge_eligible()

        # Step 3: Acquire Merge Lock and Record Attestation via MergeFactoryStore
        allowed_scopes = (
            "merge-judge",
            "merge-factory-admin",
            "tier-0-auto",
            "human-override",
        )
        if not auth.scope or auth.scope not in allowed_scopes:
            raise PermissionError(
                f"Authorization actor '{auth.actor}' scope '{auth.scope}' lacks required merge privileges"
            )
        scopes = list({auth.scope, "merge-judge"})
        principal = Principal(identity=auth.actor, scopes=scopes)
        manifest_digest = manifest.digest()

        attestation = self.mf_store.submit_attestation(
            issue_id=job.task_id,
            decision="APPROVE_MERGE",
            base_sha=job.base_commit,
            candidate_sha=job.candidate_commit,
            manifest_digest=manifest_digest,
            evidence_digest=manifest_digest,
            repository=job.repository,
            target="main",
            principal=principal,
        )

        _ = self.mf_store.acquire_lock(
            repository=job.repository,
            target="main",
            issue_id=job.task_id,
            base_sha=job.base_commit,
            candidate_sha=job.candidate_commit,
            manifest_digest=manifest_digest,
            evidence_digest=manifest_digest,
            approval_attestation_id=attestation["attestation_id"],
            ttl_seconds=300,
            principal=principal,
        )

        # Step 4: Transition job to MERGING
        self.queue.db.update_review_job_state(job.review_job_id, ReviewJobState.MERGING)

        try:
            # Step 5: Execute actual git merge
            source_branch = job.candidate_commit
            target_branch = "main"

            integration_manifest = integrate_pipeline_run(
                issue_id=job.task_id,
                branch=source_branch,
                target_branch=target_branch,
                repo_path=self.repo_path,
            )

            merge_sha = (
                integration_manifest.merge_sha
                if integration_manifest and hasattr(integration_manifest, "merge_sha")
                else "merged-sha"
            )

            # Step 6: Mark manifest MERGED
            manifest = manifest.mark_merged(
                candidate_sha=job.candidate_commit, merge_sha=merge_sha
            )

            # Step 7: Consume authorization and mark job MERGED
            self.queue.db.consume_authorization(auth.authorization_id)
            self.queue.db.update_review_job_state(
                job.review_job_id, ReviewJobState.MERGED
            )

            from prismatic.review_factory.events import emit_rf_event

            emit_rf_event(
                "review_factory.merge_completed",
                {
                    "review_job_id": job.review_job_id,
                    "task_id": job.task_id,
                    "merge_sha": merge_sha,
                    "actor": auth.actor,
                },
            )

            return MergeResult(
                job_id=job.review_job_id,
                success=True,
                merge_sha=merge_sha,
                integration_manifest=integration_manifest,
                final_manifest_state=manifest.state.value,
            )
        except Exception as exc:
            logger.error("Integration merge execution failed: %s", exc)
            self.queue.db.update_review_job_state(
                job.review_job_id, ReviewJobState.MERGE_VERIFICATION_FAILED
            )
            raise
        finally:
            self.mf_store.release_lock(
                repository=job.repository,
                target="main",
                issue_id=job.task_id,
                principal=principal,
            )

    def _load_manifest(self, job: ReviewJob) -> MergeCandidateManifest:
        if job.result_packet_path and Path(job.result_packet_path).exists():
            return MergeCandidateManifest.read(Path(job.result_packet_path))
        raise FileNotFoundError(
            f"Candidate manifest not found at {job.result_packet_path}"
        )

    def _build_ci_checks(
        self, job: ReviewJob, manifest: MergeCandidateManifest
    ) -> tuple:
        # Require real verified CI checks on manifest; fail closed if missing or red
        existing_checks = {c.name: c for c in manifest.ci_checks}
        for name in manifest.required_ci_checks:
            if (
                name not in existing_checks
                or existing_checks[name].conclusion.lower() != "success"
            ):
                raise ValueError(
                    f"Required CI check '{name}' missing or not green for candidate {job.candidate_commit}"
                )
        return manifest.ci_checks


def _cli_approve(job_id: str, actor: str, operator_key: str | None = None) -> None:
    if not actor:
        print("ERROR: --actor identity is required")
        raise PermissionError("Actor identity required")

    expected_key = os.environ.get("PRISMATIC_OPERATOR_KEY")
    if not expected_key or operator_key != expected_key:
        print("ERROR: Invalid or unconfigured operator security key — fail closed")
        raise PermissionError(
            "PRISMATIC_OPERATOR_KEY required and must match operator_key"
        )

    queue = ReviewQueue()
    job = queue.get_job(job_id)
    if job is None:
        print(f"ERROR: Review job {job_id} not found")
        sys.exit(1)

    auth_id = queue.authorize_merge(
        review_job_id=job_id,
        actor=actor,
        expires_minutes=60,
    )
    if auth_id is None:
        print(f"ERROR: Failed to authorize job {job_id} (must be in merge_ready state)")
        sys.exit(1)

    print(f"✅ Authorized: {job_id}")
    print(f"   Authorization ID: {auth_id}")
    print(f"   Actor: {actor}")
    print(f"   Tier: {job.risk_tier}")


def _cli_merge(job_id: str, dry_run: bool = False) -> None:
    queue = ReviewQueue()
    executor = MergeExecutor(queue=queue, dry_run=dry_run)

    result = executor.execute(job_id)
    if result.success:
        print(f"✅ Merged: {job_id}")
        print(f"   Merge SHA: {result.merge_sha}")
        print(f"   Manifest: {result.final_manifest_state}")
    else:
        print(f"❌ Failed: {job_id}")
        print(f"   Error: {result.error}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Review Factory Merge Executor CLI")
    sub = parser.add_subparsers(dest="command")

    approve_parser = sub.add_parser("approve", help="Approve a job for merge")
    approve_parser.add_argument("job_id", help="Review job ID")
    approve_parser.add_argument(
        "--actor", required=True, help="Operator identity (e.g. michael)"
    )
    approve_parser.add_argument("--operator-key", help="Operator security key")

    merge_parser = sub.add_parser("merge", help="Execute merge")
    merge_parser.add_argument("job_id", help="Review job ID")
    merge_parser.add_argument("--dry-run", action="store_true", help="Dry-run mode")

    args = parser.parse_args()

    if args.command == "approve":
        _cli_approve(args.job_id, actor=args.actor, operator_key=args.operator_key)
    elif args.command == "merge":
        _cli_merge(args.job_id, dry_run=args.dry_run)
    else:
        parser.print_help()
        sys.exit(1)
