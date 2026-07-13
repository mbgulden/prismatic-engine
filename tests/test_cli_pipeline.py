# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch, MagicMock

from prismatic import cli
from prismatic.quality.gates import NedReviewDecision

class TestCliPipeline(unittest.TestCase):
    
    @patch("prismatic.cli.pipeline.gql")
    @patch("prismatic.cli.pipeline.trigger_ned_review")
    @patch.dict(os.environ, {"LINEAR_API_KEY": "fake-api-key"})
    def test_pipeline_run_success(self, mock_trigger, mock_gql) -> None:
        # Mock Linear issue response
        mock_gql.side_effect = [
            # First call for GetIssueForPipeline
            {
                "issue": {
                    "id": "uuid-123",
                    "identifier": "GRO-1234",
                    "title": "A Test Issue",
                    "description": "Some description",
                    "labels": {"nodes": [{"id": "lbl-1", "name": "agent:ned-review"}]},
                    "team": {"id": "team-456", "name": "Growth Web"},
                    "pullRequests": {"nodes": [{"id": "pr-1", "url": "https://github.com/owner/repo/pull/123"}]},
                }
            }
        ]
        
        # Mock trigger_ned_review return value
        mock_trigger.return_value = NedReviewDecision(
            identifier="GRO-1234",
            triggered=True,
            verdict="APPROVE",
            target_state="Done",
            linear_comment="Comment text",
            metadata={
                "pipeline": {
                    "impact": "trivial",
                    "action": "advance",
                    "rationale": "verdict=APPROVE impact=trivial attempts=0/2 -> action=advance",
                    "rework_payload": None,
                }
            }
        )
        
        out = StringIO()
        err = StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            rc = cli.run(["pipeline", "run", "--issue", "GRO-1234"])
            
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("Fetching issue GRO-1234 from Linear...", output)
        self.assertIn("Running peer-review pipeline...", output)
        self.assertIn("Verdict:      APPROVE", output)
        self.assertIn("Target State: Done", output)
        self.assertIn("Impact:       trivial", output)
        self.assertIn("Action:       advance", output)
        mock_trigger.assert_called_once()
        
        # Verify the issue payload passed to trigger_ned_review
        called_issue = mock_trigger.call_args[0][0]
        self.assertEqual(called_issue["identifier"], "GRO-1234")
        self.assertEqual(called_issue["pr_url"], "https://github.com/owner/repo/pull/123")
        self.assertEqual(called_issue["labels"], [{"name": "agent:ned-review"}])

    @patch.dict(os.environ, {"LINEAR_API_KEY": ""})
    def test_pipeline_run_missing_api_key(self) -> None:
        err = StringIO()
        with patch("sys.stderr", err):
            rc = cli.run(["pipeline", "run", "--issue", "GRO-123"])
        self.assertEqual(rc, 1)
        self.assertIn("Error: LINEAR_API_KEY environment variable is required", err.getvalue())

    @patch("prismatic.cli.pipeline.gql")
    @patch.dict(os.environ, {"LINEAR_API_KEY": "fake-api-key"})
    def test_pipeline_run_issue_not_found(self, mock_gql) -> None:
        mock_gql.return_value = {"issue": None}
        out = StringIO()
        err = StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            rc = cli.run(["pipeline", "run", "--issue", "GRO-NOTFOUND"])
        self.assertEqual(rc, 1)
        self.assertIn("Error: Issue GRO-NOTFOUND not found in Linear.", err.getvalue())

    def test_pipeline_run_watch_mode(self) -> None:
        # Test watch mode behavior
        out = StringIO()
        err = StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            rc = cli.run(["pipeline", "run", "--issue", "GRO-123", "--watch"])
        
        self.assertEqual(rc, 1)
        combined = out.getvalue() + err.getvalue()
        self.assertTrue("rich" in combined.lower() or "watch" in combined.lower())


if __name__ == "__main__":
    unittest.main()
