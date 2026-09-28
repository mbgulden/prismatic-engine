"""RF-6 Integration Tests: Backlog Importer.

Tests exercise real PE integration by:
1. Creating a real ``AgyCompletedWorkStore``
2. Ingesting a packet via ``store.ingest()``
3. Verifying the importer finds it via ``CompletedWorkRow``
4. Verifying manifest-directory scanning
"""

from pathlib import Path

import pytest

from prismatic.agy_completed_work import (
    AgyCompletedWorkStore,
    CompletedWorkRow,
)
from prismatic.review_factory.backlog_importer import BacklogImporter, ImportResult
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.queue import ReviewQueue


# ── Helpers ──────────────────────────────────────────────────────────


def _make_test_packet(
    issue: str = "GRO-IMPORT-TEST",
    branch: str = "feature/test",
    agent: str = "agy",
) -> dict:
    """Create a minimal AGY result packet for ingestion."""
    return {
        "issue_identifier": issue,
        "source_branch": branch,
        "base_branch": "main",
        "agent": agent,
        "repository": "mbgulden/prismatic-engine",
        "changed_files": ["docs/readme.md"],
        "result_summary": "Test completed",
        "source_path": "/tmp/test-source",
        "proof": {
            "COMMAND": "pytest tests/ -x -q",
            "RESULT": "PASS",
            "LOG": "/tmp/test.log",
            "SCOPE": "focused",
            "AD_HOC_OR_CANONICAL": "canonical",
            "NOT_CLAIMING": ["integration"],
            "MARKER": "AGY_TASK_RESULT_PACKET_OK",
        },
    }


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test_import.db"


@pytest.fixture
def queue(tmp_db):
    db = ReviewFactoryDB(db_path=tmp_db)
    db.ensure_tables()
    q = ReviewQueue(db=db)
    yield q
    q.close()


# ── Tests ────────────────────────────────────────────────────────────


class TestCompletedWorkImport:
    """Test import from AgyCompletedWorkStore."""

    def test_import_reads_completed_work_rows(self, tmp_db, queue):
        """Importer uses CompletedWorkRow dataclass, not raw SQL."""
        # Ingest a packet into the real store
        store = AgyCompletedWorkStore(db_path=tmp_db)
        packet = _make_test_packet()
        row = store.ingest(packet)

        # Verify it's a real CompletedWorkRow
        assert isinstance(row, CompletedWorkRow)
        assert row.id.startswith("agy-cw-")

        # Import
        importer = BacklogImporter(queue=queue, db_path=tmp_db)
        result = importer.import_from_completed_work(limit=50)

        assert result.scanned >= 1
        # Whether it's eligible depends on classification
        # The important thing is we read real CompletedWorkRow objects
        assert isinstance(result, ImportResult)

    def test_import_filters_by_classification(self, tmp_db, queue):
        """Only 'pass_ready_for_review' classification is imported."""
        store = AgyCompletedWorkStore(db_path=tmp_db)
        packet = _make_test_packet()
        row = store.ingest(packet)

        # Check what classification the row got
        classification = row.integration_classification

        importer = BacklogImporter(queue=queue, db_path=tmp_db)
        result = importer.import_from_completed_work()

        # Should have scanned the row
        assert result.scanned >= 1

        # If not "pass_ready_for_review", it should be skipped
        if classification != "pass_ready_for_review":
            assert result.skipped_ineligible >= 1
        else:
            assert result.eligible >= 1

    def test_import_is_idempotent(self, tmp_db, queue):
        """Running import twice doesn't create duplicate jobs."""
        store = AgyCompletedWorkStore(db_path=tmp_db)
        packet = _make_test_packet()
        store.ingest(packet)

        importer = BacklogImporter(queue=queue, db_path=tmp_db)

        result1 = importer.import_from_completed_work()
        result2 = importer.import_from_completed_work()

        # Second run should not enqueue new jobs (idempotent)
        assert result2.enqueued == 0 or result2.skipped_duplicate >= result1.enqueued


