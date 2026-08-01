"""RF-6: Backlog Importer — Deep PE Integration.

Imports pending work from ``AgyCompletedWorkStore`` into the Review
Factory queue using the real ``CompletedWorkRow`` dataclass.

Does NOT query raw SQL with guessed column names.
Instead, uses ``AgyCompletedWorkStore().list()`` →
``list[CompletedWorkRow]``, then filters by
``row.integration_classification == "pass_ready_for_review"``
and ``row.eligible_for_merge``.

Usage
-----
    from prismatic.review_factory.backlog_importer import BacklogImporter

    importer = BacklogImporter(queue=queue)
    result = importer.import_from_completed_work()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from prismatic.agy_completed_work import (
    AgyCompletedWorkStore,
    CompletedWorkRow,
)
from prismatic.merge_candidate_manifest import (
    MergeCandidateManifest,
)
from prismatic.review_factory.queue import ReviewQueue

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """Summary of an import run."""

    scanned: int = 0
    eligible: int = 0
    enqueued: int = 0
    skipped_duplicate: int = 0
    skipped_ineligible: int = 0
    errors: list[str] = field(default_factory=list)


class BacklogImporter:
    """RF-6: Import pending work from AgyCompletedWorkStore.

    Reads from the same ``agy_completed_work.db`` that the factory
    queue lives in. Uses ``CompletedWorkRow`` dataclass, not raw SQL.
    """

    def __init__(
        self,
        queue: Optional[ReviewQueue] = None,
        db_path: Optional[Path] = None,
    ):
        self.queue = queue or ReviewQueue()
        self._db_path = db_path

    def import_from_completed_work(self, limit: int = 50) -> ImportResult:
        """Import eligible completed work into the review queue.

        Reads ``AgyCompletedWorkStore.list()`` and filters for:
        1. ``row.integration_classification == "pass_ready_for_review"``
        2. ``row.eligible_for_merge == True``
        3. Not already enqueued (idempotent by ``completed_work_id``)

        Returns:
            ImportResult with counts and any errors.
        """
        result = ImportResult()

        # Use the real AgyCompletedWorkStore
        store = AgyCompletedWorkStore(db_path=self._db_path)
        rows = store.list(limit=limit)
        result.scanned = len(rows)

        for row in rows:
            try:
                self._process_row(row, result)
            except Exception as exc:
                result.errors.append(f"Error processing {row.id}: {exc}")
                logger.warning("Import error for %s: %s", row.id, exc)

        logger.info(
            "Import complete: scanned=%d eligible=%d enqueued=%d "
            "skipped_dup=%d skipped_ineligible=%d errors=%d",
            result.scanned,
            result.eligible,
            result.enqueued,
            result.skipped_duplicate,
            result.skipped_ineligible,
            len(result.errors),
        )

        return result

    def _process_row(self, row: CompletedWorkRow, result: ImportResult) -> None:
        """Process a single CompletedWorkRow for import."""
        # Filter 1: integration classification
        if row.integration_classification != "pass_ready_for_review":
            result.skipped_ineligible += 1
            return

        # Filter 2: eligible for merge
        if not row.eligible_for_merge:
            result.skipped_ineligible += 1
            return

        result.eligible += 1

        # Extract fields from the CompletedWorkRow dataclass
        completed_work_id = row.id  # "agy-cw-{digest}"
        packet = row.packet  # dict — the normalized AGY result packet

        task_id = (
            packet.get("issue")
            or packet.get("issue_identifier")
            or packet.get("scope", "")
        )
        repository = packet.get("repository", "mbgulden/prismatic-engine")
        base_commit = packet.get("base_commit") or row.base_branch or "main"
        candidate_commit = packet.get("candidate_commit") or row.source_branch or ""
        changed_paths = list(packet.get("changed_files", []))

        # Optional: result_packet_path for manifest loading
        result_packet_path = packet.get("result_packet_path") or packet.get(
            "source_path"
        )

        existing = self.queue.db.get_job_by_completed_work_id(completed_work_id)
        if existing:
            result.skipped_duplicate += 1
            return

        job_id = self.queue.enqueue_completed_work(
            completed_work_id=completed_work_id,
            task_id=task_id,
            repository=repository,
            base_commit=base_commit,
            candidate_commit=candidate_commit,
            changed_paths=changed_paths,
            result_packet_path=result_packet_path,
        )
        if job_id:
            result.enqueued += 1

    def import_from_manifest_dir(self, manifest_dir: Path) -> ImportResult:
        """Import from a directory of merge_candidate.json files.

        Scans ``manifest_dir`` for ``merge_candidate.json`` files,
        reads each as a ``MergeCandidateManifest``, and enqueues them.
        """
        result = ImportResult()

        if not manifest_dir.is_dir():
            result.errors.append(f"Not a directory: {manifest_dir}")
            return result

        manifest_files = list(manifest_dir.rglob("merge_candidate.json"))
        result.scanned = len(manifest_files)

        for manifest_path in manifest_files:
            try:
                manifest = MergeCandidateManifest.read(manifest_path)
                self._process_manifest(manifest, manifest_path, result)
            except Exception as exc:
                result.errors.append(f"Error reading {manifest_path}: {exc}")
                logger.warning("Manifest import error for %s: %s", manifest_path, exc)

        return result

    def _process_manifest(
        self,
        manifest: MergeCandidateManifest,
        manifest_path: Path,
        result: ImportResult,
    ) -> None:
        """Process a single MergeCandidateManifest for import."""
        # Only import candidates (not already promoted)
        from prismatic.merge_candidate_manifest import PromotionState

        if manifest.state not in (
            PromotionState.CANDIDATE,
            PromotionState.REVIEW_REQUIRED,
        ):
            result.skipped_ineligible += 1
            return

        work_id = f"manifest-{manifest.digest()[:16]}"
        existing = self.queue.db.get_job_by_completed_work_id(work_id)
        if existing:
            result.skipped_duplicate += 1
            return

        job_id = self.queue.enqueue_completed_work(
            completed_work_id=work_id,
            task_id=manifest.issue_id,
            repository=manifest.repository,
            base_commit=manifest.base_sha,
            candidate_commit=manifest.candidate_sha,
            changed_paths=list(manifest.changed_paths),
            result_packet_path=str(manifest_path),
        )

        if job_id:
            result.enqueued += 1
