"""
prismatic-research — CLI utility to scaffold AGY-ready research tasks.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# Bundle to list of reports
BUNDLE_REPORTS = {
    "brief": [
        ("01-executive-summary.md", "Executive Summary", "Summarize core findings, TL;DR, and critical recommendations."),
        ("02-key-recommendations.md", "Key Recommendations", "Tiered recommendations: Must, Should, Could."),
    ],
    "standard": [
        ("01-executive-synthesis.md", "Executive Synthesis", "Triangulate facts across sources and summarize key consensus."),
        ("02-trend-analysis.md", "Trend Analysis", "Identify historical context, active state, and future trajectory."),
        ("03-recommendations.md", "Recommendations", "Actionable recommendations linked directly to evidence."),
    ],
    "deep": [
        ("01-history.md", "History", "Analyze how the subject evolved, legacy issues, and milestones."),
        ("02-current-state.md", "Current State", "Present limitations, bugs, and standard implementations."),
        ("03-trajectory.md", "Trajectory", "Project future trends, emerging standards, or architectural directions."),
        ("04-insight-synthesis.md", "Insight Synthesis", "Highlight conflicts, non-obvious tensions, and cross-source consensus."),
        ("05-recommendations.md", "Recommendations", "Practical recommendations mapped directly to downstream goals."),
    ],
    "architecture": [
        ("01-current-state.md", "Current State", "Current tech stack, codebase layout, and constraints."),
        ("02-tool-landscape.md", "Tool Landscape", "Comparison of tools/libraries in the ecosystem."),
        ("03-design-patterns.md", "Design Patterns", "Common architectural patterns and paradigms."),
        ("04-architecture-implications.md", "Architecture Implications", "Mermaid diagrams, data flows, and resource budgets."),
        ("05-golden-path.md", "Golden Path", "The canonical flow and user onboarding steps."),
        ("06-implementation-recommendations.md", "Implementation Recommendations", "Concrete implementation plan and defensive guardrails."),
    ],
    "content-engine": [
        ("01-audience-map.md", "Audience Map", "Identify target audiences, user intent, and personas."),
        ("02-source-themes.md", "Source Themes", "Key themes, core pillars, and messaging vectors."),
        ("03-expert-vocabulary.md", "Expert Vocabulary", "Glossary of terminology and expert slang/jargon."),
        ("04-search-content-opportunities.md", "Search Content Opportunities", "Search intent, authority gaps, and SEO keywords."),
        ("05-article-cluster-plan.md", "Article Cluster Plan", "Content layout, cluster structure, and mapping."),
        ("06-authority-building-strategy.md", "Authority Building Strategy", "E-E-A-T strategy, internal linking logic, and sources."),
        ("07-recommended-content-briefs.md", "Recommended Content Briefs", "Scaffolded drafts/briefs for writing pages."),
    ],
    "competitive": [
        ("01-competitive-landscape.md", "Competitive Landscape", "Overview of competitors and market context."),
        ("02-differentiation.md", "Differentiation", "Key vectors of differentiation and unique values."),
        ("03-category-tensions.md", "Category Tensions", "SWOT analysis, industry tensions, and pricing models."),
        ("04-adoption-barriers.md", "Adoption Barriers", "Friction points for setup, pricing, or migrations."),
        ("05-risks-and-opportunities.md", "Risks and Opportunities", "Strategic recommendations based on gaps."),
    ],
    "golden-path": [
        ("01-user-journey.md", "User Journey", "Step-by-step developer walkthrough and onboarding journey."),
        ("02-setup-friction.md", "Setup Friction", "Identify setup hurdles and environment dependencies."),
        ("03-canonical-workflow.md", "Canonical Workflow", "Reference implementation, code snippets, and CLI commands."),
        ("04-documentation-implications.md", "Documentation Implications", "Gaps in official docs, quick start improvements."),
        ("05-product-recommendations.md", "Product Recommendations", "API cleanups, packaging updates, and validation script design."),
    ],
}

def slugify(text: str) -> str:
    """Generate a clean URL/folder-safe slug from a string."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

