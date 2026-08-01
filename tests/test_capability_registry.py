import unittest
from unittest.mock import patch
import os

from prismatic.capabilities.registry import (
    registry,
    check_linear,
    check_vcs_github,
    check_agy,
    check_jules,
    check_telegram,
    check_schedule,
    check_artifact,
)


class TestCapabilityRegistry(unittest.TestCase):
    """Test suite for capability registry structure and default capability checks."""

    def test_registry_contains_default_capabilities(self):
        expected_caps = {"linear", "vcs.github", "agy", "jules", "telegram", "schedule", "artifact"}
        registered_names = {cap.name for cap in registry.list_all()}
        self.assertTrue(expected_caps.issubset(registered_names))

    @patch.dict(os.environ, {"LINEAR_API_KEY": "test-key"})
    def test_check_linear_present(self):
        ok, msg = check_linear()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {}, clear=True)
    def test_check_linear_missing(self):
        ok, msg = check_linear()
        self.assertFalse(ok)
        self.assertIn("missing", msg)

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    def test_check_github_present_github_token(self):
        ok, msg = check_vcs_github()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {"GH_TOKEN": "test-token"})
    def test_check_github_present_gh_token(self):
        ok, msg = check_vcs_github()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {"PRISMATIC_GITHUB_TOKEN": "test-token"})
    def test_check_github_present_prismatic_github_token(self):
        ok, msg = check_vcs_github()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {}, clear=True)
    def test_check_github_missing(self):
        ok, msg = check_vcs_github()
        self.assertFalse(ok)
        self.assertIn("missing", msg)

    @patch.dict(os.environ, {"AGY_TOKEN": "test-token"})
    def test_check_agy_present(self):
        ok, msg = check_agy()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {}, clear=True)
    def test_check_agy_missing(self):
        ok, msg = check_agy()
        self.assertFalse(ok)
        self.assertIn("missing", msg)

    def test_check_jules(self):
        ok, msg = check_jules()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"})
    def test_check_telegram_present_bot_token(self):
        ok, msg = check_telegram()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {"PRISMATIC_TELEGRAM_BOT_TOKEN": "test-token"})
    def test_check_telegram_present_prismatic_token(self):
        ok, msg = check_telegram()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    @patch.dict(os.environ, {}, clear=True)
    def test_check_telegram_missing(self):
        ok, msg = check_telegram()
        self.assertFalse(ok)
        self.assertIn("missing", msg)

    def test_check_schedule(self):
        ok, msg = check_schedule()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    def test_check_artifact(self):
        ok, msg = check_artifact()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
