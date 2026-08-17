#!/usr/bin/env python3
"""
pwp_synthesize_generic.py — Generic version of Step 2 that works on any project type.

Like pwp_synthesize.py but adapts the synthesis prompt based on the project type
(game, saas, web, homelab, etc.) and uses the generic profile JSON as input.
"""
import os, sys, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

PROMPTS = {
    "game": """You are a senior game producer and tech lead producing a comprehensive build/iteration plan for a game project.

Read the structured project profile and produce a `website_build_plan.md` (or `next_iteration_plan.md` for games) with the following sections. Be CONCRETE — no generic placeholders.

## Required sections

### 1. Game Architecture (current + target state)
- Current module map (per the AGENTS.md if present)
- Target architecture (per the GDD if present)
- What's blocking the target

### 2. Per-Module / Per-Subsystem Plan
For each major module:
- Current state
- Target state
- Acceptance criteria for "done well"

### 3. Design / UX Spec
- Visual language
- Sound design
- Game feel
- Reference games

### 4. Asset Plan
- Sprites, audio, cinematics
- Generation source (AGY image gen, Lyria, etc.)
- Asset attribution

### 5. Technical Requirements
- Build / deploy pipeline
- Performance targets
- Platform targets (web, mobile, desktop, console)

### 6. Testing / Automation
- Unit tests
- E2E tests
- Performance tests
- CI/CD

### 7. Iteration Sequence
- Recommended order (foundation → content → polish)
- What can run in parallel
- Critical path

### 8. Acceptance Criteria — "Well Thought Out"
- Specific quality bars
- Specific review gates
- Specific tests that must pass

## Quality bar
- >= 2000 words
- Concrete specifics, no generic placeholders
- Every section filled in
- Every module has acceptance criteria

Output ONLY the markdown, no commentary.""",

    "website": """You are a senior web architect producing a comprehensive build plan for a website. (Full prompt as in pwp_synthesize.py)""",

    "saas": """You are a senior SaaS architect producing a comprehensive roadmap for a SaaS platform. Focus on:
1. Current state of the platform
2. Per-feature roadmap
3. API design / contracts
4. Auth + multi-tenancy
5. Billing / metering
6. Performance + reliability targets
7. Migration / upgrade plan
8. Acceptance criteria

Output ONLY the markdown, >= 2000 words.""",

    "web-app": """You are a senior product architect producing a roadmap for a web application. Focus on:
1. Current capabilities
2. User flows
3. Per-feature backlog with priority
4. Design system
5. State management
6. Performance + accessibility
7. Iteration sequence
8. Acceptance criteria

Output ONLY the markdown, >= 2000 words.""",

    "infrastructure": """You are a senior infrastructure architect producing a roadmap for an infrastructure project. Focus on:
1. Current inventory
2. Per-component roadmap
3. Reliability + monitoring
4. Security + compliance
5. Automation / orchestration
6. Cost optimization
7. Disaster recovery
8. Acceptance criteria

Output ONLY the markdown, >= 2000 words.""",

    "knowledge-base": """You are a senior knowledge architect producing a roadmap for a knowledge base. Focus on:
1. Current schema / organization
2. Per-area documentation plan
3. Search / discovery
4. Cross-references / linking
5. Versioning / freshness
6. Contribution model
7. Iteration sequence
8. Acceptance criteria

Output ONLY the markdown, >= 2000 words.""",

    "agent-infra": """You are a senior agent platform architect producing a roadmap for an agent infrastructure. Focus on:
1. Current agents + their roles
2. Per-agent roadmap (capabilities, models, routing)
3. Inter-agent communication
4. Tool ecosystem
5. Observability / debugging
6. Failure handling / recovery
7. Iteration sequence
8. Acceptance criteria

Output ONLY the markdown, >= 2000 words.""",

    "unknown": """You are a senior architect producing a comprehensive build/iteration plan for a project. The project type is "unknown" so use the project description and OKF docs to determine the right focus.

Required sections:
1. Current state (from OKF docs)
2. Target state
3. Per-area plan
4. Design / UX (if applicable)
5. Asset plan (if applicable)
6. Technical requirements
7. Testing / automation
8. Iteration sequence
9. Acceptance criteria — "Well Thought Out"

Output ONLY the markdown, >= 2000 words, no commentary.""",
}

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 pwp_synthesize_generic.py <path-to-profile.json>", file=sys.stderr)
        sys.exit(1)

    profile_path = Path(sys.argv[1]).resolve()
    if not profile_path.is_file():
        print(f"Error: {profile_path} is not a file", file=sys.stderr)
        sys.exit(2)

    print(f"Generic synthesize from: {profile_path}")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    project_type = profile.get("project", {}).get("type", "unknown")
    project_name = profile.get("project", {}).get("name", "Unknown")
    print(f"  Project: {project_name}")
    print(f"  Type: {project_type}")

    prompt_template = PROMPTS.get(project_type, PROMPTS["unknown"])
    prompt = prompt_template.replace("{client_profile}", json.dumps(profile, indent=2))
    # Generic version doesn't have the placeholders — just use the profile directly
    prompt += "\n\n# Project Profile\n\n```json\n" + json.dumps(profile, indent=2) + "\n```\n"

    print(f"\nSynthesizing with AGY pro...")
    result = subprocess.run(
        [
            "/home/ubuntu/.local/bin/agy",
            "--model", "Gemini 3.1 Pro (High)",
            "--prompt", prompt,
            "--print-timeout", "5m0s",
            "--dangerously-skip-permissions",
        ],
        capture_output=True, text=True, timeout=300
    )

    if result.returncode != 0:
        print(f"AGY error: {result.stderr}", file=sys.stderr)
        sys.exit(3)

    output = result.stdout.strip()
    if not output:
        print("AGY returned empty output", file=sys.stderr)
        sys.exit(4)

    output = re.sub(r"^```markdown\s*\n", "", output, flags=re.IGNORECASE)
    output = re.sub(r"^```\s*\n", "", output)
    output = re.sub(r"\n```\s*$", "", output)

    out_path = profile_path.parent / "synthesis.md"
    out_path.write_text(output, encoding="utf-8")
    word_count = len(output.split())
    print(f"\n✓ Wrote: {out_path}")
    print(f"  Word count: {word_count} (target: >= 2000)")
    if word_count < 2000:
        print("  WARNING: short synthesis — may need different model or context")

if __name__ == "__main__":
    main()