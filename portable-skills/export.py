#!/usr/bin/env python3
"""
Prismatic Engine: Portable Skills Export Script
Copies the 19 selected skills from the orchestrator profile to the shareable bundle,
including references, templates, and scripts, and writes the quality metadata.
"""

import os
import shutil
import yaml

SOURCE_DIR = "/home/ubuntu/.hermes/profiles/orchestrator/skills"
TARGET_DIR = "/home/ubuntu/work/prismatic-engine/portable-skills"

SKILLS_CONFIG = {
    "orchestrator-delegation-discipline": {
        "source_subpath": "agent-orchestration/orchestrator-delegation-discipline",
        "quality": {
            "rating": 4,
            "dependencies": ["autonomous-execution-discipline"],
            "compatibility_notes": "Compatible with Linux and macOS. Requires subagent execution capability."
        }
    },
    "autonomous-execution-discipline": {
        "source_subpath": "agent-orchestration/autonomous-execution-discipline",
        "quality": {
            "rating": 5,
            "dependencies": ["orchestrator-delegation-discipline", "golden-thread"],
            "compatibility_notes": "Requires a persistent task daemon or cron system. Script nudge_detector.py requires python3."
        }
    },
    "golden-thread": {
        "source_subpath": "orchestration/golden-thread",
        "quality": {
            "rating": 5,
            "dependencies": ["autonomous-execution-discipline", "linear-api-mcp"],
            "compatibility_notes": "Highly compatible with Linux/macOS. Requires Linear API access for issue tracking."
        }
    },
    "static-site-seo-fix": {
        "source_subpath": "engineering/static-site-seo-fix",
        "quality": {
            "rating": 4,
            "dependencies": ["find", "grep", "python3"],
            "compatibility_notes": "Runs on bash-compatible terminals. Highly portable."
        }
    },
    "systematic-debugging": {
        "source_subpath": "software-development/systematic-debugging",
        "quality": {
            "rating": 4,
            "dependencies": [],
            "compatibility_notes": "A pure methodological/guideline skill. 100% compatible with Linux, macOS, and Windows."
        }
    },
    "github-pr-workflow": {
        "source_subpath": "github/github-pr-workflow",
        "quality": {
            "rating": 5,
            "dependencies": ["git", "gh", "github-auth"],
            "compatibility_notes": "Requires GitHub authorization token if utilizing the API fallback. Works on Linux, macOS, and Windows."
        }
    },
    "himalaya": {
        "source_subpath": "email/himalaya",
        "quality": {
            "rating": 4,
            "dependencies": ["himalaya"],
            "compatibility_notes": "Requires the himalaya binary in the system PATH. Works across Linux, macOS, and Windows."
        }
    },
    "linear": {
        "source_subpath": "productivity/linear",
        "quality": {
            "rating": 5,
            "dependencies": ["curl", "python3"],
            "compatibility_notes": "Requires LINEAR_API_KEY env var. Works on any platform with curl and Python."
        }
    },
    "agy-oauth-authentication": {
        "source_subpath": "agent-orchestration/agy-oauth-authentication",
        "quality": {
            "rating": 4,
            "dependencies": ["agy"],
            "compatibility_notes": "Requires Google Antigravity CLI (agy). OAuth browser flow needs desktop environment."
        }
    },
    "agent-ned": {
        "source_subpath": "agent-orchestration/agent-ned",
        "quality": {
            "rating": 5,
            "dependencies": ["linear", "git", "python3"],
            "compatibility_notes": "Primary executor agent. Requires Linear API, git, and Python 3.10+."
        }
    },
    "cloudflare-deployment": {
        "source_subpath": "infrastructure/cloudflare-deployment",
        "quality": {
            "rating": 4,
            "dependencies": ["wrangler", "node"],
            "compatibility_notes": "Requires Cloudflare Wrangler CLI and Node.js."
        }
    },
    "credential-security-and-git-hygiene": {
        "source_subpath": "engineering/credential-security-and-git-hygiene",
        "quality": {
            "rating": 5,
            "dependencies": ["git", "gitleaks"],
            "compatibility_notes": "Cross-platform. Requires gitleaks for secret scanning."
        }
    },
    "expert-interview-content-production": {
        "source_subpath": "content-strategy/expert-interview-content-production",
        "quality": {
            "rating": 4,
            "dependencies": ["pandoc", "python3"],
            "compatibility_notes": "Requires pandoc for PDF conversion. Works on Linux/macOS."
        }
    },
    "daily-transit-briefing": {
        "source_subpath": "human-design/daily-transit-briefing",
        "quality": {
            "rating": 4,
            "dependencies": ["python3"],
            "compatibility_notes": "Requires Human Design computation engine. Linux/macOS compatible."
        }
    },
    "human-design-computation": {
        "source_subpath": "human-design/human-design-computation",
        "quality": {
            "rating": 5,
            "dependencies": ["python3", "swisseph"],
            "compatibility_notes": "Requires Swiss Ephemeris library. Linux/macOS compatible."
        }
    },
    "next-step-bot": {
        "source_subpath": "next-step-bot",
        "quality": {
            "rating": 4,
            "dependencies": ["python3", "telegram"],
            "compatibility_notes": "Requires Telegram Bot API token. Linux/macOS compatible."
        }
    },
    "aot-agent-coordination": {
        "source_subpath": "content-strategy/aot-agent-coordination",
        "quality": {
            "rating": 4,
            "dependencies": [],
            "compatibility_notes": "Coordination protocol skill. Pure documentation — 100% portable."
        }
    },
    "prismatic-agent-factory": {
        "source_subpath": "agent-orchestration/prismatic-agent-factory",
        "quality": {
            "rating": 4,
            "dependencies": ["python3"],
            "compatibility_notes": "Reusable agent factory pattern. Linux/macOS compatible."
        }
    },
    "kai-css-agent": {
        "source_subpath": "agent-orchestration/kai-css-agent",
        "quality": {
            "rating": 4,
            "dependencies": [],
            "compatibility_notes": "CSS/styling specialist agent skill. Pure documentation — 100% portable."
        }
    }
}

