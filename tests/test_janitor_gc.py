"""Earned-autonomy Phase 5: janitor branch GC tests.

All git work happens in tmp_path repos; the real repo is never touched.
The ``gh`` CLI is stubbed out via monkeypatch so open-PR checks are
deterministic (no network, no auth).
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import prismatic.worktree_janitor as wj
from prismatic.worktree_janitor import (
    BranchCandidate,
    JanitorManifestStore,
    Mode,
    collect_gc_candidates,
    execute_janitor,
    is_release_branch,
    janitor_main,
)


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        env=merged_env,
    )
    return result.stdout.strip()


def _backdate_env(days: int) -> dict[str, str]:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S +0000"
    )
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def _commit_on_branch(
    repo: Path, branch: str, filename: str, days_old: int = 0
) -> None:
    _git(repo, "checkout", "-b", branch)
    (repo / filename).write_text(f"{branch}\n")
    _git(repo, "add", filename)
    env = _backdate_env(days_old) if days_old else None
    _git(repo, "commit", "-m", branch, env=env)


def _merge_into_main(repo: Path, branch: str) -> None:
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", branch, "-m", f"merge {branch}")
    _git(repo, "push", "origin", "main")


@pytest.fixture()
def gc_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo with main + a local origin and one branch per GC state."""
    # Deterministic open-PR checks: no gh calls, no network.
    monkeypatch.setattr(wj, "_open_pr_branch_names", lambda repo_root: set())
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "PE Test")
    (repo / "README.md").write_text("root\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")

    _git(tmp_path, "init", "--bare", "origin.git")
    _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(repo, "push", "origin", "main")

    _commit_on_branch(repo, "merged-old", "old.txt", days_old=10)
    _merge_into_main(repo, "merged-old")

    _commit_on_branch(repo, "merged-recent", "recent.txt")
    _merge_into_main(repo, "merged-recent")

    _commit_on_branch(repo, "unmerged", "unmerged.txt")
    _git(repo, "checkout", "main")

    _commit_on_branch(repo, "release/1.0", "release.txt", days_old=10)
    _merge_into_main(repo, "release/1.0")

    _commit_on_branch(repo, "wip", "wip.txt", days_old=10)
    _merge_into_main(repo, "wip")
    _git(repo, "checkout", "wip")
    return repo


def _by_name(candidates: list[BranchCandidate]) -> dict[str, BranchCandidate]:
    return {c.branch: c for c in candidates}


def _local_branches(repo: Path) -> list[str]:
    return _git(
        repo, "for-each-ref", "--format=%(refname:short)", "refs/heads"
    ).splitlines()


def test_collect_gc_candidates_classifies_branches(gc_repo: Path) -> None:
    _git(gc_repo, "checkout", "wip")
    cands = _by_name(collect_gc_candidates(gc_repo))

    assert cands["merged-old"].eligible is True
    assert cands["merged-old"].reason == "eligible: merged, aged, clean"
    assert cands["merged-old"].tip_age_days > 7

    assert cands["merged-recent"].eligible is False
    assert cands["merged-recent"].reason == "merged but inside 7-day grace"

    assert cands["unmerged"].eligible is False
    assert cands["unmerged"].reason == "unmerged into origin/main"

    assert cands["main"].eligible is False
    assert cands["main"].reason == "protected: main"

    assert cands["wip"].eligible is False
    assert cands["wip"].reason == "protected: current branch"

    assert cands["release/1.0"].eligible is False
    assert cands["release/1.0"].reason == "protected: release branch"


def test_open_pr_protects_branch(
    gc_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wj, "_open_pr_branch_names", lambda repo_root: {"merged-old"})
    cands = _by_name(collect_gc_candidates(gc_repo))

    assert cands["merged-old"].eligible is False
    assert cands["merged-old"].reason == "protected: open PR"


def test_open_pr_unknown_fails_closed(
    gc_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wj, "_open_pr_branch_names", lambda repo_root: None)
    cands = collect_gc_candidates(gc_repo)

    assert cands
    assert all(not c.eligible for c in cands)
    by_name = _by_name(cands)
    assert by_name["merged-old"].reason == "open-PR status unknown (fail-closed)"


