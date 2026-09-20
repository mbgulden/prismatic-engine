"""Tests for RF-1: Review job queue and state machine.

Acceptance marker: PE_REVIEW_FACTORY_QUEUE_OK
Required: 3/3 PASS minimum + enqueue_completed_work returns a UUID.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure the test can find the prismatic package
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    ReviewDecision,
    ReviewJobState,
    ReviewVerdict,
    RiskTier,
    VerificationReceipt,
)
from prismatic.review_factory.queue import ReviewQueue


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary review factory DB."""
    db_path = tmp_path / "test_review_factory.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    return db


@pytest.fixture
def queue(tmp_db):
    """Create a ReviewQueue with a temporary DB."""
    q = ReviewQueue(db=tmp_db)
    yield q
    q.close()


class TestEnqueueCompletedWork:
    """Test suite for intake pipeline."""

    def test_enqueue_returns_uuid(self, queue):
        """Enqueue a completed work and verify it returns a valid UUID."""
        job_id = queue.enqueue_completed_work(
            completed_work_id="agy-cw-test-001",
            task_id="GRO-4188",
            repository="mbgulden/prismatic-engine",
            base_commit="21be7812",
            candidate_commit="c09761ed",
            changed_paths=["prismatic/review/hooks.py"],
        )
        # Verify it's a valid UUID
        uuid.UUID(job_id)  # raises ValueError if invalid
        assert job_id

    def test_enqueue_idempotent(self, queue):
        """Re-enqueueing the same completed_work_id is a no-op."""
        job_id_1 = queue.enqueue_completed_work(
            completed_work_id="agy-cw-test-002",
            task_id="GRO-4188",
            repository="mbgulden/prismatic-engine",
            base_commit="21be7812",
            candidate_commit="c09761ed",
        )
        job_id_2 = queue.enqueue_completed_work(
            completed_work_id="agy-cw-test-002",
            task_id="GRO-4188",
            repository="mbgulden/prismatic-engine",
            base_commit="21be7812",
            candidate_commit="c09761ed",
        )
        assert job_id_1 == job_id_2

    def test_enqueue_classifies_risk_tier(self, queue):
        """Enqueue classifies docs as Tier 0 and auth as Tier 2."""
        # Tier 0: docs-only change
        job_id = queue.enqueue_completed_work(
            completed_work_id="agy-cw-tier0",
            task_id="GRO-0001",
            repository="mbgulden/prismatic-engine",
            base_commit="aaaa",
            candidate_commit="bbbb",
            changed_paths=["docs/readme.md"],
        )
        job = queue.db.get_review_job(job_id)
        assert job.risk_tier == RiskTier.DETERMINISTIC_ONLY

        # Tier 2: auth change
        job_id_2 = queue.enqueue_completed_work(
            completed_work_id="agy-cw-tier2",
            task_id="GRO-0002",
            repository="mbgulden/prismatic-engine",
            base_commit="aaaa",
            candidate_commit="cccc",
            changed_paths=["prismatic/auth/oauth.py"],
        )
        job_2 = queue.db.get_review_job(job_id_2)
        assert job_2.risk_tier == RiskTier.SENSITIVE


