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

CLI (systemd entry point — see scripts/prismatic-review-factory.service)
-----------------------------------------------------------------------
    python3 -m prismatic.review_factory.backlog_importer \
        --db-path /home/ubuntu/.prismatic/state/agy_completed_work.db \
        --completed-work-db-path /home/ubuntu/.prismatic/state/agy_completed_work.db \
        --inbox-dir /home/ubuntu/.prismatic/inbox \
        --state-dir /home/ubuntu/.prismatic/state \
        --workspace-dir /home/ubuntu/.prismatic/state/source-workspaces \
        --max-items 20 --worker-id review-factory-runtime
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
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

    def ingest_completed_work_row(self, row: CompletedWorkRow) -> bool:
        """Enqueue a single freshly-ingested completed-work row.

        Used by ``AgyCompletedWorkStore.ingest()`` so completed work flows
        into the review queue at closeout. Returns True when a new review
        job was enqueued.
        """
        result = ImportResult()
        try:
            self._process_row(row, result)
        except Exception as exc:
            logger.warning("Import error for %s: %s", row.id, exc)
        return result.enqueued == 1

    def _enqueue_idempotent(self, result: ImportResult, **kwargs) -> None:
        """Enqueue, tolerating a lost check-then-act race.

        Two concurrent drains can both pass the get_job_by_completed_work_id
        check; the loser's INSERT then hits the completed_work_id UNIQUE
        constraint. That is a duplicate, not an error: count it as
        skipped_duplicate instead of recording an error.
        """
        try:
            job_id = self.queue.enqueue_completed_work(**kwargs)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed: review_jobs.completed_work_id" in str(exc):
                logger.info(
                    "Concurrent drain race on %s; counting as duplicate",
                    kwargs.get("completed_work_id"),
                )
                result.skipped_duplicate += 1
                return
            raise
        if job_id:
            result.enqueued += 1

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

        self._enqueue_idempotent(
            result,
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

        self._enqueue_idempotent(
            result,
            completed_work_id=work_id,
            task_id=manifest.issue_id,
            repository=manifest.repository,
            base_commit=manifest.base_sha,
            candidate_commit=manifest.candidate_sha,
            changed_paths=list(manifest.changed_paths),
            result_packet_path=str(manifest_path),
        )


# ── CLI entry point ──
# R-1 follow-up (2026-09-27): the systemd unit
# `scripts/prismatic-review-factory.service` runs this module as
# `python3 -m prismatic.review_factory.backlog_importer ...`. The module
# previously defined no main() and no `__main__` guard, so `-m` merely
# imported it and exited 0 with every CLI flag silently ignored — the
# drain never ran. This entry point mirrors the learn_loop.py codebase
# convention (main(argv=None) -> int) and wires the REAL drain logic:
# eligible completed-work rows are imported into the review queue.


def _build_parser() -> argparse.ArgumentParser:
    """CLI for the systemd `-m` entry point — the unit's exact flags."""
    parser = argparse.ArgumentParser(
        prog="prismatic.review_factory.backlog_importer",
        description=(
            "Review Factory backlog importer: drain eligible completed-work "
            "rows into the review queue (bounded one-shot). Invoked by "
            "prismatic-review-factory.service via `python3 -m`."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Review Factory queue DB path (the queue tables live in the "
            "same file as the completed-work store; defaults to the "
            "standard state-dir resolution)."
        ),
    )
    parser.add_argument(
        "--completed-work-db-path",
        default=None,
        help=(
            "Completed-work store DB path (defaults to the standard "
            "state-dir resolution)."
        ),
    )
    parser.add_argument(
        "--inbox-dir",
        default=None,
        help=(
            "Inbox directory (accepted for unit CLI compatibility; the "
            "drain reads the completed-work DB, not the inbox)."
        ),
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "State directory; exported as PRISMATIC_STATE_DIR for default "
            "path resolution when set."
        ),
    )
    parser.add_argument(
        "--workspace-dir",
        default=None,
        help=(
            "Source-workspaces directory (accepted for unit CLI "
            "compatibility; reserved for future manifest-dir scanning)."
        ),
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=20,
        help="Maximum completed-work rows to scan per run (default: 20, "
        "the bound the systemd unit passes).",
    )
    parser.add_argument(
        "--worker-id",
        default="review-factory-runtime",
        help="Worker identity recorded in the drain summary (journald).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Run the bounded one-shot backlog drain.

    This is the entry point the systemd unit invokes via
    ``python3 -m prismatic.review_factory.backlog_importer``. It runs the
    real drain: eligible completed-work rows are imported into the review
    queue, and a one-line summary is printed (captured by journald as the
    drain's success signal).

    Exit code 0 = the drain ran (zero rows is a legitimate outcome).
    Exit code 1 = the drain failed unexpectedly (exception), so the unit
    reports failure instead of a silent success.
    """
    from prismatic.review_factory.db import ReviewFactoryDB

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.max_items < 0:
        parser.error("--max-items must be >= 0")

    if args.state_dir:
        # The unit passes --state-dir and also sets PRISMATIC_STATE_DIR in
        # the environment; make the flag authoritative for default-path
        # resolution below without clobbering an explicitly set env var.
        os.environ.setdefault("PRISMATIC_STATE_DIR", args.state_dir)

    queue_db_path = Path(args.db_path) if args.db_path else None
    completed_work_db_path = (
        Path(args.completed_work_db_path) if args.completed_work_db_path else None
    )

    db = ReviewFactoryDB(db_path=queue_db_path)
    queue = ReviewQueue(db=db)
    try:
        importer = BacklogImporter(queue=queue, db_path=completed_work_db_path)
        result = importer.import_from_completed_work(limit=args.max_items)
    except Exception as exc:
        logger.exception("backlog drain failed unexpectedly")
        print(
            f"backlog_importer drain FAILED worker_id={args.worker_id}: {exc}",
            flush=True,
        )
        return 1
    finally:
        queue.close()

    summary = (
        "backlog_importer drain complete "
        f"worker_id={args.worker_id} "
        f"scanned={result.scanned} eligible={result.eligible} "
        f"enqueued={result.enqueued} "
        f"skipped_duplicate={result.skipped_duplicate} "
        f"skipped_ineligible={result.skipped_ineligible} "
        f"errors={len(result.errors)}"
    )
    logger.info("%s", summary)
    print(summary, flush=True)
    for err in result.errors:
        print(f"backlog_importer drain error: {err}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
