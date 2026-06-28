"""Tests for prismatic.quality.loop_branch_detector."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prismatic.quality.loop_branch_detector import (
    LoopBranchVerdict,
    _SOURCE_EXTENSIONS,
    classify_all_branches,
    is_loop_branch,
)


# ---------------------------------------------------------------------------
# Helpers — make a tiny throwaway git repo we can poke at.
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Init a git repo with an initial commit on main."""
    _git(tmp_path, "init", "--quiet", "-b", "main")
    _git(tmp_path, "config", "user.email", "ned@example.com")
    _git(tmp_path, "config", "user.name", "Ned")
    # Need a commit on main so HEAD exists.
    (tmp_path / "README.md").write_text("# test repo\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "--quiet", "-m", "initial commit")
    return tmp_path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _make_branch_with_commits(repo: Path, branch: str, messages: list[str], files: dict[str, str] | None = None) -> None:
    _git(repo, "checkout", "--quiet", "-b", branch)
    if files:
        for path, content in files.items():
            p = repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            _git(repo, "add", path)
    for msg in messages:
        _git(repo, "commit", "--quiet", "--allow-empty", "-m", msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_DEFAULT_BRANCH = "main"  # the test repo's only branch


def test_no_commits_ahead_is_not_loop_noise(git_repo: Path) -> None:
    """A branch with no commits ahead of default is not loop-noise."""
    _git(git_repo, "checkout", "--quiet", "-b", "feature/some-work")
    verdict = is_loop_branch(str(git_repo), "feature/some-work", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is False
    assert verdict.commit_count == 0
    assert "no-commits-ahead" in verdict.reasons


def test_feature_work_with_source_files_is_not_loop_noise(git_repo: Path) -> None:
    """A branch that changes a Python file is real work, not loop-noise."""
    _make_branch_with_commits(
        git_repo,
        "feature/real-work",
        messages=["[Ned] GRO-100: implement sanity check"],
        files={"prismatic/foo.py": "def sanity(): return 42\n"},
    )
    verdict = is_loop_branch(str(git_repo), "feature/real-work", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is False
    assert verdict.commit_count == 1
    assert verdict.source_files_changed == 1


def test_triage_note_commits_classified_as_loop_noise(git_repo: Path) -> None:
    """Three 'triage note' commits, no source files, is loop-noise."""
    _make_branch_with_commits(
        git_repo,
        "ned/GRO-506",
        messages=[
            "[Ned] GRO-506: triage note — 16th pass on 10-issue batch",
            "[Ned] GRO-506: triage note — 17th pass on 10-issue batch",
            "[Ned] GRO-506: triage note — 18th pass on 10-issue batch",
        ],
    )
    verdict = is_loop_branch(str(git_repo), "ned/GRO-506", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is True
    assert verdict.commit_count == 3
    assert verdict.source_files_changed == 0
    # At minimum the all-messages-match check should fire.
    assert any("all-messages-match-loop-regex" in r for r in verdict.reasons)


def test_doc_only_changes_classified_as_loop_noise(git_repo: Path) -> None:
    """A branch that ONLY adds markdown files is loop-noise (no source change)."""
    _make_branch_with_commits(
        git_repo,
        "ned/GRO-507",
        messages=[
            "[Ned] GRO-507: infra findings — 7d-cluster outage wider than expected",
            "[Ned] GRO-507: infra findings — growthwebdev apex HTTP 530",
        ],
        files={
            "docs/gro-507-batch-routing-7th-pass-infra-findings.md": "# findings\n",
            "docs/gro-507-batch-routing-8th-pass-infra-findings.md": "# findings\n",
        },
    )
    verdict = is_loop_branch(str(git_repo), "ned/GRO-507", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is True
    assert verdict.source_files_changed == 0
    # Either the regex match OR the zero-source-files check should fire
    # (the regex will match for "infra findings" prefix; this test exercises
    # the zero-source-files check by ensuring the doc-only diff is detected).
    reasons_text = " ".join(verdict.reasons)
    assert ("zero-source-files-changed" in reasons_text
            or "all-messages-match-loop-regex" in reasons_text), (
        f"expected zero-source-files or regex match in reasons, got {verdict.reasons}"
    )


def test_duplicate_message_prefix_flagged(git_repo: Path) -> None:
    """Three commits with identical first 50 chars triggers duplicate-prefix detection."""
    same_prefix = "[Ned] GRO-506: triage note — batch routing infra"
    _make_branch_with_commits(
        git_repo,
        "ned/GRO-506-dup",
        messages=[
            f"{same_prefix} pass 1 — different suffix A",
            f"{same_prefix} pass 2 — different suffix B",
            f"{same_prefix} pass 3 — different suffix C",
        ],
    )
    verdict = is_loop_branch(str(git_repo), "ned/GRO-506-dup", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is True
    assert any("duplicate-message-prefixes" in r for r in verdict.reasons)


def test_real_feature_with_triage_note_is_not_loop_noise(git_repo: Path) -> None:
    """A feature branch with source changes is NOT loop-noise, even if it has a triage note."""
    _make_branch_with_commits(
        git_repo,
        "feature/gro-1829",
        messages=[
            "[Ned] GRO-1829: add egress scanner",
            "[Ned] GRO-1829: triage note — security review complete",
        ],
        files={"prismatic/security/egress_scanner.py": "import re\nSCANNER = re.compile(r'secret')\n"},
    )
    verdict = is_loop_branch(str(git_repo), "feature/gro-1829", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is False
    assert verdict.source_files_changed >= 1


def test_classify_all_branches_orders_loop_noise_first(git_repo: Path) -> None:
    """classify_all_branches returns loop-noise verdicts before work verdicts."""
    _make_branch_with_commits(
        git_repo,
        "ned/GRO-506",
        messages=["[Ned] GRO-506: triage note — 1st pass"],
    )
    _make_branch_with_commits(
        git_repo,
        "feature/real",
        messages=["[Ned] GRO-100: implement foo"],
        files={"prismatic/foo.py": "x = 1\n"},
    )

    verdicts = classify_all_branches(str(git_repo), default_branch=_DEFAULT_BRANCH)
    flags = [v.is_loop_noise for v in verdicts]
    # All loop-noise verdicts should come first.
    noise_indices = [i for i, f in enumerate(flags) if f]
    work_indices = [i for i, f in enumerate(flags) if not f]
    if noise_indices and work_indices:
        assert max(noise_indices) < min(work_indices)


def test_source_extensions_includes_common_languages() -> None:
    """The source-file extension set should cover Python, JS, TS, Go, etc."""
    assert ".py" in _SOURCE_EXTENSIONS
    assert ".js" in _SOURCE_EXTENSIONS
    assert ".ts" in _SOURCE_EXTENSIONS
    assert ".go" in _SOURCE_EXTENSIONS
    assert ".yaml" in _SOURCE_EXTENSIONS
    # And explicitly exclude markdown / text.
    assert ".md" not in _SOURCE_EXTENSIONS
    assert ".txt" not in _SOURCE_EXTENSIONS


def test_verdict_dataclass_is_immutable() -> None:
    """Verdicts are frozen — useful for caching and dict keys."""
    v = LoopBranchVerdict(
        branch="feature/x", is_loop_noise=False,
        reasons=("no-commits-ahead",), commit_count=0, source_files_changed=0,
    )
    with pytest.raises(Exception):
        v.branch = "feature/y"  # type: ignore[misc]


def test_loop_noise_with_only_one_triage_commit_still_flagged(git_repo: Path) -> None:
    """Even a single 'triage note' commit with no source files is loop-noise."""
    _make_branch_with_commits(
        git_repo,
        "ned/GRO-559",
        messages=["[Ned] GRO-559: triage note — email capture is marketing, not infra"],
        files={"docs/gro-559-triage.md": "# triage\n"},
    )
    verdict = is_loop_branch(str(git_repo), "ned/GRO-559", default_branch=_DEFAULT_BRANCH)
    assert verdict.is_loop_noise is True
    assert verdict.source_files_changed == 0


def test_status_verb_in_loop_pattern(git_repo: Path) -> None:
    """The 'status' verb is in the loop-noise regex — sentinel routine status commits match."""
    _make_branch_with_commits(
        git_repo,
        "ned/GRO-100",
        messages=["[Ned] GRO-100: status — all clear"],
    )
    verdict = is_loop_branch(str(git_repo), "ned/GRO-100", default_branch=_DEFAULT_BRANCH)
    # Triggers via the regex match (1 of 2 reasons) — combined with zero-source.
    assert verdict.is_loop_noise is True