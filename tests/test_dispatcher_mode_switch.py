"""
tests/test_dispatcher_mode_switch.py
=====================================

Verifies the dispatcher's integration with the OrchestrationModeSwitch.

Gate stack reminder (each gate is additive):

  1. dedup.is_processed
  2. AGY GitHub-credentials gate
  3. AGY stall-tracker escalation check
  4. ModeSwitch / comment-approval transition gate
  5. credit-policy gate

The previous version of this test predated the GitHub gate and the
stall-tracker awareness. The tests below harmonize to the new gate
stack without modifying engine code.
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Mock external dependencies before importing the dispatcher
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()

import prismatic.dispatcher as dispatcher
from prismatic.mode_switch import OrchestrationMode


def _comments_with(approve=False, pause_signature=None):
    """Build a gql() response for issue comments.

    Args:
        approve: include a /approve comment if True.
        pause_signature: substring that, if present in any comment,
            marks the issue as already having a pause comment.
    """
    nodes = []
    if approve:
        nodes.append({"body": "Looks good. /approve"})
    if pause_signature:
        nodes.append({"body": f"Some prior message containing {pause_signature}."})
    return {"issue": {"comments": {"nodes": nodes}}}


class TestDispatcherModeSwitchIntegration(unittest.TestCase):
    """
    Verifies that the dispatcher correctly integrates with the
    OrchestrationModeSwitch.
    """

    def setUp(self):
        # Patch sleep to be instant
        self.sleep_patcher = patch('time.sleep', return_value=None)
        self.mock_sleep = self.sleep_patcher.start()

        # Reset mode_switch to Collaborative
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)

        # Setup mock db/dedup
        self.mock_dedup = MagicMock()
        self.mock_dedup.is_processed.return_value = False
        # Stall-tracker: no row (escalated=0 implicit)
        self.mock_dedup._conn.cursor.return_value.fetchone.return_value = None

        # Reset mock calls track
        self.mock_launcher_calls = []

        # Mock agent launchers
        self.original_launchers = dispatcher.AGENT_LAUNCHERS.copy()
        for name in dispatcher.AGENT_LAUNCHERS:
            def make_mock_launcher(n):
                return lambda issue_id, title, **kwargs: self.mock_launcher_calls.append(n)
            dispatcher.AGENT_LAUNCHERS[name] = make_mock_launcher(name)

    def tearDown(self):
        dispatcher.AGENT_LAUNCHERS = self.original_launchers
        self.sleep_patcher.stop()

    # ── COLLABORATIVE MODE ─────────────────────────────────────

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_collaborative_mode_gates_major_transition(
        self, mock_comment, mock_evaluate, mock_get_issues, mock_github_provider_cls, mock_gql
    ):
        """Collaborative mode gates the major dispatch -> execute (AGY) transition.

        In collaborative mode the decompose -> execute transition is a major
        transition, so the ModeSwitch requests approval. With no /approve in
        comments and no prior pause comment, the dispatcher posts a pause
        comment and skips the launcher.
        """
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW

        # GitHub credentials: present (so the AGY gate doesn't pre-empt the test)
        mock_github_provider = mock_github_provider_cls.return_value
        mock_github_provider.has_credentials.return_value = True

        # Simulate issue with agent::agy (dispatch -> execute is major)
        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_123", "title": "Test AGY", "identifier": "GRO-123", "labels": [label]}]
            if label == "agent::agy" else []
        )

        # No /approve in comments, no prior pause comment
        mock_gql.return_value = _comments_with(approve=False, pause_signature=None)

        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})

        # Verify it was not dispatched (launcher not called)
        self.assertNotIn("agy", self.mock_launcher_calls)
        # Verify at least one pause comment was posted
        pause_calls = [
            call for call in mock_comment.call_args_list
            if "Transition paused" in str(call.args[1])
        ]
        self.assertGreaterEqual(
            len(pause_calls), 1,
            f"Expected at least one 'Transition paused' comment. "
            f"Got calls: {mock_comment.call_args_list}"
        )
        # The first pause comment is the dispatch -> execute transition
        self.assertIn("dispatch", str(pause_calls[0].args[1]))
        self.assertIn("execute", str(pause_calls[0].args[1]))

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_collaborative_mode_allows_minor_transition(
        self, mock_comment, mock_evaluate, mock_get_issues, mock_github_provider_cls, mock_gql
    ):
        """Collaborative mode auto-fires minor execute -> review (Jules) transition."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW

        # GitHub credentials: present
        mock_github_provider = mock_github_provider_cls.return_value
        mock_github_provider.has_credentials.return_value = True

        # Simulate issue with agent::jules (execute -> review is minor)
        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_456", "title": "Test Jules", "identifier": "GRO-456", "labels": [label]}]
            if label == "agent::jules" else []
        )

        # Run dispatch
        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})

        # Verify it was successfully launched/dispatched
        self.assertIn("jules", self.mock_launcher_calls)
        # Verify no pause comment was posted
        mock_comment.assert_not_called()

    # ── AUTONOMOUS MODE ────────────────────────────────────────

    @patch('prismatic.dispatcher.recover_stalled_agy')
    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_autonomous_mode_auto_fires_all(
        self, mock_comment, mock_evaluate, mock_get_issues,
        mock_github_provider_cls, mock_gql, mock_recover_stalled,
    ):
        """Autonomous mode auto-fires all normal transitions without comments.

        We patch out recover_stalled_agy because the post-dispatch stall
        recovery sweep is exercised in test_escalation_gating below; this
        test focuses on the pure auto-fire path.
        """
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW

        mock_github_provider = mock_github_provider_cls.return_value
        mock_github_provider.has_credentials.return_value = True

        # Simulate issue with agent::agy
        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_123", "title": "Test AGY", "identifier": "GRO-123", "labels": [label]}]
            if label == "agent::agy" else []
        )

        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})

        self.assertIn("agy", self.mock_launcher_calls)
        mock_comment.assert_not_called()

    # ── COMMENT-BASED APPROVAL ─────────────────────────────────

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_comment_based_approval(
        self, mock_comment, mock_evaluate, mock_get_issues, mock_github_provider_cls, mock_gql
    ):
        """A transition that is paused fires when /approve is found in Linear comments."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW

        mock_github_provider = mock_github_provider_cls.return_value
        mock_github_provider.has_credentials.return_value = True

        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_123", "title": "Test AGY", "identifier": "GRO-123", "labels": [label]}]
            if label == "agent::agy" else []
        )

        # /approve is in a comment
        mock_gql.return_value = _comments_with(approve=True)

        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})

        self.assertIn("agy", self.mock_launcher_calls)
        mock_comment.assert_not_called()

    # ── ESCALATION GATING ──────────────────────────────────────

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.add_comment')
    @patch('sqlite3.connect')
    @patch('prismatic.dispatcher.transition_label')
    def test_escalation_gating(
        self, mock_transition, mock_sqlite, mock_comment, mock_get_issues,
        mock_github_provider_cls, mock_gql,
    ):
        """Stalled AGY task escalation is gated and requires approval even in Autonomous mode."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)

        mock_conn = MagicMock()
        mock_sqlite.return_value = mock_conn
        mock_cursor = mock_conn.cursor.return_value

        # The dispatcher reads the row once via the stall-tracker cursor.
        # After update + read the row will look like (cycle_count, escalated).
        # The first read returns (2, 0) — escalated=0, cycle_count=2.
        mock_cursor.fetchone.return_value = (2, 0)

        # Simulate issue stalled in AGY
        mock_get_issues.return_value = [
            {"id": "stalled_issue", "title": "Stalled", "identifier": "GRO-100", "labels": ["agent::agy"]}
        ]

        # No /approve in comments
        mock_gql.return_value = _comments_with(approve=False)

        # Run stalled AGY recovery with max_retries=3
        dispatcher.recover_stalled_agy(max_retries=3, escalate_to="fred")

        # Escalation is gated even in Autonomous mode
        mock_transition.assert_not_called()
        # Verify that at least one comment was posted explaining pause
        pause_calls = [
            call for call in mock_comment.call_args_list
            if "Transition paused" in str(call.args[1])
        ]
        self.assertGreaterEqual(len(pause_calls), 1)
        # The escalation comment is marked as Escalation
        self.assertIn("Escalation", str(pause_calls[0].args[1]))


if __name__ == "__main__":
    unittest.main()
