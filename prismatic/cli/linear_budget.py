"""
CLI entry point for Linear rate-limit budget visibility.
"""
from __future__ import annotations

import argparse
import sys
import json
import os
from prismatic.linear.budget import linear_budget

def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prismatic-linear-budget",
        description="Linear API Rate-Limit Budget CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    status_parser = subparsers.add_parser("status", help="Show current Linear API rate-limit budget status")
    
    args = parser.parse_args(argv)
    
    if args.command == "status":
        # Compute status: remaining, reset_in_seconds, top_offenders
        util = linear_budget.get_current_utilization("global")
        remaining = util["current_tokens"]
        
        # reset_in_seconds estimate
        reset_in_seconds = util["retry_after_estimate"]
        
        # Load top offenders from state file
        state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
        counts_file = os.path.join(state_dir, "linear_call_counts.json")
        top_offenders = {}
        if os.path.exists(counts_file):
            try:
                with open(counts_file, "r") as f:
                    counts = json.load(f)
                # Sort top offenders by call counts in descending order
                sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
                top_offenders = dict(sorted_counts)
            except Exception:
                pass
                
        status_data = {
            "remaining": remaining,
            "reset_in_seconds": reset_in_seconds,
            "top_offenders": top_offenders
        }
        
        # Output as JSON
        print(json.dumps(status_data, indent=2))
        return 0
        
    return 1

def main() -> None:
    sys.exit(run(sys.argv[1:]))

if __name__ == "__main__":
    main()
