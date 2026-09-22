"""Tests for prismatic.jev DecisionClient: parallel questions, fail-closed modes."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from prismatic.jev import DecisionClient
from prismatic.jev.backends import FallbackBackend
from prismatic.jev.errors import DecisionError, NoBackendError
from prismatic.jev.questions import Choice, Noul, Score
from prismatic.jev.resilience import reset_breakers_for_tests, reset_bulkheads_for_tests

FAKE_KEY = "test-fake-key-000"


def _mock_response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    ctx = MagicMock()
    ctx.__enter__.return_value = resp
    return ctx


def _questions():
    return [
        Noul("urgent", "Is this urgent?"),
        Choice("verdict", "Triage.", options=["CLEAN", "REPAIR"]),
        Score("risk", "Risk 0-1."),
    ]


def _payload():
    return {
        "answers": {
            "urgent": {"probability": 0.87},
            "verdict": {
                "choice": "REPAIR",
                "probabilities": {"CLEAN": 0.12, "REPAIR": 0.88},
            },
            "risk": {"score": 0.31},
        }
    }


class TestDecide(unittest.TestCase):
    def setUp(self):
        reset_breakers_for_tests()
        reset_bulkheads_for_tests()
        self.env = patch.dict(
            "os.environ",
            {"SWARMJEV_BACKEND": "openrouter", "OPENROUTER_API_KEY": FAKE_KEY},
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        reset_breakers_for_tests()
        reset_bulkheads_for_tests()

    @patch("urllib.request.urlopen")
    def test_one_call_answers_all_questions_in_parallel(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_payload())
        client = DecisionClient()
        result = client.decide({"pr": "x"}, _questions())

        mock_urlopen.assert_called_once()  # one billed request, not one per question
        self.assertEqual(result.backend, "openrouter")
        self.assertFalse(result.deterministic)
        self.assertAlmostEqual(result.answers["urgent"].probability, 0.87)
        self.assertEqual(result.answers["verdict"].choice, "REPAIR")
        self.assertAlmostEqual(result.answers["risk"].score, 0.31)
        self.assertGreaterEqual(result.latency_ms, 0.0)

    @patch("urllib.request.urlopen")
    def test_fail_closed_on_backend_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("down")
        client = DecisionClient()
        with self.assertRaises(DecisionError):
            client.decide({"pr": "x"}, _questions())

    @patch("urllib.request.urlopen")
    def test_deterministic_mode_uses_defaults_on_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("down")
        client = DecisionClient()
        result = client.decide(
            {"pr": "x"},
            _questions(),
            on_error="deterministic",
            defaults={"urgent": 0.1, "verdict": "CLEAN", "risk": 0.05},
        )
        self.assertTrue(result.deterministic)
        self.assertEqual(result.answers["verdict"].choice, "CLEAN")
        self.assertAlmostEqual(result.answers["risk"].score, 0.05)

    @patch("urllib.request.urlopen")
    def test_deterministic_mode_missing_defaults_raises(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("down")
        client = DecisionClient()
        with self.assertRaises(DecisionError):
            client.decide({"pr": "x"}, _questions(), on_error="deterministic")

    def test_invalid_on_error_mode(self):
        client = DecisionClient()
        with self.assertRaises(DecisionError):
            client.decide({"pr": "x"}, _questions(), on_error="retry")

    def test_duplicate_question_names_rejected(self):
        client = DecisionClient()
        with self.assertRaises(DecisionError):
            client.decide({"pr": "x"}, [Noul("u", "p"), Noul("u", "p2")])

    def test_empty_questions_rejected(self):
        client = DecisionClient()
        with self.assertRaises(DecisionError):
            client.decide({"pr": "x"}, [])

    def test_non_dict_state_rejected(self):
        client = DecisionClient()
        with self.assertRaises(DecisionError):
            client.decide("not-a-dict", _questions())

    def test_default_backend_is_fallback_no_network(self):
        import os

        for var in ("SWARMJEV_BACKEND", "OPENROUTER_API_KEY", "JEV_API_KEY"):
            os.environ.pop(var, None)
        client = DecisionClient()
        self.assertEqual(client.backend_name, "fallback")
        with patch("urllib.request.urlopen") as mock_urlopen:
            with self.assertRaises(NoBackendError):
                client.decide({"pr": "x"}, _questions())
            mock_urlopen.assert_not_called()


class TestAuditAndHygiene(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            "os.environ",
            {"SWARMJEV_BACKEND": "openrouter", "OPENROUTER_API_KEY": FAKE_KEY},
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @patch("urllib.request.urlopen")
    def test_audit_dict_shape_and_no_secrets(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_payload())
        client = DecisionClient()
        result = client.decide({"pr": "x", "secret_note": "do-not-log"}, _questions())
        audit = result.to_audit_dict()

        self.assertEqual(audit["backend"], "openrouter")
        self.assertIn("latency_ms", audit)
        self.assertFalse(audit["deterministic"])
        self.assertIn("verdict", audit["answers"])
        self.assertEqual(audit["answers"]["verdict"]["choice"], "REPAIR")
        # State values never land in the audit trail; keys + hash do.
        self.assertNotIn("do-not-log", json.dumps(audit))
        self.assertIn("secret_note", audit["state_keys"])
        self.assertEqual(len(audit["state_sha256"]), 64)
        self.assertNotIn(FAKE_KEY, json.dumps(audit))

    @patch("urllib.request.urlopen")
    def test_key_never_in_logs_or_tracebacks(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("down")
        client = DecisionClient()
        with self.assertLogs("prismatic.jev", level="DEBUG") as logs:
            with self.assertRaises(DecisionError) as ctx:
                client.decide({"pr": "x"}, _questions())
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertNotIn(FAKE_KEY, "\n".join(logs.output))
        self.assertNotIn(FAKE_KEY, repr(client))


class TestFallbackClient(unittest.TestCase):
    def test_fallback_with_defaults(self):
        client = DecisionClient(backend=FallbackBackend())
        result = client.decide(
            {},
            _questions(),
            on_error="deterministic",
            defaults={"urgent": 0.0, "verdict": "CLEAN", "risk": 0.0},
        )
        self.assertTrue(result.deterministic)
        self.assertEqual(result.answers["verdict"].choice, "CLEAN")


if __name__ == "__main__":
    unittest.main()
