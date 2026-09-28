"""
tests/test_dispatcher_label_contract.py
========================================

Lane-scan label contract: the dispatcher must query the single-colon
``agent:<name>`` labels that actually exist in Linear
(docs/proof-loop-demo-wedge.md — e.g. ``agent:agy``).

Regression: the lane scan queried ``agent::<name>`` (double colon), which
matches no Linear label — every lane perpetually reported STARVED.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

# Mocking external dependencies before importing dispatcher.
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()

import prismatic.dispatcher as dispatcher
from prismatic.mode_switch import OrchestrationMode


class TestDispatcherLabelContract(unittest.TestCase):
    """The lane scan must ask Linear for the labels that exist."""

    @patch('prismatic.dispatcher.recover_stalled_agy')
    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.EventRouterDedup')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    def test_lane_scan_queries_single_colon_agent_labels(
        self,
        mock_evaluate,
        mock_dedup_cls,
        mock_get_issues,
        mock_github_provider_cls,
        mock_gql,
        mock_recover_stalled,
    ):
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)

        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.is_processed.return_value = False
        mock_dedup._conn.cursor.return_value.fetchone.return_value = None
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        mock_github_provider_cls.return_value.has_credentials.return_value = True
        mock_gql.return_value = {"issue": {"comments": {"nodes": []}}}

        # No issues anywhere: the scan runs, finds nothing, reports STARVED.
        mock_get_issues.return_value = []

        dispatcher.dispatch_once(mock_dedup, pipelines={"pipelines": {}})

        # Lane-scan calls carry no kwargs; the capability-routing load
        # snapshot passes max_issues=100. recover_stalled_agy is patched out,
        # so every kwarg-free call below is the lane scan.
        lane_labels = [
            call.args[0]
            for call in mock_get_issues.call_args_list
            if not call.kwargs
        ]
        self.assertTrue(lane_labels, "expected the lane scan to query Linear")

        for agent_name in dispatcher.AGENT_CONFIG:
            with self.subTest(agent=agent_name):
                self.assertIn(
                    f"agent:{agent_name}",
                    lane_labels,
                    f"lane scan did not query the canonical label agent:{agent_name}",
                )
        for label in lane_labels:
            with self.subTest(label=label):
                self.assertNotIn(
                    "::", label,
                    f"lane scan queried non-canonical double-colon label {label!r}",
                )


if __name__ == "__main__":
    unittest.main()
