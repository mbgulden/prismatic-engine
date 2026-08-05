#!/usr/bin/env python3
"""
Prismatic Skill Tree Synchronization Script (v0.2)
Maintains single-source-of-truth by syncing .agents/skills/ -> prismatic/skills/
"""

import sys
import shutil
from pathlib import Path

def sync_trees(source_dir: Path, target_dir: Path):
    if target_dir.exists():
        shutil.rmtree(target_dir)

    shutil.copytree(source_dir, target_dir)
    print(f"Copied {source_dir} -> {target_dir}")

    # Rewrite SKILL.md workspace URLs for engine tree
    target_skill_md = target_dir / "SKILL.md"
    if target_skill_md.exists():
        content = target_skill_md.read_text(encoding="utf-8")
        updated = content.replace(
            "file=.agents/skills/prismatic-agent-closeout-contract",
            "file=prismatic/skills/prismatic-agent-closeout-contract"
        )
        target_skill_md.write_text(updated, encoding="utf-8")
        print(f"Rewrote workspace URLs in {target_skill_md}")

def main():
    script_dir = Path(__file__).resolve().parent
    agent_skill = script_dir.parent
    engine_skill = agent_skill.parents[2] / "prismatic" / "skills" / "prismatic-agent-closeout-contract"

    if not agent_skill.exists():
        print(f"Error: Source tree {agent_skill} does not exist.")
        sys.exit(1)

    sync_trees(agent_skill, engine_skill)
    print("STATUS=SYNC_COMPLETE")

if __name__ == "__main__":
    main()