def test_dirty_worktree_skips_branch(gc_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt-old"
    _git(gc_repo, "worktree", "add", str(wt), "merged-old")
    (wt / "dirty.txt").write_text("dirty\n")

    cands = _by_name(collect_gc_candidates(gc_repo))

    assert cands["merged-old"].eligible is False
    assert cands["merged-old"].reason == "dirty worktree"


class _FakeLedger:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def events(self) -> list[dict]:
        return self._events


def test_ledger_gate_requires_clean_merge_record(gc_repo: Path) -> None:
    ledger = _FakeLedger(
        [{"event_type": "merge", "event_data": {"artifact_id": "merged-old"}}]
    )
    cands = _by_name(collect_gc_candidates(gc_repo, trust_ledger=ledger))

    assert cands["merged-old"].eligible is True


def test_ledger_gate_skips_without_record(gc_repo: Path) -> None:
    ledger = _FakeLedger(
        [{"event_type": "merge", "event_data": {"artifact_id": "something-else"}}]
    )
    cands = _by_name(collect_gc_candidates(gc_repo, trust_ledger=ledger))

    assert cands["merged-old"].eligible is False
    assert cands["merged-old"].reason == "no ledger record of clean merge"


def test_ledger_gate_lenient_mention_counts(gc_repo: Path) -> None:
    ledger = _FakeLedger(
        [
            {
                "event_type": "note",
                "event_data": {"notes": "merged-old looks clean, ship it"},
            }
        ]
    )
    cands = _by_name(collect_gc_candidates(gc_repo, trust_ledger=ledger))

    assert cands["merged-old"].eligible is True


def test_no_ledger_means_git_only_checks(gc_repo: Path) -> None:
    cands = _by_name(collect_gc_candidates(gc_repo, trust_ledger=None))

    assert cands["merged-old"].eligible is True


def test_apply_mode_deletes_only_eligible(gc_repo: Path) -> None:
    result = execute_janitor(Mode.Apply, gc_repo)

    assert result["mode"] == "apply"
    assert result["deleted"] == ["merged-old"]
    remaining = _local_branches(gc_repo)
    assert "merged-old" not in remaining
    for kept in ("main", "merged-recent", "unmerged", "release/1.0", "wip"):
        assert kept in remaining
    skipped = {item["branch"]: item["reason"] for item in result["skipped"]}
    assert skipped["merged-recent"] == "merged but inside 7-day grace"
    assert skipped["unmerged"] == "unmerged into origin/main"
    assert skipped["release/1.0"] == "protected: release branch"


def test_report_only_deletes_nothing(gc_repo: Path) -> None:
    result = execute_janitor(Mode.ReportOnly, gc_repo)

    assert result["deleted"] == []
    assert "merged-old" in _local_branches(gc_repo)
    manifest = Path(result["manifest_path"])
    assert manifest.exists()
    entries = [json.loads(line) for line in manifest.read_text().splitlines()]
    old = next(e for e in entries if e["branch"] == "merged-old")
    assert old["action"] == "gc-skipped"
    assert old["reason"] == "report-only mode: no deletions"


def test_manifest_records_apply_decisions(gc_repo: Path) -> None:
    result = execute_janitor(Mode.Apply, gc_repo)

    entries = {}
    for line in Path(result["manifest_path"]).read_text().splitlines():
        entry = json.loads(line)
        entries[entry["branch"]] = entry

    assert entries["merged-old"]["action"] == "gc-deleted"
    assert entries["merged-recent"]["action"] == "gc-skipped"
    assert entries["merged-recent"]["reason"] == "merged but inside 7-day grace"
    assert entries["unmerged"]["reason"] == "unmerged into origin/main"
    for entry in entries.values():
        assert {"ts", "repo", "branch", "action", "mode"} <= set(entry)


def test_manifest_store_path_format(gc_repo: Path) -> None:
    store = JanitorManifestStore(gc_repo)

    path = store.path_for(Mode.Apply)

    assert path.parent.name == "janitor"
    assert path.name == f"{gc_repo.name}-apply.jsonl"
    assert path.suffix == ".jsonl"


def test_is_release_branch() -> None:
    assert is_release_branch("release")
    assert is_release_branch("release/1.2")
    assert not is_release_branch("main")
    assert not is_release_branch("feature/release-notes")


def test_ignored_branches_file_protects(gc_repo: Path) -> None:
    ignore_dir = gc_repo / ".prismatic" / "janitor"
    ignore_dir.mkdir(parents=True, exist_ok=True)
    (ignore_dir / "ignored-branches.txt").write_text("# keep this\nmerged-old\n")

    cands = _by_name(collect_gc_candidates(gc_repo))

    assert cands["merged-old"].eligible is False
    assert cands["merged-old"].reason == "protected: ignored"


def test_protected_branches_kwarg(gc_repo: Path) -> None:
    cands = _by_name(
        collect_gc_candidates(gc_repo, protected_branches=frozenset({"merged-old"}))
    )

    assert cands["merged-old"].eligible is False
    assert cands["merged-old"].reason == "protected: ignored"


def test_janitor_main_report_only_smoke(
    gc_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    code = janitor_main(["--mode", "report-only", "--repo", str(gc_repo)])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "report-only"
    assert payload["deleted"] == []
    assert "merged-old" in _local_branches(gc_repo)


def test_branch_candidate_is_frozen() -> None:
    candidate = BranchCandidate(branch="x", eligible=True, reason="ok")

    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.eligible = False  # type: ignore[misc]