class TestStateTransitions:
    """Test the full lifecycle through the state machine."""

    def test_full_tier1_lifecycle(self, queue):
        """Happy path: enqueue → verify → review → merge_ready → authorized."""
        # 1. Enqueue
        job_id = queue.enqueue_completed_work(
            completed_work_id="agy-cw-lifecycle",
            task_id="GRO-4188",
            repository="mbgulden/prismatic-engine",
            base_commit="21be7812",
            candidate_commit="c09761ed",
            changed_paths=["prismatic/core/router.py"],
        )

        # 2. Lease for verification
        job = queue.lease_for_verification("verifier-1")
        assert job is not None
        assert job.state == ReviewJobState.VERIFYING.value
        assert job.review_job_id == job_id

        # 3. Complete verification
        receipt = VerificationReceipt(
            review_job_id=job_id,
            candidate_commit="c09761ed",
            candidate_tree="c09761ed",
            classification="targeted",
        )
        assert queue.complete_verification(job_id, receipt, worker_id="verifier-1")

        # Verify state
        updated = queue.db.get_review_job(job_id)
        assert updated.state == ReviewJobState.REVIEW_READY.value

        # 4. Lease for review
        leased = queue.lease_for_review("agy-v1.0")
        assert leased is not None
        assert leased.state == ReviewJobState.REVIEWING.value

        # 5. Submit clean verdict
        decision = ReviewDecision(
            review_job_id=job_id,
            reviewer_id="agy-v1.0",
            reviewer_capability_version="prismatic-review-worker==0.1.0",
            candidate_commit="c09761ed",
            candidate_tree="c09761ed",
            receipt_id=receipt.receipt_id,
            verdict=ReviewVerdict.CLEAN.value,
        )
        new_state = queue.submit_verdict(job_id, decision, reviewer_id="agy-v1.0")
        assert new_state == ReviewJobState.MERGE_READY.value

        # 6. Authorize explicitly (strict: tier-1 auto-merges require the
        # standing-policy actor, not a bare human name)
        auth_id = queue.authorize_merge(job_id, actor="standing-policy: tier-1")
        assert auth_id is not None

        final = queue.db.get_review_job(job_id)
        assert final.state == ReviewJobState.MERGE_AUTHORIZED.value

    def test_repair_cycle(self, queue):
        """Repair path: review finds issues → repair_required → re-queue."""
        job_id = queue.enqueue_completed_work(
            completed_work_id="agy-cw-repair",
            task_id="GRO-0003",
            repository="mbgulden/prismatic-engine",
            base_commit="aaaa",
            candidate_commit="bbbb",
            changed_paths=["prismatic/core/router.py"],
        )

        # Verify
        _ = queue.lease_for_verification("verifier-1")
        receipt = VerificationReceipt(
            review_job_id=job_id,
            candidate_commit="bbbb",
            candidate_tree="bbbb",
        )
        queue.complete_verification(job_id, receipt, worker_id="verifier-1")

        # Review → repair_required
        _ = queue.lease_for_review("agy-v1.0")
        decision = ReviewDecision(
            review_job_id=job_id,
            reviewer_id="agy-v1.0",
            candidate_commit="bbbb",
            candidate_tree="bbbb",
            receipt_id=receipt.receipt_id,
            verdict=ReviewVerdict.REPAIR_REQUIRED.value,
            findings=json.dumps(
                [
                    {
                        "severity": "error",
                        "path": "prismatic/core/router.py",
                        "line": 42,
                        "invariant": "missing error handling",
                        "reproduction_command": "pytest tests/test_router.py -k test_error",
                    }
                ]
            ),
        )
        new_state = queue.submit_verdict(job_id, decision, reviewer_id="agy-v1.0")
        assert new_state == ReviewJobState.REPAIR_REQUIRED.value

        # Verify repair packet was created
        packets = queue.db.get_unconsumed_repairs("bbbb")
        assert len(packets) == 1
        assert "missing error handling" in packets[0].findings_json

        # Consume repair → back to queued
        assert queue.consume_repair(job_id, "cccc")
        updated = queue.db.get_review_job(job_id)
        assert updated.state == ReviewJobState.QUEUED.value


class TestLeaseManagement:
    """Test lease enforcement and janitor."""

    def test_reviewer_cap_enforced(self, queue):
        """Concurrent reviewer cap (3) is enforced."""
        # Enqueue 4 jobs
        for i in range(4):
            _ = queue.enqueue_completed_work(
                completed_work_id=f"agy-cw-cap-{i}",
                task_id=f"GRO-{i}",
                repository="mbgulden/prismatic-engine",
                base_commit="aaaa",
                candidate_commit=f"bbbb{i}",
                changed_paths=["prismatic/core/router.py"],
            )
            # Fast-forward through verification
            job = queue.lease_for_verification(f"verifier-{i}")
            receipt = VerificationReceipt(
                review_job_id=job.review_job_id,
                candidate_commit=f"bbbb{i}",
                candidate_tree=f"bbbb{i}",
            )
            queue.complete_verification(
                job.review_job_id, receipt, worker_id=f"verifier-{i}"
            )

        # Lease 3 reviewers — should all succeed
        for i in range(3):
            leased = queue.lease_for_review(f"reviewer-{i}")
            assert leased is not None

        # 4th reviewer should be blocked (pool exhausted)
        blocked = queue.lease_for_review("reviewer-3")
        assert blocked is None

    def test_janitor_resets_stale_leases(self, queue):
        """Stale verifying leases get reset to queued."""
        job_id = queue.enqueue_completed_work(
            completed_work_id="agy-cw-stale",
            task_id="GRO-STALE",
            repository="mbgulden/prismatic-engine",
            base_commit="aaaa",
            candidate_commit="bbbb",
            changed_paths=["prismatic/core/router.py"],
        )

        # Lease for verification
        job = queue.lease_for_verification("verifier-stale")
        assert job is not None

        # Simulate stale lease by setting expiry in the past
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        queue.db.conn.execute(
            "UPDATE review_jobs SET lease_expires_at = ? WHERE review_job_id = ?",
            (past, job_id),
        )

        # Run janitor
        result = queue.run_janitor()
        assert result["stale_leases_reset"] >= 1

        # Job should be back in queued
        updated = queue.db.get_review_job(job_id)
        assert updated.state == ReviewJobState.QUEUED.value


class TestQueueStatistics:
    """Test queue depth and statistics."""

    def test_queue_depth(self, queue):
        """Queue depth reports correct counts by state."""
        queue.enqueue_completed_work(
            completed_work_id="agy-cw-stats-1",
            task_id="GRO-S1",
            repository="mbgulden/prismatic-engine",
            base_commit="aaaa",
            candidate_commit="bbbb",
        )
        queue.enqueue_completed_work(
            completed_work_id="agy-cw-stats-2",
            task_id="GRO-S2",
            repository="mbgulden/prismatic-engine",
            base_commit="aaaa",
            candidate_commit="cccc",
        )

        stats = queue.queue_depth()
        assert stats.get("queued", 0) == 2
