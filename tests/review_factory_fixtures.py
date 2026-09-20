"""Test-only fixture helpers for the Review Factory.

RF-R2 mandates: ``Replace production bypass use in tests with explicit
test-only fixtures.``

These helpers wrap ``ReviewQueue.enqueue_completed_work`` with valid
non-empty ``changed_paths`` and a real ``result_packet_sha256`` so tests
can exercise the queue without going through the production intake
boundary.

Import in tests:
    from tests.review_factory_fixtures import make_enqueue

Usage:
    queue = ReviewQueue(db=tmp_db)
    job_id = make_enqueue(queue, paths=["prismatic/foo.py"], task_id="GRO-TEST", tmp_path=tmp_path)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from prismatic.review_factory.queue import ReviewQueue


def make_bundle(tmp_path: Any, content: dict | None = None) -> tuple[str, str]:
    """Create a real bundle file in tmp_path and return (path, sha256).

    The bundle has real content + matching digest so the RF-R2 intake
    validator accepts it.
    """
    bundle_dir = Path(str(tmp_path)) if not isinstance(tmp_path, Path) else tmp_path
    bundle_dir.mkdir(parents=True, exist_ok=True)
    payload = content or {"_test": True}
    bundle_path = bundle_dir / "merge_candidate.json"
    bundle_path.write_text(json.dumps(payload))
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    return str(bundle_path), digest


def make_enqueue(
    queue: ReviewQueue,
    *,
    paths: list[str],
    task_id: str,
    tmp_path: Any,
    completed_work_id: str | None = None,
    repository: str = "mbgulden/prismatic-engine",
    base_commit: str | None = None,
    candidate_commit: str | None = None,
) -> str:
    """Test-only wrapper around ``ReviewQueue.enqueue_completed_work``.

    Writes a real bundle in ``tmp_path`` and supplies the matching sha256.
    Every caller is required to provide real ``paths`` (no empty list).
    """
    if not paths:
        raise ValueError("make_enqueue: paths must be non-empty")
    bundle, digest = make_bundle(tmp_path, {"task_id": task_id, "paths": paths})
    return queue.enqueue_completed_work(
        completed_work_id=completed_work_id or f"test-{os.urandom(4).hex()}",
        task_id=task_id,
        repository=repository,
        base_commit=base_commit or ("0" * 40),
        candidate_commit=candidate_commit or ("0" * 40),
        changed_paths=list(paths),
        result_packet_path=bundle,
        result_packet_sha256=digest,
    )
