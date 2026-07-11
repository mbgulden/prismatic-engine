"""Completion-proof contract tests for local Prismatic tasks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch


class TestLocalTaskCompletionProof(unittest.TestCase):
    def test_completed_status_requires_artifact_or_marker(self):
        from prismatic.local_tasks import LocalTaskQueue

        with tempfile.TemporaryDirectory() as tmp:
            queue = LocalTaskQueue(Path(tmp) / "event_router.db")
            task = queue.create(title="Write RESULT.md", agent="agy", workspace=".")

            with self.assertRaisesRegex(ValueError, "require"):
                queue.update_status(task.id, "completed")

            self.assertEqual(queue.get(task.id).status, "queued")

    def test_complete_stores_artifact_path_as_proof(self):
        from prismatic.local_tasks import LocalTaskQueue

        with tempfile.TemporaryDirectory() as tmp:
            queue = LocalTaskQueue(Path(tmp) / "event_router.db")
            task = queue.create(title="Write RESULT.md", agent="agy", workspace=".")

            completed = queue.complete(
                task.id,
                artifact_path=Path(tmp) / "RESULT.md",
                metadata_patch={"commit": "abc123"},
            )

            self.assertEqual(completed.status, "completed")
            self.assertEqual(completed.metadata["artifact_path"], str(Path(tmp) / "RESULT.md"))
            self.assertEqual(completed.metadata["commit"], "abc123")

    def test_dispatcher_records_completed_launcher_artifact(self):
        from prismatic import dispatcher
        from prismatic.local_tasks import LocalTaskQueue

        with tempfile.TemporaryDirectory() as tmp:
            queue = LocalTaskQueue(Path(tmp) / "event_router.db")
            task = queue.create(title="Local AGY task", agent="agy", workspace=".")

            class Dedup:
                def __init__(self):
                    self.processed = set()

                def is_processed(self, issue_id, label, cycle_id):
                    return (issue_id, label) in self.processed

                def mark_processed(self, issue_id, label, cycle_id):
                    self.processed.add((issue_id, label))

            def fake_launcher(issue_id, title="", **kwargs):
                return {
                    "status": "completed",
                    "artifact_path": str(Path(tmp) / "RESULT.md"),
                    "summary": "done",
                }

            with patch.dict(dispatcher.AGENT_LAUNCHERS, {"agy": fake_launcher}, clear=True):
                with patch.dict(dispatcher.AGENT_CONFIG, {"agy": {"mode": "launch"}}, clear=True):
                    with patch("prismatic.dispatcher.setup_pipeline_issues", return_value=[]):
                        with patch("prismatic.dispatcher.get_issues_with_label", return_value=[]):
                            counts = dispatcher.dispatch_once(cast(Any, Dedup()), local_task_queue=queue)

            completed = queue.get(task.id)
            self.assertEqual(counts["local_dispatched"], 1)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(completed.metadata["artifact_path"], str(Path(tmp) / "RESULT.md"))
            self.assertEqual(completed.metadata["summary"], "done")

    def test_dispatcher_refuses_completed_launcher_without_proof(self):
        from prismatic import dispatcher
        from prismatic.local_tasks import LocalTaskQueue

        with tempfile.TemporaryDirectory() as tmp:
            queue = LocalTaskQueue(Path(tmp) / "event_router.db")
            task = queue.create(title="Local AGY task", agent="agy", workspace=".")

            class Dedup:
                def __init__(self):
                    self.processed = set()

                def is_processed(self, issue_id, label, cycle_id):
                    return False

                def mark_processed(self, issue_id, label, cycle_id):
                    self.processed.add((issue_id, label))

            def fake_launcher(issue_id, title="", **kwargs):
                return {"status": "completed", "summary": "silent success is not proof"}

            with patch.dict(dispatcher.AGENT_LAUNCHERS, {"agy": fake_launcher}, clear=True):
                with patch.dict(dispatcher.AGENT_CONFIG, {"agy": {"mode": "launch"}}, clear=True):
                    with patch("prismatic.dispatcher.setup_pipeline_issues", return_value=[]):
                        with patch("prismatic.dispatcher.get_issues_with_label", return_value=[]):
                            counts = dispatcher.dispatch_once(cast(Any, Dedup()), local_task_queue=queue)

            failed = queue.get(task.id)
            self.assertEqual(counts["local_dispatched"], 0)
            self.assertEqual(failed.status, "failed")
            self.assertIn("completion", failed.metadata["error"])


if __name__ == "__main__":
    unittest.main()
