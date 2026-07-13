# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from prismatic import cli
from prismatic.quality.gates import NedReviewDecision


@patch("prismatic.cli.pipeline.gql")
@patch("prismatic.cli.pipeline.trigger_ned_review")
@patch.dict(os.environ, {"LINEAR_API_KEY": "fake-api-key"})
def test_pipeline_run_success(mock_trigger, mock_gql, capsys) -> None:
    # Mock Linear issue response
    mock_gql.side_effect = [
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
    
    rc = cli.run(["pipeline", "run", "--issue", "GRO-1234"])
        
    assert rc == 0
    captured = capsys.readouterr()
    combined_out = captured.out + captured.err
    assert "Fetching issue GRO-1234 from Linear..." in combined_out
    assert "Running peer-review pipeline..." in combined_out
    assert "Verdict:      APPROVE" in combined_out
    assert "Target State: Done" in combined_out
    assert "Impact:       trivial" in combined_out
    assert "Action:       advance" in combined_out
    mock_trigger.assert_called_once()
    
    called_issue = mock_trigger.call_args[0][0]
    assert called_issue["identifier"] == "GRO-1234"
    assert called_issue["pr_url"] == "https://github.com/owner/repo/pull/123"
    assert called_issue["labels"] == [{"name": "agent:ned-review"}]


@patch.dict(os.environ, {"LINEAR_API_KEY": ""})
def test_pipeline_run_missing_api_key(capsys) -> None:
    rc = cli.run(["pipeline", "run", "--issue", "GRO-123"])
    assert rc == 1
    captured = capsys.readouterr()
    combined_out = captured.out + captured.err
    assert "LINEAR_API_KEY environment variable is required" in combined_out


@patch("prismatic.cli.pipeline.gql")
@patch.dict(os.environ, {"LINEAR_API_KEY": "fake-api-key"})
def test_pipeline_run_issue_not_found(mock_gql, capsys) -> None:
    mock_gql.return_value = {"issue": None}
    rc = cli.run(["pipeline", "run", "--issue", "GRO-NOTFOUND"])
    assert rc == 1
    captured = capsys.readouterr()
    combined_out = captured.out + captured.err
    assert "Issue GRO-NOTFOUND not found in Linear." in combined_out


def test_pipeline_run_watch_mode(capsys) -> None:
    rc = cli.run(["pipeline", "run", "--issue", "GRO-123", "--watch"])
    assert rc == 1
    captured = capsys.readouterr()
    combined_out = captured.out + captured.err
    assert "rich" in combined_out.lower() or "watch" in combined_out.lower()
