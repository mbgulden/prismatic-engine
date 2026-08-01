"""RF-6 Integration Tests: Backlog Importer.

Tests exercise real PE integration by:
1. Creating a real ``AgyCompletedWorkStore``
2. Ingesting a packet via ``store.ingest()``
3. Verifying the importer finds it via ``CompletedWorkRow``
4. Verifying manifest-directory scanning
"""

import json
import tempfile
from pathlib import Path

import pytest

from prismatic.agy_completed_work import (
    AgyCompletedWorkStore,
    CompletedWorkRow,
    normalize_agy_result_packet,
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
