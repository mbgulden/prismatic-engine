from __future__ import annotations

import json
import subprocess
from pathlib import Path

from prismatic.core_crons import emit
from prismatic.worktree_janitor import (
    list_worktrees,
    run_janitor,
    worktree_proof_template,
)


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


def test_janitor_archives_dirty_worktree_but_refuses_removal_without_token(tmp_path: Path) -> None:
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

    assert wt.exists()
    assert result.removed
    archived = Path(result.removed[0]["archive"])
    assert (archived / "status.txt").exists()
    assert (archived / "untracked-files.txt").read_text().strip() == "scratch.txt"
    assert result.removed[0]["remove_returncode"] is None
    assert "confirm_dirty_token" in result.removed[0]["remove_stderr"]


def test_dirty_worktree_can_only_be_removed_with_exact_token(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-dirty-confirmed"
    archive = tmp_path / "archive"
    _git(repo, "worktree", "add", str(wt), "HEAD")
    (wt / "scratch.txt").write_text("dirty\n")

    token = "DELETE-DIRTY-WORKTREES:main"
    result = run_janitor(
        repo,
        base_ref="main",
        archive_dir=archive,
        dry_run=False,
        include_dirty=True,
        confirm_dirty_token=token,
        stale_seconds=0,
    )

    assert not wt.exists()
    assert result.dirty_confirm_token == token
    assert Path(result.manifest_path).exists()
    assert result.removed[0]["safety_class"] == "manual-review"


def test_unmerged_worktree_is_never_planned_for_removal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "README.md").write_text("main\n")
    _git(repo, "commit", "-am", "main change")
    _git(repo, "branch", "feature", "HEAD~1")
    wt = tmp_path / "wt-conflict"
    _git(repo, "worktree", "add", str(wt), "feature")
    (wt / "README.md").write_text("feature\n")
    _git(wt, "commit", "-am", "feature change")
    subprocess.run(["git", "merge", "main"], cwd=wt, text=True, capture_output=True)

    result = run_janitor(repo, base_ref="main", dry_run=True, include_dirty=True, stale_seconds=0)

    assert any(Path(item["path"]) == wt and item["safety_class"] == "manual-review" for item in result.kept)
    assert all(Path(item["path"]) != wt for item in result.removable)


def test_clean_unmerged_ahead_worktree_is_kept(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-ahead"
    _git(repo, "worktree", "add", str(wt), "-b", "feature", "HEAD")
    (wt / "feature.txt").write_text("valuable work\n")
    _git(wt, "add", "feature.txt")
    _git(wt, "commit", "-m", "valuable work")

    result = run_janitor(repo, base_ref="main", dry_run=True, stale_seconds=0)

    assert any(Path(item["path"]) == wt and item["safety_class"] == "keep" for item in result.kept)
    assert all(Path(item["path"]) != wt for item in result.removable)


def test_dirty_worktree_is_preserved_and_marked_missing_proof(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-undocumented-good-work"
    _git(repo, "worktree", "add", str(wt), "HEAD")
    (wt / "scratch.txt").write_text("valuable but undocumented\n")

    records = list_worktrees(repo, base_ref="main", stale_seconds=0)
    record = next(r for r in records if Path(r.path) == wt)

    assert record.safety_class == "manual-review"
    assert record.value_class == "preserve-needs-proof"
    assert "missing portable worktree proof file" in record.proof_gaps
    assert record.promotion_recommendation == "capture-proof-or-promote"


def test_proof_file_promotes_useful_work_to_indispensable(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "GRO-9999-useful-work"
    _git(repo, "worktree", "add", str(wt), "-b", "GRO-9999-useful-work", "HEAD")
    proof_dir = wt / ".prismatic"
    proof_dir.mkdir()
    proof = worktree_proof_template(issue="GRO-9999", summary="Useful agent work")
    proof["verdict"] = "indispensable"
    proof["verification"] = [{"command": "pytest focused", "result": "passed"}]
    (proof_dir / "worktree-proof.json").write_text(json.dumps(proof))
    (wt / "feature.txt").write_text("valuable committed work\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "valuable work")

    records = list_worktrees(repo, base_ref="main", stale_seconds=0)
    record = next(r for r in records if Path(r.path) == wt)

    assert record.safety_class == "keep"
    assert record.value_class == "indispensable"
    assert any("proof verdict: indispensable" in signal for signal in record.value_signals)
    assert record.promotion_recommendation == "promote"


def test_proof_template_has_portable_required_fields() -> None:
    proof = worktree_proof_template(issue="GRO-1234", summary="Summary")

    assert proof["schema"] == "prismatic.worktree-proof.v1"
    assert proof["issue"] == "GRO-1234"
    assert proof["verification"] == []
    assert "handoff" in proof


def test_core_crons_emit_portable_manifest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    crontab = emit(str(repo), fmt="crontab")
    manifest = json.loads(emit(str(repo), fmt="json"))

    assert "prismatic worktrees janitor" in crontab
    assert "--include-dirty" not in crontab
    assert "PRISMATIC_REPO_DIR=" in crontab
    assert manifest[0]["id"] == "prismatic.worktree-janitor.hourly"
