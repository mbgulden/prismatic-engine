"""Production-path tests for exact immutable backlog identities."""

from __future__ import annotations

import pytest

from prismatic.agy_completed_work import CompletedWorkRow
from prismatic.review_factory.backlog_importer import BacklogImporter, ImportResult
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.queue import ReviewQueue


BASE = "a" * 40
CANDIDATE = "b" * 40
BASE_TREE = "c" * 40
CANDIDATE_TREE = "d" * 40


@pytest.fixture
def queue(tmp_path):
    db = ReviewFactoryDB(tmp_path / "review.db")
    db.ensure_tables()
    value = ReviewQueue(db=db)
    yield value
    value.close()


def _row(
    row_id: str,
    base: str = BASE,
    candidate: str = CANDIDATE,
    base_tree: str = BASE_TREE,
    candidate_tree: str = CANDIDATE_TREE,
):
    return CompletedWorkRow(
        id=row_id,
        created_at="",
        updated_at="",
        agent="agy",
        source_branch="candidate",
        source_path="",
        base_branch="main",
        classification="merge_ready",
        eligible_for_merge=True,
        requires_clean_rebuild=False,
        proof_result="PASS",
        proof_marker="proof",
        gate_marker="gate",
        ingestion_marker="ingested",
        packet={
            "issue": "PR421-GROUP-E",
            "repository": "local/repo",
            "base_commit": base,
            "candidate_commit": candidate,
            "base_tree": base_tree,
            "candidate_tree": candidate_tree,
            "changed_files": ["prismatic/review_factory/backlog_importer.py"],
        },
        gate={},
        non_claims=(),
        evidence_retention={},
    )


def _process(queue: ReviewQueue, row: CompletedWorkRow) -> ImportResult:
    result = ImportResult()
    BacklogImporter(queue=queue)._process_row(row, result)
    return result


def test_importer_enqueues_exact_lowercase_identities(queue):
    result = _process(queue, _row("cw-valid"))
    assert result.eligible == 1
    assert result.enqueued == 1
    assert result.skipped_ineligible == 0
    assert result.errors == []
    job = queue.db.get_job_by_completed_work_id("cw-valid")
    assert job is not None
    assert job.base_commit == BASE
    assert job.candidate_commit == CANDIDATE


@pytest.mark.parametrize(
    "invalid",
    [
        "deadbee",
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "a" * 20 + "A" * 20,
        " " * 40,
        "main",
        "0" * 40,
        "",
    ],
)
@pytest.mark.parametrize("field", ["base", "candidate"])
def test_importer_rejects_non_exact_commit_before_enqueue(queue, field, invalid):
    values = {"base": BASE, "candidate": CANDIDATE}
    values[field] = invalid
    row_id = f"cw-invalid-{field}-{len(invalid)}-{hash(invalid)}"

    result = _process(queue, _row(row_id, **values))

    assert result.enqueued == 0
    assert result.skipped_ineligible == 1
    assert len(result.errors) == 1
    assert queue.db.get_job_by_completed_work_id(row_id) is None


@pytest.mark.parametrize(
    "invalid",
    [
        "deadbee",
        "A" * 40,
        " " * 40,
        "0" * 40,
        "",
    ],
)
@pytest.mark.parametrize("field", ["base_tree", "candidate_tree"])
def test_importer_rejects_non_exact_tree_before_enqueue(queue, field, invalid):
    values = {"base_tree": BASE_TREE, "candidate_tree": CANDIDATE_TREE}
    values[field] = invalid
    row_id = f"cw-tree-{field}-{len(invalid)}-{hash(invalid)}"

    result = _process(queue, _row(row_id, **values))

    assert result.enqueued == 0
    assert result.skipped_ineligible == 1
    assert len(result.errors) == 1
    assert queue.db.get_job_by_completed_work_id(row_id) is None


def test_importer_isolates_two_rows_sharing_candidate(queue):
    result1 = _process(queue, _row("cw-collision-1"))
    result2 = _process(queue, _row("cw-collision-2"))

    assert result1.enqueued == 1
    assert result2.enqueued == 1
    job1 = queue.db.get_job_by_completed_work_id("cw-collision-1")
    job2 = queue.db.get_job_by_completed_work_id("cw-collision-2")
    assert job1 is not None and job2 is not None
    assert job1.review_job_id != job2.review_job_id
    assert job1.candidate_commit == job2.candidate_commit == CANDIDATE
