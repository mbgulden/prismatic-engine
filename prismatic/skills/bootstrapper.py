"""prismatic.skills.bootstrapper — Built-In Skill Bootstrapper & Package Distribution Engine.

Bootstraps governance rules (.agents/AGENTS.md) and executable skills (.agents/skills/) into
any target workspace repository, asserting Dual-Tree hash equality between .agents/skills/
and prismatic/skills/.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class SkillSyncResult:
    marker: str
    status: str
    target_workspace: str
    agents_rules_synced: bool
    skills_synced_count: int
    dual_tree_matched: bool
    skill_tree_sha256: str

    def to_dict(self) -> dict:
        return {
            "marker": self.marker,
            "status": self.status,
            "target_workspace": self.target_workspace,
            "agents_rules_synced": self.agents_rules_synced,
            "skills_synced_count": self.skills_synced_count,
            "dual_tree_matched": self.dual_tree_matched,
            "skill_tree_sha256": self.skill_tree_sha256,
        }


def _compute_dir_hash(dir_path: Path) -> str:
    """Compute deterministic SHA-256 hash over directory file contents."""
    if not dir_path.exists():
        return ""
    hasher = hashlib.sha256()
    for path in sorted(dir_path.rglob("*")):
        if path.is_file():
            rel_path = path.relative_to(dir_path).as_posix()
            hasher.update(rel_path.encode("utf-8"))
            hasher.update(path.read_bytes())
    return hasher.hexdigest().upper()


def _safe_copy_file(src: Path, dst: Path) -> None:
    if src.exists() and src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _safe_copy_tree(src: Path, dst: Path) -> None:
    if src.exists() and src.resolve() == dst.resolve():
        return
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


class SkillBootstrapper:
    def __init__(self, source_root: Optional[Path] = None) -> None:
        self.source_root = (source_root or Path.cwd()).resolve()

    def get_source_agents_dir(self) -> Path:
        agents_dir = self.source_root / ".agents"
        if agents_dir.exists():
            return agents_dir
        raise FileNotFoundError(f"Source .agents directory not found at {agents_dir}")

    def sync_skills(self, target_workspace: Optional[Path] = None, force: bool = False) -> SkillSyncResult:
        target_dir = (target_workspace or Path.cwd()).resolve()
        source_agents = self.get_source_agents_dir()

        target_agents = target_dir / ".agents"
        target_prismatic_skills = target_dir / "prismatic" / "skills"

        target_agents.mkdir(parents=True, exist_ok=True)
        target_prismatic_skills.mkdir(parents=True, exist_ok=True)

        # 1. Sync AGENTS.md
        source_agents_md = source_agents / "AGENTS.md"
        rules_synced = False
        if source_agents_md.exists():
            _safe_copy_file(source_agents_md, target_agents / "AGENTS.md")
            rules_synced = True

        # 2. Sync skills to .agents/skills/ and prismatic/skills/
        source_skills_dir = source_agents / "skills"
        skills_count = 0
        if source_skills_dir.exists():
            for skill_path in source_skills_dir.iterdir():
                if skill_path.is_dir():
                    dest_agent_skill = target_agents / "skills" / skill_path.name
                    dest_prismatic_skill = target_prismatic_skills / skill_path.name

                    _safe_copy_tree(skill_path, dest_agent_skill)
                    _safe_copy_tree(skill_path, dest_prismatic_skill)
                    skills_count += 1

        # 3. Compute Dual-Tree Hashes
        agent_skills_hash = _compute_dir_hash(target_agents / "skills")
        dual_tree_matched = skills_count > 0
        if source_skills_dir.exists():
            for skill_path in source_skills_dir.iterdir():
                if skill_path.is_dir():
                    h_agent = _compute_dir_hash(target_agents / "skills" / skill_path.name)
                    h_prismatic = _compute_dir_hash(target_prismatic_skills / skill_path.name)
                    if h_agent != h_prismatic or not h_agent:
                        dual_tree_matched = False
                        break

        status = "PASS" if rules_synced and dual_tree_matched else "FAIL"
        marker = "PE_SKILLS_SYNC_VERIFIED_OK" if status == "PASS" else "PE_SKILLS_SYNC_FAILED"

        return SkillSyncResult(
            marker=marker,
            status=status,
            target_workspace=str(target_dir),
            agents_rules_synced=rules_synced,
            skills_synced_count=skills_count,
            dual_tree_matched=dual_tree_matched,
            skill_tree_sha256=agent_skills_hash,
        )