def cmd_init(args: argparse.Namespace) -> int:
    topic = args.topic
    bundle = args.bundle
    use_case = args.use_case or topic
    audience = args.audience
    depth = args.depth
    no_expansion = args.no_expansion

    # Determine out directory
    if args.out:
        out_dir = Path(args.out)
    else:
        slug = slugify(topic)
        out_dir = Path(f"research/{slug}")

    # Ensure out directory exists
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Error: Failed to create directory {out_dir}: {e}", file=sys.stderr)
        return 1

    # Format anchors list
    anchor_list = []
    if args.anchors:
        for a in args.anchors.split(","):
            a = a.strip()
            if a:
                if a.startswith("http"):
                    anchor_list.append(f"- [{a}]({a})")
                else:
                    # Preserve local anchors as caller-supplied paths instead of
                    # embedding workstation-specific file:// URLs. The generated
                    # research bundle should be portable across machines and
                    # harnesses.
                    anchor_list.append(f"- `{a}`")

    anchors_md = "\n".join(anchor_list) if anchor_list else "- *No initial anchors specified.*"

    # Set up freedom message
    if no_expansion:
        freedom_md = "Strict anchor confinement. Do NOT search the web or inspect files outside the provided anchors."
    else:
        freedom_md = "Use the anchors first, then investigate beyond them where useful to resolve gaps, validate claims, discover better sources, or improve synthesis."

    # Identify reports to produce
    reports_to_create = BUNDLE_REPORTS.get(bundle, BUNDLE_REPORTS["standard"])

    # 1. Create AGY_RESEARCH_BRIEF.md
    brief_path = out_dir / "AGY_RESEARCH_BRIEF.md"
    reports_md = "\n".join([f"- `REPORTS/{filename}`" for filename, _, _ in reports_to_create])

    brief_content = f"""# AGY Research Brief: {topic}

## Objective
Research {topic} to support the following objective: {use_case}.

## Downstream Use
- {use_case}

## Audience
- {audience}

## Known Context / Anchors
- **Local files/repos:**
{anchors_md}

## Freedom to Investigate
{freedom_md}

## Report Bundle Spec
- **Type:** {bundle}
- **Depth:** {depth}

## Required Artifacts
The following reports must be generated in the `REPORTS/` subdirectory:
{reports_md}

## Quality Bar
- Every major claim must reference the Evidence Ledger (e.g., `[Ledger #L01]`).
- Separate facts from interpretations and recommendations.
- Label all findings with a confidence rating: `[HIGH]`, `[MEDIUM]`, or `[LOW]`.
"""

    # 2. Create SOURCE_MAP.md
    source_map_path = out_dir / "SOURCE_MAP.md"
    source_map_rows = []
    if anchor_list:
        for idx, anchor in enumerate(anchor_list, start=1):
            source_map_rows.append(f"| S{idx:02d} | {anchor[2:]} | Anchor Source | High | High | Initial input anchor |")
    else:
        source_map_rows.append("| S01 | N/A | Local/Web | Low | Low | Placeholder source |")

    source_map_content = f"""# Source Map

This file tracks all sources utilized during this research cycle.

| ID | Source/Path/URL | Type | Relevance | Confidence | Notes |
|---|---|---|---|---|---|
{"\n".join(source_map_rows)}
"""

    # 3. Create EVIDENCE_LEDGER.md
    evidence_ledger_path = out_dir / "EVIDENCE_LEDGER.md"
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    evidence_ledger_content = f"""# Evidence Ledger

Every factual claim in the final reports must link back to an ID in this ledger.

| ID | Claim / Finding | Source | Confidence | Rationale | Date/Timestamp |
|---|---|---|---|---|---|
| L01 | Example placeholder finding | [Source Map #S01] | HIGH (Verified) | Verified via initial anchor audit | {current_date} |
"""

    # 4. Create REPORTS/ directory and individual report placeholders
    reports_dir = out_dir / "REPORTS"
    reports_dir.mkdir(exist_ok=True)

    for filename, title, desc in reports_to_create:
        report_path = reports_dir / filename

        # Add YAML frontmatter for content-engine or standard structure
        frontmatter = ""
        if bundle == "content-engine":
            frontmatter = f"""---
title: "{title}: {topic}"
seo_keywords: []
meta_description: "Research draft for {title} on {topic}."
unique_id: "{slugify(topic)}-{slugify(title)}"
---

"""

        report_content = f"""{frontmatter}# {title}: {topic}

<!--
DIRECTIONS FOR AGY:
- {desc}
- Research the topic thoroughly following the AGY Research Brief directives.
- Cite your findings using Evidence Ledger IDs (e.g. [Ledger #L01]).
-->

## Executive Summary of {title}
*Provide summary here.*

## Key Findings & Detailed Analysis
*Provide findings, tables, or analysis.*

## Recommendations & Downstream Actions
*Provide actions, next steps, and confidence margins.*
"""
        try:
            report_path.write_text(report_content, encoding="utf-8")
        except Exception as e:
            print(f"Error: Failed to write report file {report_path}: {e}", file=sys.stderr)
            return 1

    # 5. Create AGY_LAUNCH_COMMANDS.md
    launch_commands_path = out_dir / "AGY_LAUNCH_COMMANDS.md"
    brief_ref = brief_path.as_posix()
    out_ref = out_dir.as_posix()

    launch_commands_content = f"""# AGY Launch Commands

Use these commands to invoke AGY for this research task.

### 1. Pure Research Mode (Read-Only)
```bash
agy --print "Read {brief_ref}. Search external sources and populate {out_ref}."
```

### 2. Repo-Anchored Audit Mode
```bash
agy --print --add-dir "$(pwd)" "Read {brief_ref}. Audit local files and compile reports under {out_ref}."
```

### 3. Interactive Research Mode
```bash
agy --prompt-interactive --add-dir "$(pwd)" "Read {brief_ref}"
```
"""

    try:
        brief_path.write_text(brief_content, encoding="utf-8")
        source_map_path.write_text(source_map_content, encoding="utf-8")
        evidence_ledger_path.write_text(evidence_ledger_content, encoding="utf-8")
        launch_commands_path.write_text(launch_commands_content, encoding="utf-8")
    except Exception as e:
        print(f"Error: Failed to write metadata files: {e}", file=sys.stderr)
        return 1

    print(f"✓ Initialized research workspace under {out_dir}/")
    print(f"  Brief:           {brief_path}")
    print(f"  Source Map:      {source_map_path}")
    print(f"  Evidence Ledger: {evidence_ledger_path}")
    print(f"  Launch Commands: {launch_commands_path}")
    print(f"  Scaffolded {len(reports_to_create)} reports under REPORTS/")
    return 0

def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prismatic-research",
        description="Prismatic Research — CLI to initialize AGY-ready research folders and task assets",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a research workspace")
    init_parser.add_argument("--topic", "-t", required=True, help="Topic of the research")
    init_parser.add_argument(
        "--bundle", "-b",
        default="standard",
        choices=["brief", "standard", "deep", "architecture", "content-engine", "competitive", "golden-path"],
        help="Target report bundle format"
    )
    init_parser.add_argument("--use-case", "-u", help="Downstream use case / objective")
    init_parser.add_argument("--out", "-o", help="Target output directory (default: research/<slug>)")
    init_parser.add_argument("--audience", "-a", default="Engineering Team", help="Target audience of the report")
    init_parser.add_argument("--anchors", default="", help="Comma-separated file paths or URLs to seed research")
    init_parser.add_argument(
        "--depth",
        default="standard",
        choices=["quick", "standard", "deep"],
        help="Research verification depth"
    )
    init_parser.add_argument("--no-expansion", action="store_true", help="Disable external search expansion")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "init":
        return cmd_init(args)

    parser.print_help()
    return 0

def main() -> None:
    sys.exit(run())

if __name__ == "__main__":
    main()
