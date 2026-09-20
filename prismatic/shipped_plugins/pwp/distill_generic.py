#!/usr/bin/env python3
"""
pwp_distill_generic.py — Generic version of Step 3 that works on any project type.

Like pwp_distill.py but adapts issue templates based on the project type
and uses the synthesis.md + profile.json as input.
"""
import os, sys, json, re, subprocess, argparse
from pathlib import Path

# Linear setup (same as pwp_distill.py)
LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")
if not LINEAR_API_KEY:
    env_path = "/home/ubuntu/.hermes/profiles/orchestrator/.env"
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                if k.strip() == "LINEAR_API_KEY":
                    LINEAR_API_KEY = v.strip().strip('"').strip("'")
                    break
if not LINEAR_API_KEY:
    print("Error: LINEAR_API_KEY not set", file=sys.stderr)
    sys.exit(1)

TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"
PRISMATIC_PROJECT = "2eb2913f-740c-4142-b844-59feec230a9d"
TODO_STATE = "3d29ebe3-00cf-428b-b52a-bfecb5ae4410"

LABELS = {
    "agent:fred": "a43efb77-534a-4e39-8ff3-76f0e42019d1",
    "agent:agy": "1b69d9c0-20a8-45b3-a594-771b8cba75a7",
    "agent:ned": "6e0400c9-fc04-4868-86e3-f3156821f413",
}

def gql(query, variables=None):
    import urllib.request
    payload = {"query": query, "variables": variables or {}}
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps(payload).encode(),
        headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def lookup_labels():
    r = gql("""query { issueLabels(first: 100) { nodes { id name } } }""")
    for node in r["data"]["issueLabels"]["nodes"]:
        for key in LABELS:
            if node["name"] == key:
                LABELS[key] = node["id"]

def parse_synthesis(synthesis_text: str) -> dict:
    """Extract sections from the synthesis.md."""
    sections = {}
    current_section = None
    current_content = []

    for line in synthesis_text.split("\n"):
        m = re.match(r"^##\s+(\d+)\.\s+(.+)", line)
        if m:
            if current_section:
                sections[current_section["title"]] = "\n".join(current_content).strip()
            current_section = {"num": int(m.group(1)), "title": m.group(2).strip()}
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section["title"]] = "\n".join(current_content).strip()

    return sections

def extract_tasks(synthesis_text: str) -> list:
    """Extract task lines (start with - or *) from the synthesis."""
    tasks = []
    for line in synthesis_text.split("\n"):
        m = re.match(r"^\s*[-*]\s+(.+)", line)
        if m:
            task = m.group(1).strip()
            if 10 < len(task) < 200:  # reasonable length
                tasks.append(task)
    # Dedupe
    return list(dict.fromkeys(tasks))[:30]  # cap at 30 tasks

def create_issue(title, description, labels, priority, parent_id=None):
    label_ids = [LABELS[l] for l in labels if LABELS.get(l)]
    if not label_ids:
        label_ids = [LABELS["agent:fred"]]
    input_data = {
        "teamId": TEAM_ID,
        "title": title,
        "description": description,
        "priority": priority,
        "stateId": TODO_STATE,
        "projectId": PRISMATIC_PROJECT,
        "labelIds": label_ids,
    }
    if parent_id:
        input_data["parentId"] = parent_id
    r = gql(
        """mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success issue { identifier }
          }
        }""",
        {"input": input_data}
    )
    if r.get("data", {}).get("issueCreate", {}).get("success"):
        return r["data"]["issueCreate"]["issue"]["identifier"]
    print(f"  ✗ Failed: {title[:60]}", file=sys.stderr)
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("synthesis_path", help="Path to synthesis.md")
    parser.add_argument("profile_path", nargs="?", help="Optional: path to profile.json for project metadata")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-issues", type=int, default=15, help="Max child issues to create")
    args = parser.parse_args()

    synth_path = Path(args.synthesis_path).resolve()
    if not synth_path.is_file():
        print(f"Error: {synth_path} not found", file=sys.stderr)
        sys.exit(1)

    # Load profile if provided
    profile = {}
    if args.profile_path:
        profile_path = Path(args.profile_path).resolve()
        if profile_path.is_file():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))

    project_name = profile.get("project", {}).get("name", synth_path.parent.name)
    project_type = profile.get("project", {}).get("type", "unknown")
    print(f"Generic distill from: {synth_path}")
    print(f"  Project: {project_name}  Type: {project_type}")

    lookup_labels()

    synthesis_text = synth_path.read_text(encoding="utf-8")
    tasks = extract_tasks(synthesis_text)
    print(f"  Extracted {len(tasks)} tasks from synthesis")

    # Cap at max-issues
    tasks = tasks[:args.max_issues]

    if args.dry_run:
        print("\n=== DRY RUN — would create ===\n")
        print(f"  Epic: [{project_name}] Implementation epic (from synthesis)")
        for t in tasks:
            print(f"  • Task: {t[:80]}")
        return

    # Create epic
    import time
    epic_title = f"[{project_name}] Implementation epic (from synthesis)"
    epic_desc = (
        f"**Source:** `{synth_path}`\n"
        f"**Project:** {project_name} ({project_type})\n"
        f"**Tasks:** {len(tasks)}\n\n"
        f"Auto-generated by pwp_distill_generic.py. Children are extracted as actionable tasks."
    )
    print(f"\nCreating epic: {epic_title}")
    epic_id_str = create_issue(epic_title, epic_desc, ["agent:fred"], 2)
    print(f"  ✓ {epic_id_str}")

    # Get epic UUID
    epic_num = int(epic_id_str.split("-")[1])
    r = gql(f"""query {{ issues(filter: {{ number: {{ eq: {epic_num} }} }}) {{ nodes {{ id }} }} }}""")
    epic_uuid = r["data"]["issues"]["nodes"][0]["id"]

    # Create children
    created = [epic_id_str]
    for i, task in enumerate(tasks):
        title = f"[{project_name}] {task[:80]}"
        desc = f"**Task from synthesis:**\n{task}\n\n**Source:** `{synth_path}`\n"
        # Route by task type
        labels = ["agent:agy"]
        if "deploy" in task.lower() or "infra" in task.lower() or "config" in task.lower():
            labels = ["agent:ned"]
        priority = 2
        if i < 3:  # first 3 are foundation, P1
            priority = 1
        ident = create_issue(title, desc, labels, priority, parent_id=epic_uuid)
        if ident:
            created.append(ident)
            print(f"  ✓ {ident}  {title[:60]}")
        time.sleep(0.3)

    print(f"\n=== Created {len(created)} Linear issues ===")
    print(f"Epic: {epic_id_str}, Children: {len(created) - 1}")

if __name__ == "__main__":
    main()