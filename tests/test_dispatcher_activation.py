
import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Mocking external dependencies before importing dispatcher
# This ensures we don't need real signal providers or credit policy engines to test the loop logic.
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()

import prismatic.dispatcher as dispatcher

class TestDispatcherActivation(unittest.TestCase):
    """Verifies that the dispatcher correctly activates agents based on labels."""

    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.AGENT_LAUNCHERS')
    @patch('prismatic.dispatcher.EventRouterDedup')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    def test_dispatch_once_activates_all_agents(self, mock_evaluate, mock_dedup_cls, mock_launchers, mock_get_issues):
        """
        Ensures that agents with 'mode: signal' (e.g., Fred, Kai) and 
        'mode: launch' (e.g., AGY, Jules, Codex) are all activated if 
        matching labels are found.
        """
        # Setup mocks
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.is_processed.return_value = False
        
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        
        # Mock issues for each agent
        def side_effect(label):
            agent_name = label.replace("agent::", "")
            return [{"id": f"issue_{agent_name}", "title": f"Title {agent_name}", "identifier": f"GRO-{agent_name}"}]
        
        mock_get_issues.side_effect = side_effect
        
        # Track which launchers are called
        called_agents = []
        def make_launcher(name):
            def launcher(issue_id, title):
                called_agents.append(name)
                return True
            return launcher
            
        mock_launchers.get.side_effect = lambda name: make_launcher(name)
        
        # Run dispatch_once
        dispatcher.dispatch_once(mock_dedup, pipelines={"pipelines": {}})
        
        # Check if all agents were called
        expected_agents = [
            "fred", "kai", "agy", "jules", "codex",
            "ned", "ned-code", "ned-infra", "ned-audit", "ned-review"
        ]
        for agent in expected_agents:
            with self.subTest(agent=agent):
                self.assertIn(agent, called_agents, f"Agent {agent} was not dispatched!")

if __name__ == "__main__":
    unittest.main()
