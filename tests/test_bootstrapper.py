"""Tests for Built-In Skill Bootstrapper & Package Distribution."""

from __future__ import annotations

from pathlib import Path
import pytest

from prismatic.skills.bootstrapper import SkillBootstrapper, _compute_dir_hash


def test_skill_sync_creates_dot_agents_and_prismatic_skills(tmp_path: Path) -> None:
    # Setup dummy source workspace
    source_dir = tmp_path / "source"
    agents_dir = source_dir / ".agents"
    agents_skills = agents_dir / "skills" / "test-skill"
    agents_skills.mkdir(parents=True)
    (agents_dir / "AGENTS.md").write_text("# Test Rules\n", encoding="utf-8")
    (agents_skills / "SKILL.md").write_text("---\nname: test-skill\ndescription: Test\n---\n", encoding="utf-8")

    # Target workspace
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    bootstrapper = SkillBootstrapper(source_root=source_dir)
    res = bootstrapper.sync_skills(target_workspace=target_dir)

    assert res.status == "PASS"
    assert res.marker == "PE_SKILLS_SYNC_VERIFIED_OK"
    assert res.agents_rules_synced is True
    assert res.skills_synced_count == 1
    assert res.dual_tree_matched is True
    assert (target_dir / ".agents" / "AGENTS.md").exists()
    assert (target_dir / ".agents" / "skills" / "test-skill" / "SKILL.md").exists()
    assert (target_dir / "prismatic" / "skills" / "test-skill" / "SKILL.md").exists()


def test_compute_dir_hash(tmp_path: Path) -> None:
    d = tmp_path / "hash_dir"
    d.mkdir()
    (d / "f1.txt").write_text("hello", encoding="utf-8")
    h1 = _compute_dir_hash(d)

    (d / "f2.txt").write_text("world", encoding="utf-8")
    h2 = _compute_dir_hash(d)

    assert len(h1) == 64
    assert h1 != h2