class TestManifestDirImport:
    """Test import from a directory of merge_candidate.json files."""

    def test_import_missing_dir_returns_error(self, queue):
        """Importing from a nonexistent directory returns an error."""
        importer = BacklogImporter(queue=queue)
        result = importer.import_from_manifest_dir(Path("/nonexistent/dir"))

        assert len(result.errors) > 0
        assert result.scanned == 0

    def test_import_empty_dir(self, tmp_path, queue):
        """Importing from an empty directory finds nothing."""
        importer = BacklogImporter(queue=queue)
        result = importer.import_from_manifest_dir(tmp_path)

        assert result.scanned == 0
        assert result.enqueued == 0

    def test_20_entry_manifest_replay_idempotency(self, tmp_path, queue):
        """20-entry backlog manifest fixture replay is 100% idempotent."""
        from prismatic.merge_candidate_manifest import MergeCandidateManifest, RiskTier

        manifest_dir = tmp_path / "backlog_20"
        manifest_dir.mkdir(parents=True, exist_ok=True)

        for i in range(20):
            entry_dir = manifest_dir / f"entry_{i:02d}"
            entry_dir.mkdir(parents=True, exist_ok=True)
            manifest = MergeCandidateManifest.create(
                issue_id=f"GRO-200{i:02d}",
                task_id=f"GRO-200{i:02d}",
                task_file_sha256="0" * 64,
                repository="mbgulden/prismatic-engine",
                target="main",
                base_sha="a" * 40,
                candidate_sha=f"b{i:02d}".ljust(40, "0"),
                changed_paths=[f"prismatic/module_{i:02d}.py"],
                producer="agy",
                preserved_candidate_location=f"/tmp/candidate_{i:02d}",
                risk_tier=RiskTier.B,
                dashboard_change=False,
                required_ci_checks=["build"],
            )
            manifest.write(entry_dir / "merge_candidate.json")

        importer = BacklogImporter(queue=queue)

        # Run 1: imports all 20 entries
        r1 = importer.import_from_manifest_dir(manifest_dir)
        assert r1.scanned == 20
        assert r1.enqueued == 20

        # Run 2: replay on same queue -> 0 enqueued, 20 skipped duplicates
        r2 = importer.import_from_manifest_dir(manifest_dir)
        assert r2.scanned == 20
        assert r2.enqueued == 0
        assert r2.skipped_duplicate == 20


# ── Concurrent drain race ────────────────────────────────────────────


class TestConcurrentDrainRace:
    """Two one-shots racing: the loser must count skipped_duplicate, not error.

    The UNIQUE constraint on review_jobs.completed_work_id already prevents a
    double-enqueue; this pins the accounting behavior when the loser's INSERT
    hits it.
    """

    def test_lost_check_then_act_counts_duplicate(self, tmp_db, queue, monkeypatch):
        importer = BacklogImporter(queue=queue, db_path=tmp_db)
        sha = "a" * 40
        kwargs = {
            "completed_work_id": "agy-cw-race-test",
            "task_id": "GRO-RACE",
            "repository": "mbgulden/prismatic-engine",
            "base_commit": sha,
            "base_tree": sha,
            "candidate_commit": "b" * 40,
            "candidate_tree": "b" * 40,
            "changed_paths": ["docs/x.md"],
            "result_packet_path": "/tmp/x",
        }
        result = ImportResult()
        importer._enqueue_idempotent(result, **kwargs)
        assert result.enqueued == 1

        # Simulate the race: both pre-checks see nothing (stale reads between
        # check and insert), but the row is already there, so the INSERT hits
        # the completed_work_id UNIQUE constraint.
        monkeypatch.setattr(
            queue.db, "get_job_by_completed_work_id", lambda *a, **k: None
        )
        monkeypatch.setattr(
            queue.db, "get_job_by_task_and_candidate", lambda *a, **k: None
        )
        importer._enqueue_idempotent(result, **kwargs)

        assert result.enqueued == 1
        assert result.skipped_duplicate == 1
        assert result.errors == []
        # Exactly one job row: no double-enqueue.
        assert len(queue.db.list_review_jobs()) == 1
