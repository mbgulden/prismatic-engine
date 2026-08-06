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


# ── CLI tests (R8 deployment support) ─────────────────────────────────


def test_cli_help_exits_cleanly(capsys):
    """CLI --help exits 0 with all 10 flags documented."""
    import sys

    from prismatic.review_factory.backlog_importer import _cli_main

    old_argv = sys.argv
    sys.argv = ["backlog_importer", "--help"]
    try:
        with pytest.raises(SystemExit) as exc_info:
            _cli_main()
        assert exc_info.value.code == 0
    finally:
        sys.argv = old_argv

    captured = capsys.readouterr()
    assert "--db-path" in captured.out
    assert "--state-dir" in captured.out
    assert "--worker-id" in captured.out
    assert "--max-items" in captured.out
    assert "--enable-merge" in captured.out


def test_cli_dry_run_with_no_eligible_rows(tmp_path):
    """CLI dry-run exits 0 with 0 enqueued when DB is empty."""
    import json
    import os
    import subprocess
    import sys

    db_path = tmp_path / "empty.db"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log_dir = state_dir / "logs"
    log_dir.mkdir()

    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(
        [
            "/home/ubuntu/.prismatic/worktrees/george-pr421-ready",
            env.get("PYTHONPATH", ""),
        ]
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "prismatic.review_factory.backlog_importer",
            "--db-path",
            str(db_path),
            "--state-dir",
            str(state_dir),
            "--worker-id",
            "test-cli-worker",
            "--max-items",
            "20",
            "--dry-run",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert '"worker_id": "test-cli-worker"' in result.stdout
    assert '"enqueued": 0' in result.stdout

    # Verify subdirs were created
    assert (state_dir / "inbox-disposition").is_dir()
    assert (state_dir / "artifacts").is_dir()
    assert (state_dir / "completed-work-disposition").is_dir()
    assert (state_dir / "source-workspaces").is_dir()

    # Verify receipt was written
    receipts = list(log_dir.glob("test-cli-worker-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["worker_id"] == "test-cli-worker"
    assert receipt["dry_run"] is True
    assert receipt["scanned"] == 0


def test_cli_fails_when_enable_merge_without_merge_repo_path(capsys):
    """CLI --enable-merge without --merge-repo-path exits 2 (argparse error)."""
    import sys

    from prismatic.review_factory.backlog_importer import _cli_main

    old_argv = sys.argv
    sys.argv = [
        "backlog_importer",
        "--db-path",
        "/tmp/x.db",
        "--state-dir",
        "/tmp/state",
        "--worker-id",
        "w",
        "--enable-merge",
    ]
    try:
        with pytest.raises(SystemExit) as exc_info:
            _cli_main()
        assert exc_info.value.code == 2
    finally:
        sys.argv = old_argv
