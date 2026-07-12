"""
prismatic/observability/test_ops_feed.py — Gap 12 ops_feed tests (6 tests)

Tests for post_review_event_to_linear() in ops_feed.py.
All LinearTaskProvider.add_comment calls are mocked — no real HTTP traffic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from prismatic.observability.ops_feed import (
    post_review_event_to_linear,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


VALID_EVENT = "review.completed"
SAMPLE_PAYLOAD = {"run_id": "run-001", "verdict": "approve", "reviewer": "ned"}


# ── TestOpsFeed ──────────────────────────────────────────────────────────────


class TestOpsFeed:
    def test_post_review_event_with_valid_event_type_attempts_linear(self):
        """A valid event type triggers LinearTaskProvider.add_comment."""
        mock_provider = MagicMock()
        mock_provider.add_comment.return_value = True

        with patch(
            "prismatic.observability.ops_feed.LinearTaskProvider",
            return_value=mock_provider,
        ):
            result = post_review_event_to_linear(
                issue_id="GRO-100",
                event_type=VALID_EVENT,
                payload=SAMPLE_PAYLOAD,
                linear_api_key="test-key-abc",
            )

        assert result is True
        mock_provider.add_comment.assert_called_once()
        call_args = mock_provider.add_comment.call_args
        # First positional arg is issue_id
        assert call_args[0][0] == "GRO-100"
        # Second positional arg is the markdown body (non-empty string)
        body = call_args[0][1]
        assert isinstance(body, str)
        assert len(body) > 0

    def test_post_review_event_with_invalid_event_type_returns_false(self):
        """An invalid event_type returns False without calling LinearTaskProvider."""
        mock_provider = MagicMock()

        with patch(
            "prismatic.observability.ops_feed.LinearTaskProvider",
            return_value=mock_provider,
        ):
            result = post_review_event_to_linear(
                issue_id="GRO-100",
                event_type="bogus.event",
                payload=SAMPLE_PAYLOAD,
                linear_api_key="test-key-abc",
            )

        assert result is False
        mock_provider.add_comment.assert_not_called()

    def test_post_review_event_with_no_api_key_returns_false_gracefully(self):
        """Missing key → stdout fallback, returns False, no exception raised."""
        # Ensure LINEAR_API_KEY is not set in the environment
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("LINEAR_API_KEY", None)

            result = post_review_event_to_linear(
                issue_id="GRO-100",
                event_type=VALID_EVENT,
                payload=SAMPLE_PAYLOAD,
                linear_api_key=None,
            )

        assert result is False  # No API key → stdout-only, returns False

    def test_post_review_event_swallows_linear_api_exception(self):
        """If LinearTaskProvider.add_comment raises, the function returns False."""
        mock_provider = MagicMock()
        mock_provider.add_comment.side_effect = RuntimeError("Linear network error")

        with patch(
            "prismatic.observability.ops_feed.LinearTaskProvider",
            return_value=mock_provider,
        ):
            result = post_review_event_to_linear(
                issue_id="GRO-100",
                event_type=VALID_EVENT,
                payload=SAMPLE_PAYLOAD,
                linear_api_key="test-key-abc",
            )

        assert result is False  # exception swallowed

    def test_post_review_event_falls_back_to_stderr_on_linear_failure(self, capsys):
        """When Linear raises, an error message is written to stderr."""
        mock_provider = MagicMock()
        mock_provider.add_comment.side_effect = ConnectionError("timeout")

        with patch(
            "prismatic.observability.ops_feed.LinearTaskProvider",
            return_value=mock_provider,
        ):
            post_review_event_to_linear(
                issue_id="GRO-100",
                event_type=VALID_EVENT,
                payload=SAMPLE_PAYLOAD,
                linear_api_key="test-key-abc",
            )

        captured = capsys.readouterr()
        assert captured.err != "", "Expected stderr output when Linear call fails"
        assert "ops_feed" in captured.err or "GRO-100" in captured.err

    def test_post_review_event_payload_serialized_as_markdown(self):
        """The body passed to add_comment contains markdown structure."""
        mock_provider = MagicMock()
        mock_provider.add_comment.return_value = True
        nested_payload = {
            "run_id": "run-002",
            "verdict": "request_changes",
            "details": {"reason": "missing tests", "count": 3},
        }

        with patch(
            "prismatic.observability.ops_feed.LinearTaskProvider",
            return_value=mock_provider,
        ):
            post_review_event_to_linear(
                issue_id="GRO-200",
                event_type=VALID_EVENT,
                payload=nested_payload,
                linear_api_key="test-key-abc",
            )

        mock_provider.add_comment.assert_called_once()
        body = mock_provider.add_comment.call_args[0][1]
        # Should be a markdown string
        assert "##" in body or "|" in body, "Body should contain markdown formatting"
        # The event type should appear somewhere in the body
        assert "review.completed" in body
        # Nested dict should be serialized (not raw repr)
        assert "missing tests" in body or "json" in body.lower() or "details" in body


# ── TestPatternB ─────────────────────────────────────────────────────────────


class TestPatternB:
    """Verify ops_feed uses Pattern B (LinearTaskProvider), not Pattern A (curl)."""

    def test_ops_feed_uses_linear_task_provider_not_subprocess(self):
        """ops_feed.py source must reference LinearTaskProvider and not subprocess/curl."""
        import inspect
        from prismatic import observability

        src = inspect.getsource(observability.ops_feed)
        assert "LinearTaskProvider" in src, (
            "ops_feed must use LinearTaskProvider (Pattern B)"
        )
        assert "subprocess" not in src, "ops_feed must NOT use subprocess (Pattern A)"
        assert "curl" not in src, "ops_feed must NOT use curl (Pattern A)"
