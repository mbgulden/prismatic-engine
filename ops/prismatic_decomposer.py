#!/usr/bin/env python3
"""
Prismatic Decomposer — bridges research completion to implementation.

Runs after AGY/Kai research tasks complete. Parses Linear comments for
deliverable file paths, extracts actionable task descriptions from research
output, and auto-creates follow-up Linear issues for implementation agents.

Usage:
    python3 ops/prismatic_decomposer.py [--dry-run] [--project PROJECT_ID]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Configuration ─────────────────────────────────────
API_KEY = os.environ.get("LINEAR_API_KEY", "")
if not API_KEY:
    print("ERROR: LINEAR_API_KEY not set in environment", file=sys.stderr)
    sys.exit(1)
TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"
AGY_LABEL_ID = "1b69d9c0-20a8-45b3-a594-771b8cba75a7"
FRED_LABEL_ID = "a43efb77-534a-4e39-8ff3-76f0e42019d1"
TODO_STATE_ID = "3d29ebe3-00cf-428b-b52a-bfecb5ae4410"

# Research agents whose completed work triggers decomposition
RESEARCH_LABELS = ["agent:agy", "agent:kai"]

# File path patterns to extract from comments
FILE_PATH_PATTERNS = [
    r"(?:created|saved|wrote|generated|built)\s+(?:file|report|plan|doc|page)s?\s*(?:to|at|in)?\s*[`\"']?([^\s`\"']+\.(?:md|html|json|yaml|yml|py|js|css|csv))",
    r"[`\"']((?:docs|reports|_seo|site|src|assets)/[^\s`\"']+\.(?:md|html|json|yaml|yml|py|js|css|csv))[`\"']",
    r"deliverable[s]?:?\s*[`\"']?([^\s`\"']+\.(?:md|html|json|yaml|yml|py|js|css|csv))",
    r"file[s]?\s+(?:at|in):\s*[`\"']?([^\s`\"']+\.(?:md|html|json|yaml|yml|py|js|css|csv))",
]

# Task extraction patterns (headings/sections that indicate actionable items)
TASK_PATTERNS = [
    r"(?:##|###)\s*(?:Next Steps|Recommended Actions|Action Items|Implementation Tasks|Follow-up Tasks)[:\s]*\n((?:(?:[-*]\s+[^\n]+\n?)|(?:\d+\.\s+[^\n]+\n?))+)",
    r"[-*]\s+(?:TODO|TASK|ACTION|NEXT|BUILD|CREATE|IMPLEMENT|ADD|FIX|UPDATE|DEPLOY)[:\s]+([^\n]+)",
    r"\d+\.\s+(?:Build|Create|Implement|Add|Fix|Update|Deploy|Write|Generate|Set up|Configure)([^\n]+)",
]


# ── Linear API ────────────────────────────────────────


def gql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against the Linear API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": API_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_completed_research(hours_back: int = 24) -> list[dict]:
    """Fetch recently completed AGY/Kai issues with their comments."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    query = f'''query {{
      team(id: "{TEAM_ID}") {{
        issues(
          first: 50,
          filter: {{
            state: {{ type: {{ eq: "completed" }} }},
            completedAt: {{ gte: "{cutoff}" }}
          }}
        ) {{
          nodes {{
            id
            identifier
            title
            description
            completedAt
            labels {{ nodes {{ id name }} }}
            project {{ id name }}
            comments(first: 20, orderBy: createdAt) {{
              nodes {{ body createdAt }}
            }}
          }}
        }}
      }}
    }}'''

    result = gql(query)
    issues = result["data"]["team"]["issues"]["nodes"]

    # Filter to only AGY/Kai issues
    research_issues = []
    for issue in issues:
        label_names = [l["name"] for l in issue["labels"]["nodes"]]
        if any(rl in label_names for rl in RESEARCH_LABELS):
            research_issues.append(issue)

    return research_issues


def extract_file_paths(comments: list[dict]) -> list[str]:
    """Extract file paths from comment bodies."""
    paths = []
    for comment in comments:
        body = comment.get("body", "")
        for pattern in FILE_PATH_PATTERNS:
            matches = re.findall(pattern, body, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                match = match.strip()
                if match and match not in paths:
                    paths.append(match)
    return paths


def extract_tasks(comments: list[dict]) -> list[str]:
    """Extract actionable task descriptions from comment bodies."""
    tasks = []
    for comment in comments:
        body = comment.get("body", "")
        for pattern in TASK_PATTERNS:
            matches = re.findall(pattern, body, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                match = match.strip()
                if match and len(match) > 10 and match not in tasks:
                    tasks.append(match)
    return tasks


def determine_target_project(
    file_paths: list[str],
    fallback_project: dict | None = None,
) -> str | None:
    """Determine which Linear project the follow-up tasks should go to."""
    # Map file paths to projects
    path_project_map = {
        "active-oahu": "Active Oahu Tours",
        "darius-star": "Darius Star: Cyber Coelacanth",
        "prismatic-engine": "Prismatic Engine",
        "hd-platform": "HD Platform",
        "hd-bodygraph": "HD Bodygraph",
        "agentic-swarm-ops": "Agentic Swarm Ops Documentation",
        "_seo": "Active Oahu Tours — SEO & Content Engine",
        "site/": "Active Oahu Tours — Website Overhaul",
    }

    for path in file_paths:
        for prefix, project_name in path_project_map.items():
            if path.startswith(prefix):
                # Look up project ID
                return project_name

    if fallback_project:
        return fallback_project.get("name")

    return None


def get_project_id(name: str) -> str | None:
    """Get a Linear project ID by name."""
    result = gql(f'''query {{
      team(id: "{TEAM_ID}") {{
        projects(first: 50) {{
          nodes {{ id name }}
        }}
      }}
    }}''')
    for proj in result["data"]["team"]["projects"]["nodes"]:
        if proj["name"].lower() == name.lower():
            return proj["id"]
    # Fuzzy match
    for proj in result["data"]["team"]["projects"]["nodes"]:
        if name.lower() in proj["name"].lower():
            return proj["id"]
    return None


def create_followup_task(
    title: str,
    description: str,
    project_id: str,
    parent_issue_id: str,
    dry_run: bool = False,
) -> str | None:
    """Create a follow-up Linear issue."""
    if dry_run:
        print(f"  [DRY RUN] Would create: {title}")
        return None

    # Truncate title to avoid GraphQL issues
    title = title[:200]

    result = gql(
        f'''
        mutation($title: String!, $desc: String!, $team: String!, $proj: String!) {{
          issueCreate(input: {{
            title: $title,
            description: $desc,
            teamId: $team,
            projectId: $proj
          }}) {{
            issue {{ id identifier title }}
          }}
        }}
    ''',
        {
            "title": title,
            "desc": description,
            "team": TEAM_ID,
            "proj": project_id,
        },
    )

    issue = result.get("data", {}).get("issueCreate", {}).get("issue")
    if issue:
        # Add a comment linking back to the research
        gql(f'''mutation {{
          commentCreate(input: {{
            issueId: "{issue['id']}",
            body: "🔗 Derived from research: {parent_issue_id}"
          }}) {{ success }}
        }}''')
        return issue["identifier"]
    return None


# ── Main ──────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prismatic Decomposer — research → implementation bridge"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print tasks without creating issues",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look back N hours for completed research (default: 24)",
    )
    parser.add_argument(
        "--project",
        type=str,
        help="Target project ID for created tasks (auto-detected if omitted)",
    )
    args = parser.parse_args()

    print(f"🔍 Prismatic Decomposer — scanning {args.hours}h of completed research")
    print()

    # Fetch completed research
    research_issues = fetch_completed_research(hours_back=args.hours)
    print(f"Found {len(research_issues)} completed research issue(s)")
    print()

    total_tasks = 0
    total_created = 0

    for issue in research_issues:
        identifier = issue["identifier"]
        title = issue["title"]
        project = issue.get("project") or {}
        comments = issue.get("comments", {}).get("nodes", [])

        print(f"📋 {identifier}: {title[:80]}")

        # Extract file paths and tasks
        file_paths = extract_file_paths(comments)
        tasks = extract_tasks(comments)

        if not tasks:
            print(f"  ⏭️  No actionable tasks found in comments")
            continue

        print(f"  📁 {len(file_paths)} file path(s), 📝 {len(tasks)} task(s)")

        # Determine target project
        target_project_name = determine_target_project(file_paths, project)
        if not target_project_name and not args.project:
            print(f"  ⚠️  Could not determine target project — skipping")
            continue

        target_project_id = args.project or get_project_id(target_project_name)
        if not target_project_id:
            print(f"  ❌ Project '{target_project_name}' not found in Linear")
            continue

        # Create follow-up tasks
        for task_desc in tasks:
            # Build a proper title from the task description
            task_title = task_desc[:200]
            task_body = f"""**Auto-generated from research: {identifier}**

{task_desc}

---
📁 Deliverables referenced:
"""
            for fp in file_paths:
                task_body += f"- `{fp}`\n"

            task_body += f"\n🔗 Parent research: [{identifier}](https://linear.app/growthwebdev/issue/{identifier})"

            created_identifier = create_followup_task(
                task_title,
                task_body,
                target_project_id,
                identifier,
                dry_run=args.dry_run,
            )

            total_tasks += 1
            if created_identifier:
                print(f"  ✅ Created {created_identifier}: {task_title[:60]}...")
                total_created += 1

        print()

    # Summary
    print("=" * 60)
    if args.dry_run:
        print(f"🏁 DRY RUN: {total_tasks} task(s) would be created")
    else:
        print(f"🏁 Complete: {total_created}/{total_tasks} task(s) created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
