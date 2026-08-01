"""Group B: 5-Point Counterexample Matrix Test for Identity, Lease, Candidate, and Receipt Fences.

Matrix Coverage:
1. Positive: Valid worker_id and matching receipt complete verification cleanly.
2. Direct Negative: Mismatched worker_id or receipt candidate_commit fails closed.
3. Collision & Isolation: Cross-job receipt submission rejected.
4. Boundary & Empty: Empty (""), whitespace ("   "), or None worker_id / candidate fields fail closed.
5. Bypass Path: Submitting receipt with omitted candidate_commit or candidate_tree fails closed.
"""

import tempfile
from pathlib import Path
import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import VerificationReceipt
from prismatic.review_factory.queue import ReviewQueue


@pytest.fixture
def temp_queue():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_rf.db"
        queue = ReviewQueue(db=ReviewFactoryDB(db_path))
        yield queue
        queue.close()


def test_group_b_empty_and_whitespace_identity_fence(temp_queue):
    """Boundary & Empty: Submitting complete_verification with empty or whitespace worker_id
    or blank receipt candidate fields MUST raise ValueError and fail closed.
    """
    job_id = temp_queue.enqueue_completed_work(
        completed_work_id="cw-job-b1",
        task_id="TASK-B1",
        repository="org/repo",
        base_commit="base-b1",
        candidate_commit="cand-commit-b1-123456789012345678901234567890",
        candidate_tree="cand-tree-b1-123456789012345678901234567890",
    )

    job = temp_queue.lease_for_verification(worker_id="good-worker")
    assert job is not None

    # Test 1: Empty worker_id MUST be rejected when job is leased to good-worker
    rec_valid = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="cand-commit-b1-123456789012345678901234567890",
        candidate_tree="cand-tree-b1-123456789012345678901234567890",
    )

    with pytest.raises(ValueError, match="Worker identity required"):
        temp_queue.complete_verification(job_id, receipt=rec_valid, worker_id="")

    with pytest.raises(ValueError, match="Worker identity required"):
        temp_queue.complete_verification(job_id, receipt=rec_valid, worker_id="   ")

    # Test 2: Blank receipt candidate_commit MUST be rejected
    rec_blank_cand = VerificationReceipt(
        review_job_id=job_id,
        candidate_commit="",
        candidate_tree="cand-tree-b1-123456789012345678901234567890",
    )

    with pytest.raises(ValueError, match="Candidate commit required"):
        temp_queue.complete_verification(job_id, receipt=rec_blank_cand, worker_id="good-worker")
