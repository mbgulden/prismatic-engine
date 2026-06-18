"""
tests/test_chat_agy_capability.py
==================================

Verifies the chat.agy capability added in GRO-1969 (additive half of
GRO-1955). The capability has three behaviors in v0.1:

1. **check_status()** — returns True if AGY is reachable (binary on
   PATH, env var set, or OAuth token at any known location), False
   otherwise.
2. **list_sessions()** — returns an empty list in v0.1 (live data
   path is not yet wired; this is by design).
3. **get_session(id)** — returns None in v0.1 (no live data yet).

Also verifies:
- The capability is registered in the engine's capability registry
  as "chat.agy".
- The two gateway endpoints respond correctly: GET /chat/sessions
  returns a JSON list, GET /chat/sessions/<id> returns 404.

Future panes (local AI GPU agents, Hermes agents) will reuse the
``ChatSession`` dataclass from ``prismatic/chat/__init__.py``;
that module is tested implicitly via the gateway response shape.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from prismatic.capabilities.chat_agy import (
    ChatAGYCapability,
    ChatSession,
    _DEFAULT_AGY_OAUTH_PATHS,
)
from prismatic.chat import ChatSession as PkgChatSession
from prismatic.capabilities import registry as default_registry


class TestChatSessionDataclass(unittest.TestCase):
    """The dataclass shape is the contract for every pane."""

    def test_chatsession_to_dict_drops_none(self):
        s = ChatSession(id="abc", agent="agy", status="running", started_at="2026-06-18T00:00:00Z")
        d = s.to_dict()
        self.assertEqual(d["id"], "abc")
        self.assertEqual(d["agent"], "agy")
        self.assertEqual(d["status"], "running")
        self.assertEqual(d["started_at"], "2026-06-18T00:00:00Z")
        # None fields are dropped
        self.assertNotIn("last_event_at", d)
        self.assertNotIn("label", d)

    def test_chatsession_to_dict_keeps_empty_string(self):
        s = ChatSession(id="abc", agent="agy", status="", started_at="")
        d = s.to_dict()
        self.assertEqual(d["status"], "")
        self.assertEqual(d["started_at"], "")

    def test_pkg_chatsession_has_same_shape(self):
        # prismatic.chat.ChatSession and prismatic.capabilities.chat_agy.ChatSession
        # are different classes (separate module paths for ergonomic
        # import). Pane authors should be able to use either; the
        # important thing is that they have the same field names.
        fields_a = {f for f in PkgChatSession.__dataclass_fields__}
        fields_b = {f for f in ChatSession.__dataclass_fields__}
        self.assertEqual(fields_a, fields_b)


class TestChatAGYCapabilityCheckStatus(unittest.TestCase):
    """check_status() reflects whether AGY is reachable."""

    def setUp(self):
        # Clear AGY_PATH so the test starts from a clean state.
        self._old_env = {}
        for k in ("AGY_PATH",):
            if k in os.environ:
                self._old_env[k] = os.environ.pop(k)

    def tearDown(self):
        for k, v in self._old_env.items():
            os.environ[k] = v

    def test_check_status_ok_when_oauth_token_exists(self):
        # Pick a path that exists for the test host
        existing_path = next(
            (p for p in _DEFAULT_AGY_OAUTH_PATHS if p.exists()), None
        )
        if existing_path is None:
            self.skipTest("No AGY OAuth token on this host")
        cap = ChatAGYCapability()
        ok, msg = cap.check_status()
        self.assertTrue(ok, f"Expected ok, got ({ok}, {msg})")
        self.assertIn("OAuth token", msg)

    def test_check_status_fails_when_nothing_present(self):
        with patch.object(Path, "exists", return_value=False):
            cap = ChatAGYCapability(agy_path="/nonexistent/agy/binary")
            ok, msg = cap.check_status()
        self.assertFalse(ok)
        self.assertIn("not reachable", msg)

    def test_check_status_ok_when_agy_path_env_set(self):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.dict(os.environ, {"AGY_PATH": "/fake/agy"}, clear=False):
                cap = ChatAGYCapability()
                ok, msg = cap.check_status()
        self.assertTrue(ok)
        self.assertIn("/fake/agy", msg)

    def test_check_status_explicit_agy_path_argument(self):
        with patch("pathlib.Path.exists", return_value=True):
            cap = ChatAGYCapability(agy_path="/custom/agy")
            ok, msg = cap.check_status()
        self.assertTrue(ok)
        self.assertIn("/custom/agy", msg)


class TestChatAGYCapabilityListAndGet(unittest.TestCase):
    """v0.1 contract: empty list, None on get."""

    def test_list_sessions_returns_empty_list(self):
        cap = ChatAGYCapability()
        self.assertEqual(cap.list_sessions(), [])

    def test_get_session_returns_none(self):
        cap = ChatAGYCapability()
        self.assertIsNone(cap.get_session("any-id"))


class TestChatAGYCapabilityRegistryRegistration(unittest.TestCase):
    """The capability is registered in the engine's registry as 'chat.agy'."""

    def test_chat_agy_registered_in_default_registry(self):
        cap = default_registry.get("chat.agy")
        self.assertIsNotNone(cap, "chat.agy is not registered in the default registry")

    def test_chat_agy_registry_check_runs(self):
        cap = default_registry.get("chat.agy")
        self.assertIsNotNone(cap)
        ok, msg = cap.check_status()
        # ok is True or False depending on the host; we only assert the
        # call returns a tuple and the message is a string.
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)


class TestChatGatewayEndpoints(unittest.TestCase):
    """The gateway endpoints surface the capability read-only."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        # The gateway server module pulls in heavy deps; load it lazily.
        from prismatic.gateway.server import app
        cls.client = TestClient(app)

    def test_list_chat_sessions_returns_json_list(self):
        r = self.client.get("/chat/sessions")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)
        # v0.1 contract: empty list
        self.assertEqual(r.json(), [])

    def test_get_chat_session_returns_404_for_any_id(self):
        r = self.client.get("/chat/sessions/does-not-exist")
        self.assertEqual(r.status_code, 404)
        body = r.json()
        self.assertEqual(body.get("detail", {}).get("error"), "session_not_found")
        self.assertIn("v0.1", body.get("detail", {}).get("reason", ""))


if __name__ == "__main__":
    unittest.main()
