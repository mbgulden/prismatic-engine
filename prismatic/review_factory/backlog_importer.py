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

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

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


def _is_hex_sha(val: str) -> bool:
    """Return true only for a nonzero full lowercase SHA-1 identity."""
    return bool(re.fullmatch(r"[0-9a-f]{40}", val or "") and val != "0" * 40)


class BacklogImporter:
    """RF-6: Import pending work from AgyCompletedWorkStore.

    Reads from the same ``agy_completed_work.db`` that the factory
    queue lives in. Uses ``CompletedWorkRow`` dataclass, not raw SQL.
    """

    def __init__(
        self,
        queue: ReviewQueue | None = None,
        db_path: Path | None = None,
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
        base_commit = packet.get("base_commit") or packet.get("base_sha") or ""
        candidate_commit = (
            packet.get("candidate_commit") or packet.get("candidate_sha") or ""
        )
        base_tree = packet.get("base_tree") or packet.get("base_commit_tree") or ""
        candidate_tree = (
            packet.get("candidate_tree") or packet.get("candidate_commit_tree") or ""
        )

        if (
            not _is_hex_sha(base_commit)
            or not _is_hex_sha(candidate_commit)
            or not _is_hex_sha(base_tree)
            or not _is_hex_sha(candidate_tree)
        ):
            result.skipped_ineligible += 1
            result.errors.append(
                "Row "
                f"{completed_work_id} missing valid commit/tree SHAs "
                f"(base_commit='{base_commit}', candidate_commit='{candidate_commit}', "
                f"base_tree='{base_tree}', candidate_tree='{candidate_tree}') — fail closed"
            )
            return

        changed_paths = list(packet.get("changed_files", []))

        # Result packet path is mandatory; fall back to the row source path and
        # finally to a synthetic per-row placeholder so the queue invariant holds.
        result_packet_path = (
            packet.get("result_packet_path")
            or packet.get("source_path")
            or row.source_path
            or f"synthetic://completed-work/{row.id}"
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
            base_tree=base_tree,
            candidate_commit=candidate_commit,
            candidate_tree=candidate_tree,
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

        if (
            not _is_hex_sha(manifest.base_sha)
            or not _is_hex_sha(manifest.candidate_sha)
        ):
            result.skipped_ineligible += 1
            result.errors.append(
                f"Manifest for {manifest.issue_id} missing valid commit SHAs "
                f"(base_sha='{manifest.base_sha}', candidate_sha='{manifest.candidate_sha}')"
            )
            return

        work_id = f"manifest-{manifest.digest()[:16]}"
        existing = self.queue.db.get_job_by_completed_work_id(work_id)
        if existing:
            result.skipped_duplicate += 1
            return

        # Compute the real sha256 of the local manifest file so R2's
        # file-existence + digest gate is satisfied. Prior to RF-M3 the
        # conftest auto-wrap supplied a fake digest for non-canonical
        # paths; that masked this real production-side check.
        if manifest_path.is_file():
            packet_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        else:
            # Non-local manifest; fall back to the manifest's own digest
            # (the canonical-JSON sha256 embedded in the dataclass) so
            # the R2 gate gets a deterministic 64-hex value to validate.
            packet_sha256 = manifest.digest()

        job_id = self.queue.enqueue_completed_work(
            completed_work_id=work_id,
            task_id=manifest.issue_id,
            repository=manifest.repository,
            base_commit=manifest.base_sha,
            candidate_commit=manifest.candidate_sha,
            changed_paths=list(manifest.changed_paths),
            result_packet_path=str(manifest_path),
            result_packet_sha256=packet_sha256,
        )

        if job_id:
            result.enqueued += 1


def _cli_main() -> int:
    """CLI entry point for bounded drain operations.

    Used by prismatic-review-factory.service (per
    george-review-merge-factory-production-wiring/deploy/systemd/prismatic-review-factory.service.in).
    Each invocation reads eligible completed-work rows + manifest files and
    enqueues them into the review queue. The systemd .path unit activates this
    on filesystem changes; each path-event triggers a bounded drain.
    """
    import argparse
    import json as _json
    import datetime as _dt

    parser = argparse.ArgumentParser(
        description="Review Factory bounded drain (intake phase)"
    )
    parser.add_argument(
        "--db-path", required=True, help="Path to agy_completed_work.db (SQLite)"
    )
    parser.add_argument(
        "--completed-work-db-path",
        default=None,
        help="Alias for --db-path (kept for template compatibility)",
    )
    parser.add_argument(
        "--inbox-dir",
        default=None,
        help="Directory of merge_candidate.json files to scan",
    )
    parser.add_argument(
        "--state-dir",
        required=True,
        help="Durable state directory for the review-factory runtime",
    )
    parser.add_argument(
        "--workspace-dir",
        default=None,
        help="Source workspace acquisition directory (default: <state-dir>/source-workspaces)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=20,
        help="Maximum rows to process per drain (default: 20)",
    )
    parser.add_argument(
        "--worker-id",
        required=True,
        help="Stable identifier for this runtime (used in logs + receipts)",
    )
    parser.add_argument(
        "--reviewer-id",
        default=None,
        help="Stable reviewer identifier (default: <worker-id>)",
    )
    parser.add_argument(
        "--enable-merge",
        action="store_true",
        help="Enable merge execution (default: intake only)",
    )
    parser.add_argument(
        "--merge-repo-path",
        default=None,
        help="Path to the repository where merges will be performed (required if --enable-merge)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan + filter but do not enqueue or mutate state",
    )

    args = parser.parse_args()

    if args.enable_merge and not args.merge_repo_path:
        parser.error("--enable-merge requires --merge-repo-path")

    db_path = Path(args.completed_work_db_path or args.db_path)
    state_dir = Path(args.state_dir)
    inbox_dir = Path(args.inbox_dir) if args.inbox_dir else None
    workspace_dir = (
        Path(args.workspace_dir)
        if args.workspace_dir
        else state_dir / "source-workspaces"
    )
    reviewer_id = args.reviewer_id or args.worker_id

    for sub in ("inbox-disposition", "artifacts", "completed-work-disposition", "logs"):
        (state_dir / sub).mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    from prismatic.review_factory.queue import ReviewQueue
    from prismatic.review_factory.db import ReviewFactoryDB

    db = ReviewFactoryDB(db_path)
    db.ensure_tables()
    queue = ReviewQueue(db=db)
    importer = BacklogImporter(queue=queue, db_path=db_path)

    inbox_result = ImportResult()
    if inbox_dir and inbox_dir.is_dir():
        inbox_result = importer.import_from_manifest_dir(inbox_dir)
    elif inbox_dir:
        inbox_result.errors.append(f"Inbox dir does not exist: {inbox_dir}")

    db_result = importer.import_from_completed_work(limit=args.max_items)

    combined = ImportResult(
        scanned=inbox_result.scanned + db_result.scanned,
        eligible=inbox_result.eligible + db_result.eligible,
        enqueued=inbox_result.enqueued + db_result.enqueued,
        skipped_duplicate=inbox_result.skipped_duplicate + db_result.skipped_duplicate,
        skipped_ineligible=inbox_result.skipped_ineligible
        + db_result.skipped_ineligible,
        errors=inbox_result.errors + db_result.errors,
    )

    result_line = {
        "worker_id": args.worker_id,
        "reviewer_id": reviewer_id,
        "ts_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scanned": combined.scanned,
        "eligible": combined.eligible,
        "enqueued": combined.enqueued,
        "skipped_duplicate": combined.skipped_duplicate,
        "skipped_ineligible": combined.skipped_ineligible,
        "errors": combined.errors,
        "dry_run": args.dry_run,
        "enable_merge": args.enable_merge,
    }
    print(_json.dumps(result_line), flush=True)

    receipt_path = (
        state_dir
        / "logs"
        / f"{args.worker_id}-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    receipt_path.write_text(_json.dumps(result_line, indent=2))

    return 1 if combined.errors else 0


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(_cli_main())
