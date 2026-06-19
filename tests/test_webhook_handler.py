"""
tests/test_webhook_handler.py
=============================

Verifies that prismatic/gateway/server.py::linear_webhook:

  1. Validates HMAC signature when PRISMATIC_LINEAR_WEBHOOK_SECRET is set.
  2. Rejects requests with bad/missing signature (401).
  3. Dispatches directly when event has agent:* label (event-driven path).
  4. Falls back to SQLite queue when dispatch fails or labels don't match.
  5. Rejects malformed JSON (400).

GRO-2047 / GRO-2048 / GRO-2050 verification.
"""

import unittest
import os
import sys
import json
import hmac
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Mock external deps before importing
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()


def _sign(body: bytes, secret: str) -> str:
    """Compute Linear-Signature header value (HMAC-SHA256 hex)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _make_event(identifier="GRO-2051", labels=None, action="update", event_type="Issue"):
    """Build a synthetic Linear webhook event."""
    return {
        "type": event_type,
        "action": action,
        "data": {
            "id": f"uuid-{identifier}",
            "identifier": identifier,
            "title": f"Test {identifier}",
            "labels": [{"name": n} for n in (labels or [])],
        },
    }


class TestLinearWebhook(unittest.TestCase):
    def setUp(self):
        # Use a temp dir for state so we don't pollute the real prismatic_state
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        self.env_patches = [
            patch.dict(
                os.environ,
                {
                    "PRISMATIC_LINEAR_WEBHOOK_SECRET": "test_secret_xyz",
                    "PRISMATIC_STATE_DIR": str(self.state_dir),
                },
            )
        ]
        for p in self.env_patches:
            p.start()

        # Mock the dispatcher.dispatch_once so we don't hit Linear API
        self.dispatch_patcher = patch("prismatic.dispatcher.dispatch_once")
        self.mock_dispatch_once = self.dispatch_patcher.start()
        self.mock_dispatch_once.return_value = {
            "dispatched": 1,
            "pipeline_setup": 0,
            "stale_killed": 0,
            "errors": 0,
        }

        # Mock EventRouterDedup
        self.dedup_patcher = patch("prismatic.dispatcher.EventRouterDedup")
        self.mock_dedup_cls = self.dedup_patcher.start()
        self.mock_dedup_cls.return_value = MagicMock()

        # Import after env + mocks are in place
        from fastapi.testclient import TestClient
        from prismatic.gateway.server import app

        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.dispatch_patcher.stop()
        self.dedup_patcher.stop()
        for p in self.env_patches:
            p.stop()
        self._tmp.cleanup()

    def _post(self, event, secret="test_secret_xyz", include_sig=True):
        body = json.dumps(event).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if include_sig:
            headers["Linear-Signature"] = _sign(body, secret)
        return self.client.post("/api/gateway/linear", content=body, headers=headers)

    def test_dispatches_with_agent_label(self):
        """GRO-2048: agent:* label on Issue/update triggers dispatch."""
        resp = self._post(_make_event(labels=["agent:agy", "type:docs"]))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "dispatched")
        self.assertEqual(body["identifier"], "GRO-2051")
        # dispatch_once was invoked
        self.mock_dispatch_once.assert_called_once()

    def test_queues_without_agent_label(self):
        """GRO-2050: events without agent:* labels go to SQLite queue."""
        resp = self._post(_make_event(labels=["type:docs"]))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "queued")
        # dispatch was NOT invoked
        self.mock_dispatch_once.assert_not_called()
        # Queue entry exists
        db = self.state_dir / "linear_webhook_queue.db"
        self.assertTrue(db.exists())
        import sqlite3
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT identifier, event_type FROM linear_webhook_queue"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "GRO-2051")
        self.assertEqual(rows[0][1], "Issue")

    def test_rejects_bad_signature(self):
        """401 when HMAC signature is wrong."""
        resp = self._post(_make_event(labels=["agent:agy"]), secret="wrong_secret")
        self.assertEqual(resp.status_code, 401, resp.text)
        self.assertEqual(resp.json()["status"], "rejected")
        # No dispatch, no queue write
        self.mock_dispatch_once.assert_not_called()

    def test_rejects_missing_signature(self):
        """401 when Linear-Signature header is missing."""
        resp = self._post(_make_event(labels=["agent:agy"]), include_sig=False)
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_rejects_bad_json(self):
        """400 when payload is not valid JSON."""
        headers = {
            "Linear-Signature": _sign(b"not json", "test_secret_xyz"),
            "Content-Type": "application/json",
        }
        resp = self.client.post(
            "/api/gateway/linear", content=b"not json", headers=headers
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_queues_on_dispatch_failure(self):
        """If dispatch_once raises, the event is queued (don't lose it)."""
        self.mock_dispatch_once.side_effect = RuntimeError("linear rate limit")
        resp = self._post(_make_event(labels=["agent:agy"]))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "queued")
        # And the queue has the entry
        db = self.state_dir / "linear_webhook_queue.db"
        self.assertTrue(db.exists())

    def test_non_issue_events_are_queued(self):
        """Comment events (not Issue) bypass dispatch and go to queue."""
        resp = self._post(
            _make_event(event_type="Comment", action="create", labels=["agent:fred"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "queued")
        self.mock_dispatch_once.assert_not_called()

    def test_no_secret_skips_hmac_validation(self):
        """If PRISMATIC_LINEAR_WEBHOOK_SECRET is unset, validation is skipped."""
        with patch.dict(os.environ, {"PRISMATIC_LINEAR_WEBHOOK_SECRET": ""}):
            resp = self._post(_make_event(labels=["agent:agy"]), include_sig=False)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["status"], "dispatched")

    def test_duplicate_event_is_idempotent(self):
        """Same body twice should only queue once (event_id dedup)."""
        event = _make_event(labels=["type:docs"])
        resp1 = self._post(event)
        resp2 = self._post(event)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        import sqlite3
        db = self.state_dir / "linear_webhook_queue.db"
        with sqlite3.connect(str(db)) as conn:
            count = conn.execute("SELECT count(*) FROM linear_webhook_queue").fetchone()[0]
        self.assertEqual(count, 1, f"expected 1 row, got {count}")


if __name__ == "__main__":
    unittest.main()