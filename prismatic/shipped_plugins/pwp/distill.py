"""
PWP Distill - parses a website_build_plan.md and creates a Linear epic with child issues.

Library API:
    from prismatic_web_publisher.distill import run_distill
    result = run_distill(plan_path, dry_run=False)
    # Returns: dict with epic_id, child_ids, issue_data, status

CLI:
    python -m prismatic_web_publisher.distill <plan.md> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

LINEAR_API = "https://api.linear.app/graphql"


def load_api_key() -> str:
    """Load Linear API key from the orchestrator .env."""
    env_path = Path("/home/ubuntu/.hermes/profiles/orchestrator/.env")
    if not env_path.exists():
        raise RuntimeError(f"Orchestrator .env not found: {env_path}")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            if k.strip() == "LINEAR_API_KEY":
                return v.strip().strip('"').strip("'")
    raise RuntimeError("LINEAR_API_KEY not found in .env")


def gql(query: str, variables: dict | None = None) -> dict:
    """Make a GraphQL request to Linear."""
    api_key = load_api_key()
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        LINEAR_API,
        data=body,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def lookup_labels() -> dict[str, str]:
    """Look up Linear label IDs by name. Returns dict of {name: id}."""
    try:
        data = gql("{ issueLabels(first: 50) { nodes { id name } } }")
    except Exception as e:
        print(f"  Warning: could not look up labels: {e}", file=sys.stderr)
        return {}
    return {l["name"]: l["id"] for l in data.get("data", {}).get("issueLabels", {}).get("nodes", [])}


def parse_build_plan(plan_text: str) -> dict:
    """Extract the page list, automations, etc. from a build plan."""
    # Client name from H1
    client_name = "Client"
    h1_match = re.search(r"^#\s+(.+)$", plan_text, re.MULTILINE)
    if h1_match:
        h1_text = h1_match.group(1).strip()
        m = re.search(r"Website Build Plan\s+(?:for|:)\s+(.+)", h1_text, re.IGNORECASE)
        if m:
            client_name = m.group(1).strip()
        else:
            m = re.search(r"(.+?)\s+(?::\s*)?(?:Comprehensive\s+)?Website Build Plan", h1_text, re.IGNORECASE)
            if m:
                client_name = m.group(1).strip()
            else:
                client_name = h1_text



    # Pages from the page list (look for `**\`/<path>/\`**` or similar)
    pages = []
    in_pages = False
    for line in plan_text.split("\n"):
        # Match "1", "1.", "1.1", "1.1 Full Page List", "1. Site Architecture", "Site Architecture", etc.
        if re.match(r"^###?\s*(?:1(?:\.\d+)?[\.\)]?\s*)?(?:Full\s+)?(?:Page\s+List|Site\s+Architecture)", line, re.IGNORECASE):
            in_pages = True
            continue

        if in_pages:
            if line.startswith("#") and not re.match(r"^###?\s*1(?:\.\d+)?[\.\)]?", line):
                in_pages = False
                break
            # Handles: - Page 1: Home (/) or - About Us (/about)
            paren_url = re.search(r"\((/[\w\-/]*)\)", line)
            if paren_url:
                url = paren_url.group(1).rstrip("/")
                if not url:
                    url = "/"
                title = re.sub(r"^\s*[-*]?\s*(?:Page\s+\d+:\s*)?", "", line[:paren_url.start()]).strip()
                pages.append({"url": url, "title": title or "Home"})
                continue

            # Use 2-pass: find backtick-wrapped URL first, then title in parens
            url_match = re.search(r"`([^`\s]+)`", line)
            title_match = re.search(r"\(([^)]+)\)", line)
            if url_match and title_match:
                url = url_match.group(1).rstrip("/")
                if url.startswith("/") and url.count("/") <= 3 and not url.startswith("//"):
                    pages.append({
                        "url": url,
                        "title": title_match.group(1).strip(),
                    })
                    continue

            # Fallback: **`/path/`:** Page Title
            page_match = re.match(
                r"^\s*[-*]?\s*[`*]*\s*`?(/[\w\-/]+/?)`?[`*]*\s*[*_]?[*_]?[:\s]*(.+)$", line
            )
            if page_match:
                url = page_match.group(1).rstrip("/")
                if url.count("/") <= 3:
                    pages.append({
                        "url": url,
                        "title": page_match.group(2).strip().rstrip(":").rstrip("*").strip(),
                    })
                    continue

    # Phases (just count from any "## Phase" headers)
    phases = re.findall(r"^##\s+Phase\s+\d+", plan_text, re.MULTILINE | re.IGNORECASE)

    # Automations (look for "##" + workflow-ish name)
    automations = []
    for line in plan_text.split("\n"):
        auto_match = re.match(r"^##\s+(\d+\.\s+)?(.+?(?:Workflow|Automation|Sequence|Flow).+)$", line, re.IGNORECASE)
        if auto_match:
            name = auto_match.group(2).strip()
            # Only consider it a workflow if it's not a generic section
            if not any(skip in name.lower() for skip in ["tech", "spec", "metric", "asset", "design"]):
                automations.append({"title": name})

    return {
        "client_name": client_name,
        "pages": pages,
        "phases": phases,
        "automations": automations,
    }


def issue_for_page(client_name: str, page: dict, build_plan_path: str) -> dict:
    """Create the issue payload for a single page build."""
    url = page.get("url", "/")
    title = page.get("title", url)
    return {
        "title": f"[{client_name}] Build page: {title} (`{url}/`)",
        "description": (
            f"Build the page at `{url}/` per the build plan.\n\n"
            f"**Build plan:** `{build_plan_path}`\n\n"
            f"**Reference:** build plan §2 (per-page content briefs)\n\n"
            f"Acceptance criteria:\n"
            f"- [ ] Page built per content brief\n"
            f"- [ ] Mobile-responsive (Tailwind responsive classes)\n"
            f"- [ ] SEO meta + OpenGraph + favicon\n"
            f"- [ ] Internal nav/footer links work\n"
            f"- [ ] Hero image (use Unsplash or AGY image gen with negative prompt for non-aggressive)\n"
            f"- [ ] CTA buttons wired to the right destinations"
        ),
        "labels": ["agent:fred", "agent:kai-content"],
        "priority": 2,
    }


def issue_for_design(client_name: str, plan_path: str) -> dict:
    """Create the issue payload for the design system implementation."""
    return {
        "title": f"[{client_name}] Design system implementation (colors, type, components)",
        "description": (
            f"Implement the design system per the build plan §3.\n\n"
            f"**Build plan:** `{plan_path}`\n\n"
            f"**Components:** color tokens, typography scale, button/card/form utilities, "
            f"hero/section/card layouts, mobile responsiveness\n\n"
            f"Acceptance:\n"
            f"- [ ] CSS tokens in `src/styles/global.css`\n"
            f"- [ ] Reusable components in `src/components/`\n"
            f"- [ ] Base layout with Nav + Footer"
        ),
        "labels": ["agent:fred", "agent:kai-css"],
        "priority": 2,
    }


def issue_for_assets(client_name: str, plan_path: str) -> dict:
    """Create the issue payload for the asset curation."""
    return {
        "title": f"[{client_name}] Asset curation — hero images, classes, instructor portraits",
        "description": (
            f"Curate all visual assets per the build plan §4.\n\n"
            f"**Build plan:** `{plan_path}`\n\n"
            f"**Asset types:** logo, hero images (5), instructor portraits, icons (Lucide SVG)\n\n"
            f"**Source strategy:** AGY image generation with strict negative prompts "
            f"for non-aggressive imagery, OR curated Unsplash assets.\n\n"
            f"Acceptance:\n"
            f"- [ ] All hero images optimized and in `public/`\n"
            f"- [ ] Logo (3 versions: primary, stacked, dark mode)\n"
            f"- [ ] Instructor portraits (or placeholders)\n"
            f"- [ ] Icons (Lucide SVG) for nav + CTAs"
        ),
        "labels": ["agent:fred", "agent:agy"],
        "priority": 2,
    }


def issue_for_automation(client_name: str, plan_path: str, automation: dict) -> dict:
    """Create the issue payload for an automation workflow."""
    title = automation.get("title", "Automation")
    return {
        "title": f"[{client_name}] Automation: {title}",
        "description": (
            f"Set up the automation: {title}\n\n"
            f"**Build plan:** `{plan_path}`\n\n"
            f"**Reference:** build plan §6 (automation workflows)\n\n"
            f"Acceptance:\n"
            f"- [ ] Provider configured (FareHarbor / Formspree / Mailgun / etc.)\n"
            f"- [ ] Triggers + conditions set up\n"
            f"- [ ] Test with a real interaction\n"
            f"- [ ] Monitoring + alerts on failure"
        ),
        "labels": ["agent:ned", "agent:ned-infra"],
        "priority": 3,
    }


def issue_for_deploy(client_name: str, plan_path: str) -> dict:
    """Create the issue payload for the deploy."""
    return {
        "title": f"[{client_name}] Cloudflare Pages deploy + DNS + monitoring",
        "description": (
            f"Deploy the site to Cloudflare Pages, set up DNS, configure monitoring.\n\n"
            f"**Build plan:** `{plan_path}`\n\n"
            f"**Reference:** build plan §5 (technical requirements)\n\n"
            f"Acceptance:\n"
            f"- [ ] Cloudflare Pages project created\n"
            f"- [ ] Connected to GitHub repo\n"
            f"- [ ] Custom domain configured\n"
            f"- [ ] 301 redirects (HTTP→HTTPS, www→non-www)\n"
            f"- [ ] Analytics tracking all pages\n"
            f"- [ ] Uptime monitoring alerts on downtime"
        ),
        "labels": ["agent:ned", "agent:ned-infra"],
        "priority": 2,
    }


def create_issue(title: str, description: str, labels: list, priority: int = 2, parent_id: str | None = None) -> str | None:
    """Create a Linear issue. Returns the issue identifier (e.g. GRO-123) or None on failure."""
    label_map = lookup_labels()
    label_ids = [label_map[l] for l in labels if l in label_map]
    if not label_ids:
        print(f"  Warning: no valid labels found for {labels}", file=sys.stderr)
        return None

    issue_input = {
        "title": title,
        "description": description,
        "priority": priority,
        "labelIds": label_ids,
    }
    if parent_id:
        issue_input["parentId"] = parent_id

    result = gql(
        """mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier }
          }
        }""",
        {"input": issue_input}
    )
    dc = (result.get("data") or {}).get("issueCreate") or {}
    if dc.get("success"):
        return dc.get("issue", {}).get("identifier")
    return None


# ── Library API ──────────────────────────────────────────────

def run_distill(
    plan_path: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Library API: parse a build plan and create Linear issues.

    Args:
        plan_path: Path to website_build_plan.md
        dry_run: If True, don't actually create issues (just return what would be created)

    Returns:
        dict with keys:
            - epic_id: str (e.g. "GRO-2142")
            - child_ids: list of str
            - parsed: dict from parse_build_plan()
            - issues: list of dicts (what was created or would be)
            - status: "ok" | "error"
            - error: str (if status == "error")
    """
    plan_path = Path(plan_path).resolve()
    if not plan_path.is_file():
        return {"status": "error", "error": f"{plan_path} is not a file"}

    parsed = parse_build_plan(plan_path.read_text(encoding="utf-8"))

    if dry_run:
        # Just return what would be created
        issues = [
            issue_for_page(parsed["client_name"], p, str(plan_path))
            for p in parsed["pages"]
        ]
        issues.append(issue_for_design(parsed["client_name"], str(plan_path)))
        issues.append(issue_for_assets(parsed["client_name"], str(plan_path)))
        issues.extend(
            issue_for_automation(parsed["client_name"], str(plan_path), a)
            for a in parsed["automations"]
        )
        issues.append(issue_for_deploy(parsed["client_name"], str(plan_path)))
        return {
            "status": "ok",
            "epic_id": None,
            "child_ids": [],
            "parsed": parsed,
            "issues": issues,
            "dry_run": True,
        }

    # Create the parent epic
    epic_title = f"[{parsed['client_name']}] Website build epic (from build plan)"
    epic_description = (
        f"**Source:** `{plan_path}`\n\n"
        f"**Client:** {parsed['client_name']}\n"
        f"**Pages:** {len(parsed['pages'])}\n"
        f"**Phases:** {len(parsed['phases'])}\n"
        f"**Automations:** {len(parsed['automations'])}\n\n"
        f"Generated from the Prismatic Web Plugin build plan. All child issues are derived from the plan's site architecture, design system, and automation workflows."
    )
    epic_id_str = create_issue(epic_title, epic_description, ["agent:fred"], 1)
    if not epic_id_str:
        return {"status": "error", "error": "Failed to create parent epic"}

    # Get the epic UUID
    epic_num = int(epic_id_str.split("-")[1])
    r = gql(f"""query {{ issues(filter: {{ number: {{ eq: {epic_num} }} }}) {{ nodes {{ id }} }} }}""")
    epic_uuid = r["data"]["issues"]["nodes"][0]["id"]

    created = [epic_id_str]

    # Per-page issues
    for page in parsed["pages"]:
        issue = issue_for_page(parsed["client_name"], page, str(plan_path))
        ident = create_issue(issue["title"], issue["description"], issue["labels"], issue["priority"], parent_id=epic_uuid)
        if ident:
            created.append(ident)
            print(f"  ✓ {ident}  {issue['title'][:60]}")
        time.sleep(0.3)

    # Design system
    issue = issue_for_design(parsed["client_name"], str(plan_path))
    ident = create_issue(issue["title"], issue["description"], issue["labels"], issue["priority"], parent_id=epic_uuid)
    if ident:
        created.append(ident)
        print(f"  ✓ {ident}  {issue['title'][:60]}")
    time.sleep(0.3)

    # Assets
    issue = issue_for_assets(parsed["client_name"], str(plan_path))
    ident = create_issue(issue["title"], issue["description"], issue["labels"], issue["priority"], parent_id=epic_uuid)
    if ident:
        created.append(ident)
        print(f"  ✓ {ident}  {issue['title'][:60]}")
    time.sleep(0.3)

    # Automations
    for auto in parsed["automations"]:
        issue = issue_for_automation(parsed["client_name"], str(plan_path), auto)
        ident = create_issue(issue["title"], issue["description"], issue["labels"], issue["priority"], parent_id=epic_uuid)
        if ident:
            created.append(ident)
            print(f"  ✓ {ident}  {issue['title'][:60]}")
        time.sleep(0.3)

    # Deploy
    issue = issue_for_deploy(parsed["client_name"], str(plan_path))
    ident = create_issue(issue["title"], issue["description"], issue["labels"], issue["priority"], parent_id=epic_uuid)
    if ident:
        created.append(ident)
        print(f"  ✓ {ident}  {issue['title'][:60]}")

    return {
        "status": "ok",
        "epic_id": epic_id_str,
        "child_ids": created[1:],
        "parsed": parsed,
        "issues": [{"title": c} for c in created],
        "dry_run": False,
    }


# ── CLI ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PWP Distill - create Linear issues from a build plan")
    parser.add_argument("plan_path", help="Path to website_build_plan.md")
    parser.add_argument("--out", default=None, help="Output directory (not used for distill)")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually create issues")
    args = parser.parse_args()

    result = run_distill(args.plan_path, dry_run=args.dry_run)

    if result["status"] == "error":
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    parsed = result["parsed"]
    print(f"\n  Client: {parsed['client_name']}")
    print(f"  Pages: {len(parsed['pages'])}")
    print(f"  Phases: {len(parsed['phases'])}")
    print(f"  Automations: {len(parsed['automations'])}")

    if result.get("dry_run"):
        print(f"\n=== DRY RUN — would create {len(result['issues'])} issues ===")
        sys.exit(0)

    print(f"\n=== Created {len(result['issues'])} Linear issues ===")
    print(f"Epic: {result['epic_id']}")
    print(f"Children: {len(result['child_ids'])}")
    sys.exit(0)


if __name__ == "__main__":
    main()
