#!/usr/bin/env python3
"""
linear_relabel.py — bulk-add engine-consumption labels to Linear issues.

Tags every open issue in the GRO team with:
- `agent:<fred|codex|kai|jules|ned>` based on repo + task type
- `engine_consumable:true|false` based on whether the issue has clear acceptance
- `dispatch:ready` for issues that are engine_consumable AND have no blockers

Per Epic 1 of the Q3 roadmap (GRO-3022): the curator lane consumes events
tagged `dispatch:ready`. Without this relabel pass, the queue is invisible
to the engine.

Idempotent: re-running won't double-apply labels.

Usage:
    python3 scripts/linear_relabel.py --dry-run            # show what would change
    python3 scripts/linear_relabel.py --apply              # apply changes
    python3 scripts/linear_relabel.py --apply --limit 50  # only first 50
    python3 scripts/linear_relabel.py --apply --only GRO-3022  # single issue
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# === Linear API config ===

ENV_FILE = "/home/ubuntu/.hermes/profiles/orchestrator/.env"
LINEAR_API = "https://api.linear.app/graphql"
TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"


def get_api_key() -> str:
    with open(ENV_FILE) as f:
        text = f.read()
    prefix = "LINEAR_API_KEY" + "="
    m = re.search("^" + re.escape(prefix) + "(.+)$", text, re.MULTILINE)
    if not m:
        raise SystemExit(f"LINEAR_API_KEY not found in {ENV_FILE}")
    return m.group(1)


def graphql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        LINEAR_API,
        data=body,
        headers={
            "Authorization": get_api_key(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:500]}"}


# === Label classification ===

# Tags these labels need to exist for the relabel to work
REQUIRED_LABELS = [
    "agent:fred", "agent:codex", "agent:kai", "agent:jules", "agent:ned",
    "engine_consumable", "dispatch:ready",
    # These already exist on the team from previous sessions
    "epic", "prismatic-engine", "docs",
]


@dataclass
class IssueClassification:
    """Result of classifying one issue."""
    identifier: str
    title: str
    existing_labels: set[str] = field(default_factory=set)
    suggested_agent: str | None = None
    suggested_engine_consumable: str = "false"  # "true" or "false"
    should_dispatch: bool = False
    reason: str = ""

    def to_add_labels(self) -> list[str]:
        """Labels to add. Returns empty if nothing to do."""
        out = []
        if self.suggested_agent and self.suggested_agent not in self.existing_labels:
            out.append(f"agent:{self.suggested_agent}")
        if f"engine_consumable:{self.suggested_engine_consumable}" not in self.existing_labels:
            out.append(f"engine_consumable:{self.suggested_engine_consumable}")
        if self.should_dispatch and "dispatch:ready" not in self.existing_labels:
            out.append("dispatch:ready")
        return out


def classify_issue(issue: dict) -> IssueClassification:
    """Decide which agent should pick up this issue."""
    identifier = issue["identifier"]
    title = (issue.get("title") or "").strip()
    desc = (issue.get("description") or "").strip()
    labels = {l["name"] for l in issue.get("labels", {}).get("nodes", [])}
    state = issue.get("state", {}).get("name", "").lower()
    priority = issue.get("priority", 0)

    classification = IssueClassification(
        identifier=identifier,
        title=title,
        existing_labels=labels,
    )

    # === Agent suggestion rules (priority order) ===
    title_lower = title.lower()
    desc_lower = desc.lower()

    # Epic / doc tasks → fred (orchestration, infra, governance)
    if "epic" in labels or "docs" in labels or any(
        kw in title_lower for kw in ["doc", "spec", "audit", "curator", "vault",
                                       "governance", "north star", "roadmap"]
    ):
        classification.suggested_agent = "fred"

    # AGY / model routing / quality gates / tests → codex
    elif any(kw in title_lower for kw in ["codex", "agent:", "test", "ratchet",
                                            "pipeline", "smoke", "rework",
                                            "classification", "circuit",
                                            "retry", "dispatch"]):
        classification.suggested_agent = "codex"

    # Doc / link / AOT / broken-link → kai
    elif any(kw in title_lower for kw in ["kai", "link", "aot", "broken-link",
                                           "doc sweep", "content"]):
        classification.suggested_agent = "kai"

    # UI / dashboard / frontend / Claude → jules
    elif any(kw in title_lower for kw in ["jules", "ui", "dashboard", "frontend",
                                           "review-pr"]):
        classification.suggested_agent = "jules"

    # Silent-cron / infrastructure / Ned investigations → ned
    elif any(kw in title_lower for kw in ["silent-cron", "silent cron", "cron",
                                           "infrastructure", "telemetry",
                                           "ned"]):
        classification.suggested_agent = "ned"

    # SILENT prefix in title → ned (Ned's investigation pattern)
    elif title.startswith("[SILENT") or "[SILENT" in title:
        classification.suggested_agent = "ned"

    # PR review / merge-related → jules
    elif "pr review" in title_lower or "merged prs" in title_lower:
        classification.suggested_agent = "jules"

    # Default: codex (most general coding work)
    else:
        classification.suggested_agent = "codex"

    # === Engine consumability ===
    # Heuristic: title contains an action verb anywhere (not just at start).
    # Many real issues are prefixed with [Ned] / [Fred] / [agent:fred] etc.
    # so we look for verbs anywhere in the title.
    action_verbs = [
        "add ", "build ", "fix ", "wire ", "ship ", "implement ",
        "audit ", "diagnose ", "investigate ", "test ", "write ",
        "create ", "deploy ", "migrate ", "refactor ", "verify ",
        "rotate ", "extract ", "dispatch ", "remediate ", "rebuild ",
        "setup ", "set up ", "configure ", "publish ",
        "document ", "explore ", "spec ", "design ", "draft ", "research ",
        "trace ", "scan ", "check ", "review ", "monitor ", "observe ",
        "decide ", "ship ", "remove ", "delete ", "merge ",
        "rebase ", "backfill ", "reconcile ", "enrich ", "bulk ",
    ]
    title_with_sep = title_lower + " "  # trailing space so last-word still matches
    has_action = any(v in title_with_sep for v in action_verbs)
    # "In Review" is open too — the agent can revise a PR or fix review feedback
    is_open_state = state in ("backlog", "todo", "in progress", "in review")
    has_clear_scope = len(desc) > 50 or len(title) > 30

    if has_action and is_open_state and has_clear_scope:
        classification.suggested_engine_consumable = "true"
        # Dispatch-ready: in Backlog/Todo (priority 0 = No priority, still OK)
        if state in ("backlog", "todo"):
            classification.should_dispatch = True
            classification.reason = "action verb + open + clear scope"
        else:
            classification.reason = "action verb + scope but not in backlog/todo"
    else:
        classification.suggested_engine_consumable = "false"
        if not has_action:
            classification.reason = "no action verb in title"
        elif not has_clear_scope:
            classification.reason = "scope unclear (short desc+title)"
        elif not is_open_state:
            classification.reason = f"not in open state ({state})"

    return classification


def ensure_labels_exist() -> dict[str, str]:
    """Ensure all REQUIRED_LABELS exist in the team. Returns name→id map."""
    print(f"=== Ensuring {len(REQUIRED_LABELS)} required labels exist ===")
    # Fetch existing
    q = '{ issueLabels(first: 100) { nodes { id name } } }'
    r = graphql(q)
    existing = {l["name"]: l["id"] for l in r.get("data", {}).get("issueLabels", {}).get("nodes", [])}
    print(f"  {len(existing)} labels already exist")

    name_to_id = dict(existing)
    for name in REQUIRED_LABELS:
        if name in name_to_id:
            continue
        # Create
        m = graphql(
            'mutation($name: String!, $teamId: String!) {'
            '  issueLabelCreate(input: { name: $name, teamId: $teamId }) {'
            '    success issueLabel { id name }'
            '  }'
            '}',
            {"name": name, "teamId": TEAM_ID},
        )
        data = m.get("data", {}).get("issueLabelCreate", {})
        if data.get("success"):
            label = data["issueLabel"]
            name_to_id[label["name"]] = label["id"]
            print(f"  + created: {name}")
        else:
            print(f"  ! failed to create {name}: {m.get('errors', m)}")
    return name_to_id


def fetch_open_issues(limit: int | None = None) -> list[dict]:
    """Fetch all open (unstarted + started) issues in the team."""
    q = (
        '{ issues(filter: { team: { id: { eq: "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef" } },'
        ' state: { type: { in: ["unstarted", "started"] } } }, first: 100) {'
        '  nodes {'
        '    identifier title description priority state { name } labels { nodes { name } }'
        '  }'
        ' }'
    '}'
    )
    r = graphql(q)
    issues = r.get("data", {}).get("issues", {}).get("nodes", [])
    if limit:
        issues = issues[:limit]
    return issues


def apply_labels(issue_id: str, label_ids: list[str]) -> bool:
    """Add labels to an issue."""
    if not label_ids:
        return False  # nothing to do
    m = graphql(
        'mutation($id: String!, $labelIds: [String!]!) {'
        '  issueUpdate(id: $id, input: { labelIds: $labelIds }) {'
        '    success issue { identifier }'
        '  }'
        '}',
        {"id": issue_id, "labelIds": label_ids},
    )
    return bool(m.get("data", {}).get("issueUpdate", {}).get("success"))


def main():
    ap = argparse.ArgumentParser(description="Bulk-relabel Linear issues for engine consumption")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without applying")
    ap.add_argument("--apply", action="store_true",
                    help="Apply label changes (otherwise default to dry-run)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit to first N issues (for testing)")
    ap.add_argument("--only", type=str, default=None,
                    help="Only process this issue identifier (e.g. GRO-3022)")
    ap.add_argument("--skip-label-create", action="store_true",
                    help="Don't try to create labels (assume they exist)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Skip confirmation prompt for --apply")
    args = ap.parse_args()

    # Default to dry-run if no action flag
    if not args.dry_run and not args.apply:
        args.dry_run = True

    print(f"=== linear_relabel.py ===")
    print(f"  Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"  Limit: {args.limit or 'all'}")
    if args.only:
        print(f"  Only: {args.only}")
    print()

    # 1. Ensure labels exist
    if not args.skip_label_create and args.apply:
        label_map = ensure_labels_exist()
    else:
        # Fetch existing
        r = graphql('{ issueLabels(first: 100) { nodes { id name } } }')
        label_map = {l["name"]: l["id"] for l in r.get("data", {}).get("issueLabels", {}).get("nodes", [])}

    # 2. Fetch issues
    print(f"\n=== Fetching open issues ===")
    issues = fetch_open_issues(args.limit)
    if args.only:
        issues = [i for i in issues if i["identifier"] == args.only]
    print(f"  Found {len(issues)} issues to process")

    # 3. Classify each
    print(f"\n=== Classifying ===")
    classifications = [classify_issue(i) for i in issues]

    # Stats
    by_agent: dict[str, int] = {}
    by_dispatch: int = 0
    by_consumable_true: int = 0
    no_change_count = 0
    changes: list[tuple[IssueClassification, list[str]]] = []

    for c in classifications:
        to_add = c.to_add_labels()
        if c.suggested_agent:
            by_agent[c.suggested_agent] = by_agent.get(c.suggested_agent, 0) + 1
        if c.should_dispatch:
            by_dispatch += 1
        if c.suggested_engine_consumable == "true":
            by_consumable_true += 1
        if to_add:
            changes.append((c, to_add))
        else:
            no_change_count += 1

    print(f"\n=== Summary ===")
    print(f"  Total issues: {len(classifications)}")
    print(f"  Would change: {len(changes)}")
    print(f"  Already correct: {no_change_count}")
    print(f"\n  By suggested agent:")
    for agent, count in sorted(by_agent.items()):
        print(f"    {agent}: {count}")
    print(f"\n  Engine consumable: {by_consumable_true}/{len(classifications)}")
    print(f"  Dispatch-ready: {by_dispatch}")

    # 4. Show first few changes
    print(f"\n=== Sample changes (first 10) ===")
    for c, to_add in changes[:10]:
        print(f"  {c.identifier}: +{', '.join(to_add)}")
        print(f"    title: {c.title[:70]}")
        print(f"    reason: {c.reason}")

    if args.dry_run:
        print(f"\n[DRY RUN] {len(changes)} issues would be updated. Re-run with --apply to execute.")
        return

    # 5. Confirm before applying
    if not args.yes:
        resp = input(f"\nApply {len(changes)} label changes? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    # 6. Apply
    print(f"\n=== Applying ===")
    success_count = 0
    fail_count = 0
    for i, (c, to_add) in enumerate(changes):
        if i > 0 and i % 10 == 0:
            print(f"  Progress: {i}/{len(changes)}")
        # Resolve label names → ids
        label_ids = [label_map[name] for name in to_add if name in label_map]
        if apply_labels(c.identifier, label_ids):
            success_count += 1
        else:
            fail_count += 1
            print(f"  ! failed: {c.identifier}")
        # Rate-limit: Linear API is 1500 req/hour per token
        if i > 0 and i % 50 == 0:
            time.sleep(1)

    print(f"\n=== Done ===")
    print(f"  Applied: {success_count}")
    print(f"  Failed: {fail_count}")
    print(f"\n  Next step: dispatch_engine.py can now find {by_dispatch} dispatch-ready issues")


if __name__ == "__main__":
    main()