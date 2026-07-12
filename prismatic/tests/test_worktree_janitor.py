from __future__ import annotations

import json
import subprocess
from pathlib import Path

from prismatic.core_crons import emit
from prismatic.worktree_janitor import list_worktrees, run_janitor


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "PE Test")
    (repo / "README.md").write_text("root\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_list_worktrees_reports_registered_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-clean"
    _git(repo, "worktree", "add", str(wt), "HEAD")

    records = list_worktrees(repo, base_ref="main")

    assert {Path(r.path).name for r in records} == {"repo", "wt-clean"}
    assert any(not r.dirty and r.merged_to_base for r in records if Path(r.path) == wt)


def test_janitor_dry_run_plans_without_removing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-clean"
    _git(repo, "worktree", "add", str(wt), "HEAD")

    result = run_janitor(repo, base_ref="main", dry_run=True, stale_seconds=0)

    assert result.dry_run is True
    assert any(Path(item["path"]) == wt for item in result.removable)
    assert wt.exists()


def test_janitor_archives_dirty_worktree_before_removal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-dirty"
    archive = tmp_path / "archive"
    _git(repo, "worktree", "add", str(wt), "HEAD")
    (wt / "scratch.txt").write_text("dirty\n")

    result = run_janitor(
        repo,
        base_ref="main",
        archive_dir=archive,
        dry_run=False,
        include_dirty=True,
        stale_seconds=0,
    )

    assert not wt.exists()
    assert result.removed
    archived = Path(result.removed[0]["archive"])
    assert (archived / "status.txt").exists()
    assert (archived / "untracked-files.txt").read_text().strip() == "scratch.txt"


def test_core_crons_emit_portable_manifest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    crontab = emit(str(repo), fmt="crontab")
    manifest = json.loads(emit(str(repo), fmt="json"))

    assert "prismatic worktrees janitor" in crontab
    assert "PRISMATIC_REPO_DIR=" in crontab
    assert manifest[0]["id"] == "prismatic.worktree-janitor.hourly"
