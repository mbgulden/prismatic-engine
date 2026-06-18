import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Mocking external dependencies before importing dispatcher
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()

import prismatic.dispatcher as dispatcher
from prismatic.mode_switch import OrchestrationMode

class TestDispatcherModeSwitchIntegration(unittest.TestCase):
    """
    Verifies that the dispatcher correctly integrates with the OrchestrationModeSwitch.
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

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_collaborative_mode_gates_major_transition(self, mock_comment, mock_evaluate, mock_get_issues, mock_gql):
        """Collaborative mode should gate major transition (dispatch -> execute/agy)."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        
        # Simulate issue with agent::agy (dispatch -> execute is major)
        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_123", "title": "Test AGY", "identifier": "GRO-123", "labels": [label]}]
            if label == "agent::agy" else []
        )
        
        # No "/approve" in comments
        mock_gql.return_value = {"issue": {"comments": {"nodes": []}}}
        
        # Run dispatch
        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})
        
        # Verify it was not dispatched (launcher not called)
        self.assertNotIn("agy", self.mock_launcher_calls)
        # Verify pause comment was posted
        mock_comment.assert_called_once()
        self.assertIn("Transition paused", mock_comment.call_args[0][1])

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_collaborative_mode_allows_minor_transition(self, mock_comment, mock_evaluate, mock_get_issues, mock_gql):
        """Collaborative mode should auto-fire minor transition (execute -> review/jules)."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        
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

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_autonomous_mode_auto_fires_all(self, mock_comment, mock_evaluate, mock_get_issues, mock_gql):
        """Autonomous mode should auto-fire all normal transitions."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        
        # Simulate issue with agent::agy
        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_123", "title": "Test AGY", "identifier": "GRO-123", "labels": [label]}]
            if label == "agent::agy" else []
        )
        
        # Run dispatch
        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})
        
        # Verify it was successfully launched/dispatched
        self.assertIn("agy", self.mock_launcher_calls)
        # Verify no pause comment was posted
        mock_comment.assert_not_called()

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    @patch('prismatic.dispatcher.add_comment')
    def test_comment_based_approval(self, mock_comment, mock_evaluate, mock_get_issues, mock_gql):
        """A transition that is paused should fire if `/approve` is found in Linear comments."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.COLLABORATIVE)
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        
        # Simulate issue with agent::agy
        mock_get_issues.side_effect = lambda label: (
            [{"id": "issue_123", "title": "Test AGY", "identifier": "GRO-123", "labels": [label]}]
            if label == "agent::agy" else []
        )
        
        # Simulate comment containing "/approve"
        mock_gql.return_value = {
            "issue": {
                "comments": {
                    "nodes": [
                        {"body": "This looks great. /approve"}
                    ]
                }
            }
        }
        
        # Run dispatch
        counts = dispatcher.dispatch_once(self.mock_dedup, pipelines={"pipelines": {}})
        
        # Verify it was successfully launched/dispatched
        self.assertIn("agy", self.mock_launcher_calls)
        # Verify no pause comment was posted
        mock_comment.assert_not_called()

    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.add_comment')
    @patch('sqlite3.connect')
    @patch('prismatic.dispatcher.transition_label')
    def test_escalation_gating(self, mock_transition, mock_sqlite, mock_comment, mock_get_issues, mock_gql):
        """Stalled AGY task escalation should be gated and require approval."""
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)
        
        mock_conn = MagicMock()
        mock_sqlite.return_value = mock_conn
        mock_cursor = mock_conn.cursor.return_value
        
        # Simulate row found with cycle_count=2, not escalated
        mock_cursor.fetchone.return_value = (2, 0)
        
        # Simulate issue stalled in agy
        mock_get_issues.return_value = [{"id": "stalled_issue", "title": "Stalled", "identifier": "GRO-100", "labels": ["agent::agy"]}]
        
        # No `/approve` in comments
        mock_gql.return_value = {"issue": {"comments": {"nodes": []}}}
        
        # Run stalled AGY recovery with max_retries=3
        dispatcher.recover_stalled_agy(max_retries=3, escalate_to="fred")
        
        # Since it's an escalation, it should be gated even in Autonomous mode
        # Verify that transition_label was NOT called
        mock_transition.assert_not_called()
        # Verify that comment was posted explaining pause
        mock_comment.assert_called_once()
        self.assertIn("Transition paused", mock_comment.call_args[0][1])
        self.assertIn("Escalation", mock_comment.call_args[0][1])

if __name__ == "__main__":
    unittest.main()
