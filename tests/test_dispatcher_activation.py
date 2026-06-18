"""
tests/test_dispatcher_activation.py
===================================

Verifies that the dispatcher activates agents when the new gate stack
allows it. The dispatcher now runs the following gates for each issue
before calling a launcher:

  1. dedup.is_processed (idempotency)
  2. AGY-specific GitHub credentials gate (gro-1957)
  3. AGY stall-tracker escalation check
  4. ModeSwitch / comment-approval transition gate
  5. credit-policy gate

This test harmonizes the previous "activates all agents" assertion
with the new gate stack by setting AUTONOMOUS mode (so the mode-switch
auto-fires), mocking gql() to return comments, mocking
GitHubProvider().has_credentials() to True, and pre-seeding the
dedup connection so the stall-tracker lookup returns no rows.
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Mocking external dependencies before importing dispatcher.
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()

import prismatic.dispatcher as dispatcher
from prismatic.mode_switch import OrchestrationMode


def _empty_comments():
    """Return a gql() response that contains no /approve and no pause message."""
    return {"issue": {"comments": {"nodes": []}}}


class TestDispatcherActivation(unittest.TestCase):
    """Verifies that the dispatcher correctly activates agents based on labels."""

    def setUp(self):
        # Force AUTONOMOUS mode so the ModeSwitch auto-fires all transitions
        # without requiring /approve. This is the engine's "no human in the loop"
        # posture; the other modes are tested in test_dispatcher_mode_switch.py.
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.AGENT_LAUNCHERS')
    @patch('prismatic.dispatcher.EventRouterDedup')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    def test_dispatch_once_activates_all_agents(
        self,
        mock_evaluate,
        mock_dedup_cls,
        mock_launchers,
        mock_get_issues,
        mock_github_provider_cls,
        mock_gql,
    ):
        """Each registered agent with a matching label is activated."""
        # dedup mocks
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.is_processed.return_value = False
        # The stall-tracker lookup goes through dedup._conn.cursor().execute(...).fetchone()
        # which under MagicMock returns a truthy non-tuple. The dispatcher only treats
        # a real (tuple, list) row whose first element is truthy as "escalated", so the
        # default MagicMock fetchone() is safely interpreted as "no row".
        mock_dedup._conn.cursor.return_value.fetchone.return_value = None

        # credit policy: allow
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW

        # GitHub credentials: present (so the AGY gate passes)
        mock_github_provider = mock_github_provider_cls.return_value
        mock_github_provider.has_credentials.return_value = True

        # gql() returns empty comments (no /approve)
        mock_gql.return_value = _empty_comments()

        # One issue per agent label
        def issues_for_label(label):
            agent_name = label.replace("agent::", "")
            return [
                {
                    "id": f"issue_{agent_name}",
                    "title": f"Title {agent_name}",
                    "identifier": f"GRO-{agent_name}",
                    "labels": [label],
                }
            ]
        mock_get_issues.side_effect = issues_for_label

        # Mock each launcher to record its invocation
        called_agents = []

        def make_launcher(name):
            def launcher(issue_id, title, **kwargs):
                called_agents.append(name)
                return True
            return launcher

        mock_launchers.get.side_effect = lambda name: make_launcher(name)

        # Run dispatch_once
        dispatcher.dispatch_once(mock_dedup, pipelines={"pipelines": {}})

        expected_agents = ["fred", "kai", "agy", "jules", "codex"]
        for agent in expected_agents:
            with self.subTest(agent=agent):
                self.assertIn(
                    agent,
                    called_agents,
                    f"Agent {agent} was not dispatched! called={called_agents}",
                )

    @patch('prismatic.dispatcher.recover_stalled_agy')
    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.EventRouterDedup')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_dispatch_once_blocks_agy_when_github_missing(
        self,
        mock_add_comment,
        mock_evaluate,
        mock_dedup_cls,
        mock_get_issues,
        mock_github_provider_cls,
        mock_gql,
        mock_recover_stalled,
    ):
        """AGY is blocked with a remediation comment when GitHub credentials are absent.

        This is the GRO-1957 contract: AGY/Jules workflow requires a working
        GitHub API connection. The dispatcher posts a Linear comment with
        remediation guidance and returns None instead of launching AGY.

        We don't mock AGENT_LAUNCHERS. Instead we leave it intact (the real
        launch_agy etc.) and rely on get_issues_with_label to return only
        the AGY issue, so only launch_agy is called and the GitHub gate
        inside it fires.
        """
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)

        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.is_processed.return_value = False
        mock_dedup._conn.cursor.return_value.fetchone.return_value = None

        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW

        # GitHub credentials: ABSENT
        mock_github_provider = mock_github_provider_cls.return_value
        mock_github_provider.has_credentials.return_value = False

        mock_gql.return_value = _empty_comments()

        # Only AGY has an issue
        def issues_for_label(label):
            if label == "agent::agy":
                return [{"id": "agy_issue", "title": "AGY task", "identifier": "GRO-AGY", "labels": [label]}]
            return []
        mock_get_issues.side_effect = issues_for_label

        dispatcher.dispatch_once(mock_dedup, pipelines={"pipelines": {}})

        # A remediation comment was posted
        mock_add_comment.assert_called()
        # The remediation comment mentions the GitHub API
        any_remediation = any(
            "GitHub" in str(call.args[1])
            for call in mock_add_comment.call_args_list
        )
        self.assertTrue(any_remediation, "Expected a GitHub-related remediation comment")


if __name__ == "__main__":
    unittest.main()
