# SPDX-License-Identifier: AGPL-3.0-only
"""CLI entry point for running or watching peer-review pipelines."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prismatic pipeline",
        description="Run or watch the peer-review pipeline for a Linear issue.",
    )
    subparsers = parser.add_subparsers(dest="pipeline_command")

    run_parser = subparsers.add_parser("run", help="Run the pipeline for a specific issue")
    run_parser.add_argument(
        "--issue",
        required=True,
        help="Linear issue identifier (e.g. GRO-X)",
    )
    run_parser.add_argument(
        "--watch",
        action="store_true",
        help="Run in interactive TUI mode (watch mode)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.pipeline_command != "run":
        parser.print_help()
        return 0

    if args.watch:
        # Check if rich is installed to verify optional dependencies
        try:
            import rich
        except ImportError:
            print(
                "Error: Watch mode requires the [tui] extra. Install via: pip install prismatic-engine[tui]",
                file=sys.stderr,
            )
            return 1
        
        # TUI handling is moved to GRO-3068
        print("Watch mode (TUI) is scheduled for GRO-3068.", file=sys.stderr)
        return 1

    # Headless execution
    issue_id = args.issue
    
    # Check for LINEAR_API_KEY
    if not os.environ.get("LINEAR_API_KEY"):
        print("Error: LINEAR_API_KEY environment variable is required.", file=sys.stderr)
        return 1

    try:
        from prismatic.dispatcher import gql, add_comment
        from prismatic.quality.gates import trigger_ned_review
        from prismatic.review.pipeline import PipelineOrchestrator
        from prismatic.review import ReviewerRegistry, RealPRReviewer
    except ImportError as e:
        print(f"Error: Internal import failed: {e}", file=sys.stderr)
        return 1

    # Fetch issue details from Linear
    print(f"Fetching issue {issue_id} from Linear...")
    query = """
    query GetIssueForPipeline($id: String!) {
        issue(id: $id) {
            id
            identifier
            title
            description
            labels {
                nodes {
                    id
                    name
                }
            }
            team {
                id
                name
            }
            pullRequests {
                nodes {
                    id
                    url
                }
            }
        }
    }
    """
    try:
        data = gql(query, {"id": issue_id})
    except Exception as exc:
        print(f"Error fetching issue {issue_id} from Linear: {exc}", file=sys.stderr)
        return 1

    issue_node = data.get("issue")
    if not issue_node:
        print(f"Error: Issue {issue_id} not found in Linear.", file=sys.stderr)
        return 1

    # Extract labels and PR URL
    labels = [{"name": label["name"]} for label in issue_node.get("labels", {}).get("nodes", [])]
    pr_nodes = issue_node.get("pullRequests", {}).get("nodes", [])
    pr_url = pr_nodes[0].get("url") if pr_nodes else ""

    if not pr_url:
        # Fallback to description scanning
        desc = issue_node.get("description") or ""
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", desc)
        if match:
            pr_url = match.group(0)

    issue_dict = {
        "id": issue_node["id"],
        "identifier": issue_node["identifier"],
        "labels": labels,
        "pr_url": pr_url,
        "title": issue_node.get("title", ""),
        "description": issue_node.get("description", ""),
    }

    team_id = issue_node.get("team", {}).get("id", "")

    # Define callbacks for trigger_ned_review
    def post_comment(identifier: str, body: str) -> None:
        success = add_comment(identifier, body)
        if not success:
            raise RuntimeError(f"Linear comment mutation returned success=False")

    def transition_state(identifier: str, target_state_name: str) -> None:
        if not team_id:
            raise ValueError(f"Cannot transition state: team ID not found on issue.")
        
        # Get state ID by name
        states_query = """
        query GetTeamStates($teamId: String!) {
            team(id: $teamId) {
                states {
                    nodes {
                        id
                        name
                    }
                }
            }
        }
        """
        states_data = gql(states_query, {"teamId": team_id})
        states = states_data.get("team", {}).get("states", {}).get("nodes", [])
        state_id = None
        for s in states:
            if s["name"].lower() == target_state_name.lower():
                state_id = s["id"]
                break
        
        if not state_id:
            raise ValueError(f"Workflow state '{target_state_name}' not found for team {team_id}")

        update_query = """
        mutation TransitionIssueState($issueId: String!, $stateId: String!) {
            issueUpdate(id: $issueId, input: { stateId: $stateId }) {
                success
            }
        }
        """
        update_data = gql(update_query, {"issueId": identifier, "stateId": state_id})
        success = update_data.get("issueUpdate", {}).get("success", False)
        if not success:
            raise RuntimeError(f"Linear issue update mutation returned success=False")

    # Discover and load plugins into the registry
    registry = ReviewerRegistry()
    plugins_dir = os.environ.get("PRISMATIC_PLUGINS_DIR") or "plugins"
    if os.path.exists(plugins_dir):
        try:
            from prismatic.core.registry import PluginLoader
            from prismatic.interface.plugin import PluginContext
            from prismatic.quality.plugin_load import read_core_version

            core_ver = read_core_version()
            loader = PluginLoader(core_version=core_ver, plugins_dir=plugins_dir)

            class CLIPluginContext(PluginContext):
                def __init__(self, review_registry):
                    super().__init__(config={}, db_connection=None, state_dir=".prismatic")
                    self.review_registry = review_registry

            ctx = CLIPluginContext(registry)
            loader.scan_and_load_plugins(ctx)
        except Exception as e:
            print(f"Warning: Failed to scan/load plugins: {e}", file=sys.stderr)

    # Initialize reviewer and pipeline
    reviewer = RealPRReviewer(registry=registry)
    pipeline = PipelineOrchestrator(registry=registry)

    print("Running peer-review pipeline...")
    try:
        decision = trigger_ned_review(
            issue_dict,
            reviewer=reviewer,
            pipeline=pipeline,
            post_comment=post_comment,
            transition_state=transition_state,
        )
    except Exception as exc:
        print(f"Error running pipeline: {exc}", file=sys.stderr)
        return 1

    # Print summary
    print("\n--- Pipeline Decision Summary ---")
    print(f"Identifier:   {decision.identifier}")
    print(f"Triggered:    {decision.triggered}")
    if decision.triggered:
        print(f"Verdict:      {decision.verdict}")
        print(f"Target State: {decision.target_state}")
        
        # Check if pipeline decision exists in metadata
        pipeline_dec = decision.metadata.get("pipeline")
        if pipeline_dec:
            print(f"Impact:       {pipeline_dec.get('impact')}")
            print(f"Action:       {pipeline_dec.get('action')}")
            print(f"Rationale:    {pipeline_dec.get('rationale')}")
            
            rework = pipeline_dec.get("rework_payload")
            if rework:
                print(f"Rework Label: {rework.get('rework_label')}")
                print(f"Rework Attempt: {rework.get('rework_attempt')}/{rework.get('max_rework_attempts')}")
        else:
            print("Pipeline:     No pipeline decision made (omitted/no-op)")
            
        if "post_comment_error" in decision.metadata:
            print(f"Warning: Comment posting failed: {decision.metadata['post_comment_error']}", file=sys.stderr)
        if "transition_error" in decision.metadata:
            print(f"Warning: State transition failed: {decision.metadata['transition_error']}", file=sys.stderr)
    else:
        print(f"Skipped:      {decision.metadata.get('reason')}")

    return 0
