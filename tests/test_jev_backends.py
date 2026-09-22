"""Tests for prismatic.jev backends: selection, request shape, fail-closed."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from prismatic.jev.backends import (
    FallbackBackend,
    OpenRouterDecisionsBackend,
    TypeSafeDecisionsBackend,
    resolve_backend,
)
from prismatic.jev.errors import DecisionError, MissingCredentialError, NoBackendError
from prismatic.jev.questions import Choice, Noul

FAKE_KEY = "test-fake-key-000"


def _mock_response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    ctx = MagicMock()
    ctx.__enter__.return_value = resp
    return ctx


def _answers_payload():
    return {
        "answers": {
            "urgent": {"probability": 0.87, "confidence": 0.9},
            "verdict": {
                "choice": "REPAIR",
                "probabilities": {"CLEAN": 0.05, "REPAIR": 0.88, "REJECT": 0.07},
            },
        }
    }


def _questions():
    return [
        Noul("urgent", "Is this urgent?"),
        Choice("verdict", "Triage.", options=["CLEAN", "REPAIR", "REJECT"]),
    ]


class TestBackendResolution(unittest.TestCase):
    def test_default_is_fallback(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("SWARMJEV_BACKEND", None)
            self.assertIsInstance(resolve_backend(), FallbackBackend)

    def test_unknown_name_fails_closed_to_fallback(self):
        with patch.dict("os.environ", {"SWARMJEV_BACKEND": "mystery"}):
            self.assertIsInstance(resolve_backend(), FallbackBackend)

    def test_openrouter_resolution(self):
        with patch.dict(
            "os.environ",
            {"SWARMJEV_BACKEND": "openrouter", "OPENROUTER_API_KEY": FAKE_KEY},
        ):
            self.assertIsInstance(resolve_backend(), OpenRouterDecisionsBackend)

    def test_typesafe_resolution(self):
        with patch.dict(
            "os.environ", {"SWARMJEV_BACKEND": "typesafe", "JEV_API_KEY": FAKE_KEY}
        ):
            self.assertIsInstance(resolve_backend(), TypeSafeDecisionsBackend)


class TestOpenRouterBackend(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            "os.environ", {"OPENROUTER_API_KEY": FAKE_KEY}, clear=False
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @patch("urllib.request.urlopen")
    def test_request_shape(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_answers_payload())
        backend = OpenRouterDecisionsBackend()
        result = backend.decide({"pr": "x"}, _questions())

        self.assertEqual(result.backend, "openrouter")
        self.assertAlmostEqual(result.answers["urgent"].probability, 0.87)
        self.assertEqual(result.answers["verdict"].choice, "REPAIR")
        self.assertGreaterEqual(result.latency_ms, 0.0)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), f"Bearer {FAKE_KEY}")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "typesafe/jev-1.13")
        self.assertEqual(body["state"], {"pr": "x"})
        self.assertIn("urgent", body["questions"])
        self.assertIn("verdict", body["questions"])
        self.assertEqual(
            body["questions"]["verdict"]["options"], ["CLEAN", "REPAIR", "REJECT"]
        )

    def test_missing_key_names_env_var(self):
        import os

        os.environ.pop("OPENROUTER_API_KEY", None)
        with self.assertRaises(MissingCredentialError) as ctx:
            OpenRouterDecisionsBackend()
        self.assertEqual(ctx.exception.env_var, "OPENROUTER_API_KEY")
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

    def test_typesafe_missing_key_names_env_var(self):
        import os

        os.environ.pop("JEV_API_KEY", None)
        with self.assertRaises(MissingCredentialError) as ctx:
            TypeSafeDecisionsBackend()
        self.assertEqual(ctx.exception.env_var, "JEV_API_KEY")

    def test_typesafe_url_override(self):
        with patch.dict(
            "os.environ",
            {"JEV_API_KEY": FAKE_KEY, "JEV_API_URL": "https://example.invalid/decide"},
        ):
            backend = TypeSafeDecisionsBackend()
        self.assertEqual(backend.url, "https://example.invalid/decide")

    @patch("urllib.request.urlopen")
    def test_timeout_fails_closed(self, mock_urlopen):
        import socket

        mock_urlopen.side_effect = socket.timeout("timed out")
        backend = OpenRouterDecisionsBackend()
        with self.assertRaises(DecisionError):
            backend.decide({}, _questions())

    @patch("urllib.request.urlopen")
    def test_url_error_fails_closed(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        backend = OpenRouterDecisionsBackend()
        with self.assertRaises(DecisionError):
            backend.decide({}, _questions())

    @patch("urllib.request.urlopen")
    def test_http_error_fails_closed(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://openrouter.ai/api/alpha/decisions", 500, "boom", {}, None
        )
        backend = OpenRouterDecisionsBackend()
        with self.assertRaises(DecisionError):
            backend.decide({}, _questions())

    @patch("urllib.request.urlopen")
    def test_bad_json_fails_closed(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b"not json"
        ctx = MagicMock()
        ctx.__enter__.return_value = resp
        mock_urlopen.return_value = ctx
        backend = OpenRouterDecisionsBackend()
        with self.assertRaises(DecisionError):
            backend.decide({}, _questions())

    @patch("urllib.request.urlopen")
    def test_missing_answer_fails_closed(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(
            {"answers": {"urgent": {"probability": 0.1}}}
        )
        backend = OpenRouterDecisionsBackend()
        with self.assertRaises(DecisionError):
            backend.decide({}, _questions())

    @patch("urllib.request.urlopen")
    def test_malformed_answer_fails_closed(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(
            {
                "answers": {
                    "urgent": {"probability": 0.1},
                    "verdict": {"choice": "MAYBE", "probabilities": {}},
                }
            }
        )
        backend = OpenRouterDecisionsBackend()
        with self.assertRaises(DecisionError):
            backend.decide({}, _questions())

    @patch("urllib.request.urlopen")
    def test_error_never_leaks_key(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("down")
        backend = OpenRouterDecisionsBackend()
        try:
            backend.decide({}, _questions())
            self.fail("expected DecisionError")
        except DecisionError as exc:
            self.assertNotIn(FAKE_KEY, str(exc))

    def test_repr_hides_key(self):
        backend = OpenRouterDecisionsBackend()
        self.assertNotIn(FAKE_KEY, repr(backend))
        self.assertIn("<set>", repr(backend))


class TestFallbackBackend(unittest.TestCase):
    def test_no_defaults_raises(self):
        with self.assertRaises(NoBackendError):
            FallbackBackend().decide({}, _questions())

    def test_defaults_returned(self):
        result = FallbackBackend().decide(
            {}, _questions(), defaults={"urgent": 0.2, "verdict": "CLEAN"}
        )
        self.assertEqual(result.backend, "fallback")
        self.assertAlmostEqual(result.answers["urgent"].probability, 0.2)
        self.assertEqual(result.answers["verdict"].choice, "CLEAN")

    def test_missing_default_for_question_raises(self):
        with self.assertRaises(DecisionError):
            FallbackBackend().decide({}, _questions(), defaults={"urgent": 0.2})

    def test_invalid_default_raises_not_coerced(self):
        with self.assertRaises(DecisionError):
            FallbackBackend().decide(
                {}, _questions(), defaults={"urgent": 9.9, "verdict": "CLEAN"}
            )


if __name__ == "__main__":
    unittest.main()
