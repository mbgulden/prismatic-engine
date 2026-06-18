"""
tests/test_schedule_agy_real_adapter.py
========================================

Verifies the real ``get_agy_schedules`` adapter added in GRO-1970. The
adapter has three behaviors:

1. **Live path**: when ``~/.gemini/schedules/*.json`` exists with one
   or more valid files, parse them and return real records with
   ``metadata.adapter == "live"``.
2. **Fallback path**: when the directory is empty or missing, return
   the canonical mock records with
   ``metadata.adapter == "fallback-mock"``.
3. **Error path**: when a file exists but cannot be parsed, log a
   warning and continue. If no other live records exist, fall back.

Tests use ``tmp_path`` and ``monkeypatch`` to avoid touching the real
``~/.gemini/schedules/`` directory.
"""

import json
import logging
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from prismatic.schedules import get_agy_schedules, OWNER_AGY


class TestAGYAdapterLivePath(unittest.TestCase):
    """When ~/.gemini/schedules/*.json exists, parse it."""

    def setUp(self):
        # Save and clear AGY_SCHEDULES_DIR env var so monkeypatch
        # gives us full control.
        self._old_env = os.environ.pop("AGY_SCHEDULES_DIR", None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["AGY_SCHEDULES_DIR"] = self._old_env
        else:
            os.environ.pop("AGY_SCHEDULES_DIR", None)

    def test_live_path_with_one_file(self):
        with patch("prismatic.schedules.Path.home", return_value=Path("/tmp/fake-home")):
            fake_dir = Path("/tmp/fake-home/.gemini/schedules")
            fake_dir.mkdir(parents=True, exist_ok=True)
            (fake_dir / "daily-sync.json").write_text(json.dumps([{
                "id": "daily-sync",
                "name": "AGY Daily Sync",
                "schedule": "0 2 * * *",
                "enabled": True,
                "lane": "agy-review",
            }]))
            try:
                with patch.dict(os.environ, {"AGY_SCHEDULES_DIR": str(fake_dir)}, clear=False):
                    records = get_agy_schedules()
            finally:
                (fake_dir / "daily-sync.json").unlink()
                fake_dir.rmdir()
                fake_dir.parent.rmdir()
                Path("/tmp/fake-home").rmdir()

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.id, "agy:schedule:daily-sync")
        self.assertEqual(rec.name, "AGY Daily Sync")
        self.assertEqual(rec.schedule_expr, "0 2 * * *")
        self.assertTrue(rec.enabled)
        self.assertEqual(rec.owner, OWNER_AGY)
        self.assertEqual(rec.metadata.get("adapter"), "live")
        self.assertIn("source_file", rec.metadata)

    def test_live_path_with_multiple_files(self):
        with patch("prismatic.schedules.Path.home", return_value=Path("/tmp/fake-home2")):
            fake_dir = Path("/tmp/fake-home2/.gemini/schedules")
            fake_dir.mkdir(parents=True, exist_ok=True)
            (fake_dir / "a.json").write_text(json.dumps({"id": "a", "schedule": "* * * * *"}))
            (fake_dir / "b.json").write_text(json.dumps([{"id": "b"}, {"id": "c"}]))
            try:
                with patch.dict(os.environ, {"AGY_SCHEDULES_DIR": str(fake_dir)}, clear=False):
                    records = get_agy_schedules()
            finally:
                for p in (fake_dir / "a.json", fake_dir / "b.json"):
                    p.unlink()
                fake_dir.rmdir()
                fake_dir.parent.rmdir()
                Path("/tmp/fake-home2").rmdir()

        ids = {r.id for r in records}
        self.assertEqual(ids, {"agy:schedule:a", "agy:schedule:b", "agy:schedule:c"})
        # All records should be marked "live"
        for r in records:
            self.assertEqual(r.metadata.get("adapter"), "live")

    def test_live_path_with_malformed_file_continues(self):
        """A malformed JSON file should be skipped, not crash the adapter."""
        with patch("prismatic.schedules.Path.home", return_value=Path("/tmp/fake-home3")):
            fake_dir = Path("/tmp/fake-home3/.gemini/schedules")
            fake_dir.mkdir(parents=True, exist_ok=True)
            (fake_dir / "bad.json").write_text("not valid json {")
            (fake_dir / "good.json").write_text(json.dumps({"id": "good", "schedule": "0 0 * * *"}))
            try:
                with patch.dict(os.environ, {"AGY_SCHEDULES_DIR": str(fake_dir)}, clear=False):
                    with self.assertLogs("prismatic.schedules", level="WARNING") as cm:
                        records = get_agy_schedules()
            finally:
                for p in (fake_dir / "bad.json", fake_dir / "good.json"):
                    p.unlink()
                fake_dir.rmdir()
                fake_dir.parent.rmdir()
                Path("/tmp/fake-home3").rmdir()

        # The good file was parsed, the bad file was skipped.
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "agy:schedule:good")
        # A warning was logged about the bad file
        self.assertTrue(any("bad.json" in msg for msg in cm.output))


class TestAGYAdapterFallbackPath(unittest.TestCase):
    """When the schedules dir is missing, return fallback mock with adapter marker."""

    def setUp(self):
        self._old_env = os.environ.pop("AGY_SCHEDULES_DIR", None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["AGY_SCHEDULES_DIR"] = self._old_env

    def test_fallback_when_dir_missing(self):
        # Point AGY_SCHEDULES_DIR at a path that does not exist
        with patch.dict(os.environ, {"AGY_SCHEDULES_DIR": "/tmp/does-not-exist-agy-schedules"}, clear=False):
            with self.assertLogs("prismatic.schedules", level="WARNING") as cm:
                records = get_agy_schedules()

        # Two fallback records returned
        self.assertEqual(len(records), 2)
        # All records are marked as fallback-mock
        for r in records:
            self.assertEqual(r.metadata.get("adapter"), "fallback-mock")
            self.assertIn("reason", r.metadata)
        # A warning was logged
        self.assertTrue(any("fallback" in msg for msg in cm.output))

    def test_fallback_when_dir_is_empty(self):
        with patch("prismatic.schedules.Path.home", return_value=Path("/tmp/fake-empty")):
            fake_dir = Path("/tmp/fake-empty/.gemini/schedules")
            fake_dir.mkdir(parents=True, exist_ok=True)
            try:
                with patch.dict(os.environ, {"AGY_SCHEDULES_DIR": str(fake_dir)}, clear=False):
                    with self.assertLogs("prismatic.schedules", level="WARNING") as cm:
                        records = get_agy_schedules()
            finally:
                fake_dir.rmdir()
                fake_dir.parent.rmdir()
                Path("/tmp/fake-empty").rmdir()

        self.assertEqual(len(records), 2)
        for r in records:
            self.assertEqual(r.metadata.get("adapter"), "fallback-mock")
        self.assertTrue(any("fallback" in msg for msg in cm.output))


if __name__ == "__main__":
    unittest.main()
