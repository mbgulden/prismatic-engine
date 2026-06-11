#!/usr/bin/env python3
"""
Prismatic Engine: Portable Skills Export Script
Copies the 7 selected skills from the orchestrator profile to the shareable bundle,
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
