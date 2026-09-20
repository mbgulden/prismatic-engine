"""Tests for Universal Skill Discovery and Mandatory Verification Injection."""

from __future__ import annotations

from pathlib import Path
import pytest

from prismatic.skills import get_universal_skills_dirs, list_skills, upload_skill


def test_get_universal_skills_dirs() -> None:
    dirs = get_universal_skills_dirs()
    assert len(dirs) >= 1
    assert any("skills" in str(d) for d in dirs)


def test_list_skills_aggregates_and_marks_installed() -> None:
    skills = list_skills()
    assert len(skills) >= 1
    for skill in skills:
        assert "name" in skill
        assert "installed" in skill
        assert skill["installed"] is True


def test_upload_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agents" / "skills").mkdir(parents=True)

    content = "---\nname: my-custom-skill\ndescription: Custom skill\n---\n"
    res = upload_skill("my-custom-skill", content)

    assert res["name"] == "my-custom-skill"
    assert res["installed"] is True
    assert (tmp_path / ".agents" / "skills" / "my-custom-skill" / "SKILL.md").exists()
