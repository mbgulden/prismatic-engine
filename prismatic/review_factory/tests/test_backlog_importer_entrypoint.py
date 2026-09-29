"""Fail-first test for the review-factory systemd entry point.

The unit ``scripts/prismatic-review-factory.service`` runs::

    python3 -m prismatic.review_factory.backlog_importer --db-path ... \
        --completed-work-db-path ... --inbox-dir ... --state-dir ... \
        --workspace-dir ... --max-items 20 --worker-id review-factory-runtime

Before the R-1 follow-up the module defined no ``main()`` and no
``__main__`` guard, so ``-m`` merely imported it: exit 0, every CLI flag
silently ignored, the drain never ran. This test invokes the entry point
exactly the way the unit does (subprocess, ``-m``, the unit's flags)
against a scratch DB holding one eligible completed-work row, and asserts
the drain actually executes:

- exit code 0,
- the drain summary line on stdout (the journald success signal),
- the row was enqueued in the review queue (queue-row count change).

Pre-fix this test fails (exit 0, no output, no rows); post-fix it passes.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE = "prismatic.review_factory.backlog_importer"


def _hex40(seed: str) -> str:
    """A nonzero 40-char lowercase hex SHA, as the importer requires."""
    digest = hashlib.sha256(seed.encode()).hexdigest()[:40]
    assert digest != "0" * 40
    return digest


def _eligible_packet() -> dict:
    """A packet the completed-work gate classifies merge_ready/eligible.

    Mirrors the fixture in test_backlog_importer.py, extended so the gate
    reaches ``merge_ready`` (lane scope, proof contract) and the importer
    passes its SHA fail-closed checks.
    """
    return {
        "issue_identifier": "GRO-ENTRYPOINT-TEST",
        "agent": "agy",
        "source_branch": "feature/entrypoint-test",
        "source_path": str(Path.home() / "entrypoint-test-source"),
        "base_branch": "main",
        "repository": "mbgulden/prismatic-engine",
        "changed_files": ["docs/readme.md"],
        "result_summary": "Entrypoint drain test completed",
        "base_commit": _hex40("entrypoint-base-commit"),
        "candidate_commit": _hex40("entrypoint-candidate-commit"),
        "base_tree": _hex40("entrypoint-base-tree"),
        "candidate_tree": _hex40("entrypoint-candidate-tree"),
        "lane_scope": {
            "allowed_paths": ["docs/"],
            "touched_paths": ["docs/readme.md"],
        },
        "proof": {
            "command": "python3 -m pytest -q prismatic/review_factory/tests/test_backlog_importer_entrypoint.py",
            "result": "PASS",
            "log": "/tmp/entrypoint-test-proof.log",
            "scope": "entrypoint drain test",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "non_claims": ["production_deployed", "auto_merge"],
            "marker": "ENTRYPOINT_TEST_PROOF_OK",
        },
    }


def _scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Keep ambient PRISMATIC_* settings from leaking into the drain run."""
    for key in (
        "PRISMATIC_AGY_COMPLETED_WORK_DB",
        "PRISMATIC_REVIEW_REPO_DIR",
    ):
        env.pop(key, None)
    return env


@pytest.mark.skipif(
    os.environ.get("PRISMATIC_SKIP_ENTRYPOINT_TEST") == "1",
    reason="entrypoint subprocess test disabled via PRISMATIC_SKIP_ENTRYPOINT_TEST",
)
def test_entrypoint_drains_completed_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismatic.agy_completed_work import AgyCompletedWorkStore
    from prismatic.review_factory.db import ReviewFactoryDB
    from prismatic.review_factory.queue import ReviewQueue

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Isolate the store's best-effort review-factory enqueue side effect.
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))

    db_path = tmp_path / "agy_completed_work.db"
    store = AgyCompletedWorkStore(db_path=db_path)
    row = store.ingest(_eligible_packet())

    # Precondition: the fixture row must actually be drain-eligible, or this
    # test would pass vacuously on an empty drain.
    assert row.integration_classification == "pass_ready_for_review", (
        f"fixture packet classified {row.integration_classification!r}, "
        "expected 'pass_ready_for_review'"
    )
    assert row.eligible_for_merge, "fixture packet must be eligible_for_merge"

    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    workspace_dir = tmp_path / "workspaces"
    workspace_dir.mkdir()

    env = _scrub_env(dict(os.environ))
    env["PRISMATIC_STATE_DIR"] = str(state_dir)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            MODULE,
            "--db-path",
            str(db_path),
            "--completed-work-db-path",
            str(db_path),
            "--inbox-dir",
            str(inbox_dir),
            "--state-dir",
            str(state_dir),
            "--workspace-dir",
            str(workspace_dir),
            "--max-items",
            "20",
            "--worker-id",
            "test-entrypoint",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )

    assert proc.returncode == 0, (
        f"entry point exited {proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    # The drain's journald success signal — absent pre-fix (silent no-op).
    assert "drain complete" in proc.stdout, (
        "entry point printed no drain summary; the drain did not execute\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "enqueued=1" in proc.stdout, (
        "drain ran but did not enqueue the eligible row\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    # Queue-row count change: the completed-work row must now be a review job.
    queue = ReviewQueue(db=ReviewFactoryDB(db_path=db_path))
    try:
        job = queue.db.get_job_by_completed_work_id(row.id)
    finally:
        queue.close()
    assert job is not None, (
        f"completed-work row {row.id} was not enqueued by the entry point"
    )
