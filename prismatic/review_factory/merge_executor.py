"""RF-4: Merge Executor — Deep PE Integration.

Drives two parallel promotion tracks:
1. ``MergeCandidateManifest``: CI_GREEN → MERGE_ELIGIBLE → MERGED
2. ``PipelineStateMachine``: REVIEW → INTEGRATE → COMPLETED

Uses ``MergeFactoryStore`` for attestation and merge lock acquisition,
and ``integrate_pipeline_run()`` for the actual git merge.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
from prismatic.verification.receipt_store import VerificationReceiptStore

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Outcome of a merge execution."""

    job_id: str
    success: bool
    merge_sha: str = ""
    error: str = ""
    integration_manifest: Optional[IntegrationManifest] = None
    final_manifest_state: str = ""


class MergeExecutor:
    """RF-4: Execute merges through the PE promotion pipeline."""

    def __init__(
        self,
        queue: Optional[ReviewQueue] = None,
        dry_run: bool = False,
        repo_path: Optional[Path] = None,
        mf_store: Optional[MergeFactoryStore] = None,
        verification_receipt_store: Optional[VerificationReceiptStore] = None,
    ):
        self.queue = queue or ReviewQueue()
        self.dry_run = dry_run
        self.repo_path = repo_path
        self.mf_store = mf_store or MergeFactoryStore()
        self.verification_receipt_store = verification_receipt_store

    def execute(
        self,
        job_id: str,
        manifest: Optional[MergeCandidateManifest] = None,
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

        # Load and bind the durable manifest. A caller-supplied object cannot
        # bypass a file-backed reviewed packet.
        try:
            if job.result_packet_path:
                durable_manifest = self._load_manifest(job)
                if (
                    manifest is not None
                    and manifest.digest() != durable_manifest.digest()
                ):
                    return MergeResult(
                        job_id=job_id,
                        success=False,
                        error="Supplied manifest does not match the durable result packet",
                    )
                manifest = durable_manifest
            elif manifest is None:
                manifest = self._load_manifest(job)
        except Exception as exc:
            return MergeResult(job_id=job_id, success=False, error=str(exc))

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
            # Fail closed without mutating job state here: a pre-claim
            # failure (validation error, lost atomic claim) must leave the
            # job and authorization untouched so a contention loser cannot
            # poison the winner's merge. Post-claim failures are recorded by
            # _execute_merge's own handler after a successful claim.
            logger.error("Merge failed for %s: %s", job_id, exc)
            return MergeResult(job_id=job_id, success=False, error=str(exc))

        return result

    def _git_rev_parse(self, ref: str) -> str:
        import subprocess

        completed = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _is_exact_merge_commit(
        self,
        *,
        merge_sha: str,
        original_head: str,
        candidate_commit: str,
    ) -> bool:
        """Prove a commit is the authorized no-ff merge before rollback."""
        import subprocess

        completed = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", merge_sha],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
            text=True,
        )
        parts = completed.stdout.strip().split()
        return (
            len(parts) >= 3
            and parts[0] == merge_sha
            and parts[1] == original_head
            and candidate_commit in parts[2:]
        )

    def _rollback_target(
        self,
        *,
        target_branch: str,
        original_head: str,
        failed_head: str,
    ) -> None:
        """CAS the exact failed merge ref back without overwriting advancement."""
        import subprocess

        subprocess.run(
            [
                "git",
                "update-ref",
                f"refs/heads/{target_branch}",
                original_head,
                failed_head,
            ],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
        )
        # Restore index/worktree content only when this checkout's symbolic HEAD
        # is the target. Unlike ``reset --hard``, read-tree performs no ref move.
        symbolic_head = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if symbolic_head == target_branch:
            subprocess.run(
                ["git", "read-tree", "--reset", "-u", original_head],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
            )

    def _execute_merge(
        self,
        job: ReviewJob,
        manifest: MergeCandidateManifest,
        auth: MergeAuthorization,
    ) -> MergeResult:
        # Step 1: Validate every required check against a durable provider-neutral
        # receipt, even when the caller supplies a pre-promoted manifest.
        ci_checks = self._build_ci_checks(job, manifest)
        if manifest.state == PromotionState.CLEAN:
            manifest = manifest.record_ci(ci_checks)
        if manifest.state == PromotionState.CI_GREEN:
            manifest = manifest.mark_merge_eligible()
        if manifest.state != PromotionState.MERGE_ELIGIBLE:
            raise PermissionError(
                f"Manifest state {manifest.state.value} is not merge eligible"
            )

        # Step 2: Validate authority scope and identity.
        allowed_scopes = (
            "tier-0-auto",
            "tier-1-auto",
            "tier-2-exception",
            "tier-3-exception",
        )
        if not auth.actor or not auth.actor.strip():
            raise PermissionError("Authorization actor identity is required")
        if not auth.scope or auth.scope not in allowed_scopes:
            raise PermissionError(
                f"Authorization actor '{auth.actor}' scope '{auth.scope}' lacks required merge privileges"
            )
        # Scope/identity are revalidated from the authoritative row returned by
        # the atomic claim before a merge-factory principal is constructed.

        # Step 3: Bind the manifest and target to the exact review job.
        expected_changed_paths = tuple(
            sorted(json.loads(job.changed_paths_json or "[]"))
        )
        expected_risk_tier = {0: "A", 1: "B", 2: "C", 3: "C"}.get(job.risk_tier)
        manifest_bindings = {
            "issue_id": (manifest.issue_id, job.task_id),
            "task_id": (manifest.task_id, job.task_id),
            "repository": (manifest.repository, job.repository),
            "base_sha": (manifest.base_sha, job.base_commit),
            "candidate_sha": (manifest.candidate_sha, job.candidate_commit),
            "changed_paths": (manifest.changed_paths, expected_changed_paths),
            "risk_tier": (manifest.risk_tier.value, expected_risk_tier),
            "proof_policy_version": (
                f"v{manifest.proof_policy_version}",
                job.policy_version,
            ),
        }
        for field, (observed, expected) in manifest_bindings.items():
            if observed != expected:
                raise PermissionError(
                    f"Manifest {field} mismatch: expected {expected}, got {observed}"
                )
        target_branch = manifest.target
        if not target_branch:
            raise PermissionError(
                f"Manifest target is unset; refusing to merge {job.review_job_id}"
            )
        expected_merge_tree = auth.expected_merge_tree
        if not expected_merge_tree:
            raise PermissionError(
                f"Authorization expected merge tree is unset for {job.review_job_id}"
            )
        manifest_digest = manifest.digest()

        # Step 4: Atomically claim one-shot authority and transition the job from
        # merge_authorized to merging.  A contention loser changes neither row
        # and exits before any Git, lock, or attestation side effect.
        claimed_auth = self.queue.db.claim_authorization_for_merge(
            auth.authorization_id,
            review_job_id=job.review_job_id,
            repository=job.repository,
            pr_head_commit=job.candidate_commit,
            pr_base_commit=job.base_commit,
            candidate_tree=job.candidate_tree or job.candidate_commit,
            expected_merge_tree=expected_merge_tree,
            policy_version=job.policy_version,
        )
        if claimed_auth is None:
            raise PermissionError(
                f"Authorization {auth.authorization_id} could not be atomically claimed"
            )
        auth = claimed_auth
        if not auth.actor or not auth.actor.strip() or auth.scope not in allowed_scopes:
            raise PermissionError("Claimed authorization identity or scope is invalid")
        scopes = [auth.scope, "rf-merge-executor"]
        principal = Principal(
            identity=f"rf-claim:{auth.authorization_id}:{auth.actor}", scopes=scopes
        )

        target_head_before: str | None = None
        merge_sha_created: str | None = None
        lock_token = ""
        lock_acquired = False
        try:
            target_head_before = self._git_rev_parse(target_branch)
            if target_head_before != job.base_commit:
                raise PermissionError(
                    f"Target {target_branch} advanced: expected {job.base_commit}, "
                    f"found {target_head_before}"
                )
            attestation = self.mf_store.submit_attestation(
                issue_id=job.task_id,
                decision="APPROVE_MERGE",
                base_sha=job.base_commit,
                candidate_sha=job.candidate_commit,
                manifest_digest=manifest_digest,
                evidence_digest=manifest_digest,
                repository=job.repository,
                target=target_branch,
                principal=principal,
            )
            lock_record = self.mf_store.acquire_lock(
                repository=job.repository,
                target=target_branch,
                issue_id=job.task_id,
                base_sha=job.base_commit,
                candidate_sha=job.candidate_commit,
                manifest_digest=manifest_digest,
                evidence_digest=manifest_digest,
                approval_attestation_id=attestation["attestation_id"],
                ttl_seconds=300,
                principal=principal,
            )
            lock_token = str(lock_record.get("acquisition_token", ""))
            if not lock_token:
                raise RuntimeError("Merge lock did not return an acquisition token")
            lock_acquired = True
            locked_target_head = self._git_rev_parse(target_branch)
            if locked_target_head != job.base_commit:
                raise PermissionError(
                    f"Target {target_branch} advanced after lock acquisition: "
                    f"expected {job.base_commit}, found {locked_target_head}"
                )

            # Step 5: Execute real Git merge against the manifest target.
            integration_manifest = integrate_pipeline_run(
                issue_id=job.task_id,
                branch=job.candidate_commit,
                target_branch=target_branch,
                repo_path=self.repo_path,
                expected_target_head=job.base_commit,
                require_manifest_persistence=True,
            )
            merge_sha_created = integration_manifest.merge_sha or None
            if not integration_manifest.is_success():
                raise RuntimeError(
                    integration_manifest.error_message
                    or "Integration pipeline returned a non-success status"
                )
            if integration_manifest.test_results.get("passed") is False:
                raise RuntimeError(
                    integration_manifest.error_message
                    or "Integration pipeline tests returned failure"
                )
            merge_sha = integration_manifest.merge_sha
            if not merge_sha:
                raise RuntimeError("Integration did not return a merge SHA")

            merge_tree = self._git_rev_parse(f"{merge_sha}^{{tree}}")
            if merge_tree != expected_merge_tree:
                raise PermissionError(
                    f"Result tree {merge_tree} does not match expected "
                    f"{expected_merge_tree}; refusing merge"
                )

            # Step 8: Atomically persist the merged manifest when this job is
            # file-backed, then require durable MERGING -> MERGED finalization.
            pre_merge_manifest = manifest
            manifest = manifest.mark_merged(
                candidate_sha=job.candidate_commit, merge_sha=merge_sha
            )
            manifest_persisted = False
            if job.result_packet_path:
                manifest.write(Path(job.result_packet_path))
                manifest_persisted = True

            if not self.queue.db.update_review_job_state(
                job.review_job_id, ReviewJobState.MERGED
            ):
                if manifest_persisted:
                    pre_merge_manifest.write(Path(job.result_packet_path))
                raise RuntimeError(
                    f"Job {job.review_job_id} could not finalize durable merged state"
                )

            from prismatic.review_factory.events import emit_rf_event

            emit_rf_event(
                "review_factory.merge_completed",
                {
                    "review_job_id": job.review_job_id,
                    "task_id": job.task_id,
                    "merge_sha": merge_sha,
                    "actor": auth.actor,
                    "target": target_branch,
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
            rollback_error: Exception | None = None
            if target_head_before is not None:
                try:
                    current_target_head = self._git_rev_parse(target_branch)
                    if current_target_head != target_head_before:
                        failed_head = merge_sha_created or current_target_head
                        if (
                            current_target_head != failed_head
                            or not self._is_exact_merge_commit(
                                merge_sha=failed_head,
                                original_head=target_head_before,
                                candidate_commit=job.candidate_commit,
                            )
                        ):
                            raise RuntimeError(
                                "Rollback refused: current target is not the exact "
                                "merge commit created from the authorized parents"
                            )
                        self._rollback_target(
                            target_branch=target_branch,
                            original_head=target_head_before,
                            failed_head=failed_head,
                        )
                        # Watchdog metrics feed (Phase 0 observe-only): the
                        # auto-merge was rolled back. A feed failure must
                        # never break the hot path.
                        try:
                            from prismatic.review_factory.metrics_feed import (
                                record_rollback,
                            )

                            record_rollback(
                                job_id=job.review_job_id,
                                merge_sha=failed_head,
                                reason=(
                                    "integration merge failed; target "
                                    f"{target_branch} CAS-rolled back: {exc}"
                                ),
                            )
                        except Exception:
                            logger.warning(
                                "watchdog feed record_rollback failed",
                                exc_info=True,
                            )
                except Exception as rollback_exc:
                    rollback_error = rollback_exc
                    logger.error(
                        "CAS rollback of %s failed: %s", target_branch, rollback_exc
                    )
            if not self.queue.db.update_review_job_state(
                job.review_job_id, ReviewJobState.MERGE_VERIFICATION_FAILED
            ):
                logger.error(
                    "Could not persist merge_verification_failed for %s",
                    job.review_job_id,
                )
            if rollback_error is not None:
                raise RuntimeError(
                    f"Merge failed and rollback was refused or failed: {rollback_error}"
                ) from exc
            raise
        finally:
            if lock_acquired:
                self.mf_store.release_lock(
                    repository=job.repository,
                    target=target_branch,
                    issue_id=job.task_id,
                    principal=principal,
                    acquisition_token=lock_token,
                )

    def _load_manifest(self, job: ReviewJob) -> MergeCandidateManifest:
        if job.result_packet_path and Path(job.result_packet_path).exists():
            # Verify the durable packet has not been tampered with.
            if job.result_packet_sha256:
                import hashlib

                actual = hashlib.sha256(
                    Path(job.result_packet_path).read_bytes()
                ).hexdigest()
                if actual != job.result_packet_sha256:
                    raise ValueError(
                        f"Durable result packet digest mismatch for {job.review_job_id}: "
                        f"expected {job.result_packet_sha256}, got {actual}"
                    )
            return MergeCandidateManifest.read(Path(job.result_packet_path))
        raise FileNotFoundError(
            f"Candidate manifest not found at {job.result_packet_path}"
        )

    def _build_ci_checks(
        self, job: ReviewJob, manifest: MergeCandidateManifest
    ) -> tuple:
        """Validate required checks against durable provider-neutral receipts."""

        existing_checks = {check.name: check for check in manifest.ci_checks}
        if not manifest.required_ci_checks:
            raise ValueError(
                f"Manifest for {job.review_job_id} has no required verification checks"
            )
        receipt_store = self.verification_receipt_store or VerificationReceiptStore()
        validated = []
        for name in manifest.required_ci_checks:
            check = existing_checks.get(name)
            if check is None or check.conclusion != "SUCCESS":
                raise ValueError(
                    f"Required CI check '{name}' missing or not green for candidate {job.candidate_commit}"
                )
            if check.head_sha != job.candidate_commit:
                raise ValueError(
                    f"Required CI check '{name}' head mismatch for candidate {job.candidate_commit}"
                )
            if not all(
                (
                    check.provider_receipt_id,
                    check.provider_receipt_sha256,
                    check.provider_policy_sha256,
                )
            ):
                raise ValueError(
                    f"Required CI check '{name}' lacks provider-neutral receipt provenance"
                )
            try:
                stored = receipt_store.get(check.provider_receipt_id)
            except KeyError as exc:
                raise ValueError(
                    f"Required CI check '{name}' references an unknown provider-neutral receipt"
                ) from exc
            if stored.receipt_sha256 != check.provider_receipt_sha256:
                raise ValueError(f"Required CI check '{name}' receipt digest mismatch")
            validated.append(check)
        return tuple(validated)


def _cli_approve(job_id: str, actor: str, operator_key: Optional[str] = None) -> None:
    if not actor:
        print("ERROR: --actor identity is required")
        sys.exit(1)

    expected_key = os.environ.get("PRISMATIC_OPERATOR_KEY")
    if expected_key and operator_key != expected_key:
        print("ERROR: Invalid operator security key")
        sys.exit(1)

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
