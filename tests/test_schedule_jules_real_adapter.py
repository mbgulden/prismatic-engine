"""
tests/test_schedule_jules_real_adapter.py
==========================================

Verifies the real ``get_jules_schedules`` adapter added in GRO-1970. The
adapter has a three-tier read path:

1. **Local config**: ``~/.config/jules/schedules.json`` if it exists.
2. **Remote API**: if ``JULES_API_KEY`` is set, call
   ``https://jules.google.com/api/v1/schedules``.
3. **Fallback**: canonical mock with
   ``metadata.adapter == "fallback-mock"``.

Tests use ``tmp_path``, ``monkeypatch`` (via patch.dict), and a fake
``urllib.request`` to avoid real network calls.
"""

import json
import logging
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from prismatic.schedules import get_jules_schedules, OWNER_JULES


class TestJulesAdapterLocalPath(unittest.TestCase):
    """Tier 1: local config."""

    def setUp(self):
        self._old_file = os.environ.pop("JULES_SCHEDULES_FILE", None)

    def tearDown(self):
        if self._old_file is not None:
            os.environ["JULES_SCHEDULES_FILE"] = self._old_file

    def test_local_path_with_one_entry(self):
        with patch("prismatic.schedules.Path.home", return_value=Path("/tmp/fake-jules-home")):
            fake_dir = Path("/tmp/fake-jules-home/.config/jules")
            fake_dir.mkdir(parents=True, exist_ok=True)
            (fake_dir / "schedules.json").write_text(json.dumps([{
                "id": "dep-scan",
                "name": "Jules Dep Scan",
                "schedule": "0 0 * * 0",
                "enabled": True,
                "persona": "security-reviewer",
            }]))
            try:
                with patch.dict(os.environ, {"JULES_SCHEDULES_FILE": str(fake_dir / "schedules.json")}, clear=False):
                    records = get_jules_schedules()
            finally:
                (fake_dir / "schedules.json").unlink()
                fake_dir.rmdir()
                fake_dir.parent.rmdir()
                Path("/tmp/fake-jules-home").rmdir()

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.id, "jules:schedule:dep-scan")
        self.assertEqual(rec.owner, OWNER_JULES)
        self.assertEqual(rec.metadata.get("adapter"), "live")
        self.assertIn("source_file", rec.metadata)


class TestJulesAdapterRemotePath(unittest.TestCase):
    """Tier 2: remote API when JULES_API_KEY is set."""

    def setUp(self):
        self._old_file = os.environ.pop("JULES_SCHEDULES_FILE", None)
        self._old_key = os.environ.pop("JULES_API_KEY", None)

    def tearDown(self):
        if self._old_file is not None:
            os.environ["JULES_SCHEDULES_FILE"] = self._old_file
        if self._old_key is not None:
            os.environ["JULES_API_KEY"] = self._old_key

    def test_remote_path_with_valid_response(self):
        # Make sure the local file does NOT exist
        with patch("pathlib.Path.exists", return_value=False):
            with patch.dict(os.environ, {"JULES_API_KEY": "test-jules-key"}, clear=False):
                fake_response = MagicMock()
                fake_response.read.return_value = json.dumps([
                    {"id": "rem-1", "name": "Remote 1", "schedule": "0 12 * * *"}
                ]).encode("utf-8")
                fake_response.__enter__ = lambda self: self
                fake_response.__exit__ = lambda self, *args: False
                with patch("urllib.request.urlopen", return_value=fake_response):
                    records = get_jules_schedules()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "jules:schedule:rem-1")
        self.assertEqual(records[0].metadata.get("adapter"), "live")
        self.assertEqual(
            records[0].metadata.get("source"),
            "jules.google.com/api/v1/schedules",
        )

    def test_remote_path_api_failure_falls_back(self):
        with patch("pathlib.Path.exists", return_value=False):
            with patch.dict(os.environ, {"JULES_API_KEY": "test-jules-key"}, clear=False):
                with patch("urllib.request.urlopen", side_effect=OSError("network down")):
                    with self.assertLogs("prismatic.schedules", level="WARNING") as cm:
                        records = get_jules_schedules()

        # Falls back to the canonical mock
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].metadata.get("adapter"), "fallback-mock")
        # A warning was logged
        self.assertTrue(any("API call failed" in msg for msg in cm.output))


class TestJulesAdapterFallbackPath(unittest.TestCase):
    """Tier 3: fallback when nothing else is available."""

    def setUp(self):
        self._old_file = os.environ.pop("JULES_SCHEDULES_FILE", None)
        self._old_key = os.environ.pop("JULES_API_KEY", None)

    def tearDown(self):
        if self._old_file is not None:
            os.environ["JULES_SCHEDULES_FILE"] = self._old_file
        if self._old_key is not None:
            os.environ["JULES_API_KEY"] = self._old_key

    def test_fallback_when_no_file_no_key(self):
        with patch("pathlib.Path.exists", return_value=False):
            # Make sure JULES_API_KEY is not set
            os.environ.pop("JULES_API_KEY", None)
            with self.assertLogs("prismatic.schedules", level="WARNING") as cm:
                records = get_jules_schedules()

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.metadata.get("adapter"), "fallback-mock")
        self.assertIn("reason", rec.metadata)
        self.assertTrue(any("fallback" in msg for msg in cm.output))


if __name__ == "__main__":
    unittest.main()
