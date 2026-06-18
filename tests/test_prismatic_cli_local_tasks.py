"""
Tests for the unified ``prismatic`` CLI and local task queue.

These encode the first-user journey slice: a bare-metal user can run
``prismatic status`` and create a local task without Linear configured.
"""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestUnifiedPrismaticCli(unittest.TestCase):
    def test_status_alias_delegates_to_doctor_cli(self):
        from prismatic import cli

        with patch("prismatic.cli.doctor_cli_run", return_value=0) as fake_doctor:
            rc = cli.run(["status", "--provider", "github"])

        self.assertEqual(rc, 0)
        fake_doctor.assert_called_once()
        args = fake_doctor.call_args.args[0]
        self.assertEqual(args.provider, "github")

    def test_task_create_persists_local_task(self):
        from prismatic import cli

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "event_router.db"
            with patch("builtins.print"):
                rc = cli.run([
                        "task",
                        "create",
                        "Audit this repository for linting errors",
                        "--agent",
                        "agy",
                        "--workspace",
                        ".",
                        "--db-path",
                        str(db_path),
                    ])

            self.assertEqual(rc, 0)
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT agent, title, status FROM local_tasks"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("agy", "Audit this repository for linting errors", "queued"))


class TestLocalTaskQueue(unittest.TestCase):
    def test_create_and_fetch_queued_local_task(self):
        from prismatic.local_tasks import LocalTaskQueue

        with tempfile.TemporaryDirectory() as tmp:
            tmp_db = Path(tmp) / "event_router.db"
            queue = LocalTaskQueue(tmp_db)
            task = queue.create(
                title="Audit this repository for linting errors",
                agent="agy",
                workspace=".",
            )

            self.assertTrue(task.id.startswith("local-"))
            self.assertEqual(task.status, "queued")

            queued = queue.list_queued(agent="agy")
            self.assertEqual([item.id for item in queued], [task.id])
            self.assertEqual(queued[0].title, "Audit this repository for linting errors")

            conn = sqlite3.connect(tmp_db)
            try:
                row = conn.execute(
                    "SELECT id, agent, title, workspace, status FROM local_tasks"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, (task.id, "agy", task.title, str(Path(".").resolve()), "queued"))

    def test_dispatch_once_checks_local_tasks_before_linear(self):
        from prismatic import dispatcher
        from prismatic.local_tasks import LocalTaskQueue

        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        tmp_db = Path(tmp_dir.name) / "event_router.db"
        queue = LocalTaskQueue(tmp_db)
        task = queue.create(title="Local AGY task", agent="agy", workspace=".")

        launched = []

        def fake_launcher(issue_id, title="", **kwargs):
            launched.append((issue_id, title, kwargs))
            return True

        class Dedup:
            def __init__(self):
                self.processed = set()

            def is_processed(self, issue_id, label, cycle_id):
                return (issue_id, label) in self.processed

            def mark_processed(self, issue_id, label, cycle_id):
                self.processed.add((issue_id, label))

        with patch.dict(dispatcher.AGENT_LAUNCHERS, {"agy": fake_launcher}, clear=True):
            with patch.dict(dispatcher.AGENT_CONFIG, {"agy": {"mode": "launch"}}, clear=True):
                with patch("prismatic.dispatcher.setup_pipeline_issues", return_value=[]):
                    with patch("prismatic.dispatcher.get_issues_with_label", return_value=[]) as fake_linear:
                        counts = dispatcher.dispatch_once(Dedup(), local_task_queue=queue)

        self.assertEqual(counts["local_dispatched"], 1)
        self.assertEqual(launched[0][0], task.id)
        self.assertEqual(launched[0][1], "Local AGY task")
        self.assertEqual(launched[0][2]["workspace"], str(Path(".").resolve()))
        self.assertEqual(queue.get(task.id).status, "dispatched")
        fake_linear.assert_called()


if __name__ == "__main__":
    unittest.main()