def export_skills():
    print("Starting Portable Skills Export...")
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)
        print(f"Created target directory: {TARGET_DIR}")

    for skill_name, config in SKILLS_CONFIG.items():
        src_path = os.path.join(SOURCE_DIR, config["source_subpath"])
        dest_path = os.path.join(TARGET_DIR, skill_name)

        print(f"\nProcessing skill: {skill_name}")
        if not os.path.exists(src_path):
            print(f"  [ERROR] Source path does not exist: {src_path}")
            continue

        # Clean existing dest folder to ensure fresh export
        if os.path.exists(dest_path):
            shutil.rmtree(dest_path)
            print(f"  Removed existing target directory: {dest_path}")
        os.makedirs(dest_path)

        # 1. Copy SKILL.md
        src_skill_md = os.path.join(src_path, "SKILL.md")
        if os.path.exists(src_skill_md):
            shutil.copy2(src_skill_md, os.path.join(dest_path, "SKILL.md"))
            print("  Copied SKILL.md")
        else:
            print("  [WARNING] SKILL.md not found in source!")

        # 2. Copy subdirectories (references, templates, scripts)
        for folder in ["references", "templates", "scripts"]:
            sub_src = os.path.join(src_path, folder)
            if os.path.exists(sub_src):
                sub_dest = os.path.join(dest_path, folder)
                shutil.copytree(sub_src, sub_dest)
                print(f"  Bundled {folder}/")

        # 3. Write SKILL_QUALITY.yaml
        quality_file = os.path.join(dest_path, "SKILL_QUALITY.yaml")
        with open(quality_file, "w", encoding="utf-8") as f:
            yaml.dump(config["quality"], f, default_flow_style=False, sort_keys=False)
        print("  Created SKILL_QUALITY.yaml")

    print("\nExport completed successfully!")

if __name__ == "__main__":
    export_skills()
