from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prismatic.verification import (
    SOURCE_ACQUISITION_V1_OK,
    SourceAcquisitionError,
    SourceAcquisitionPolicy,
    SourceAcquisitionRequest,
    acquire_source,
    validate_acquired_source,
)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, capture_output=True
    ).stdout.strip()


@pytest.fixture
def fixture_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    work = tmp_path / "work"
    bare = tmp_path / "source.git"
    work.mkdir()
    git("init", "-q", cwd=work)
    git("config", "user.email", "test@example.invalid", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    (work / "file.txt").write_text("immutable\n", encoding="utf-8")
    git("add", ".", cwd=work)
    git("commit", "-qm", "fixture", cwd=work)
    git("branch", "-M", "main", cwd=work)
    candidate = git("rev-parse", "HEAD", cwd=work)
    tree = git("rev-parse", "HEAD^{tree}", cwd=work)
    git("clone", "--bare", "-q", str(work), str(bare), cwd=tmp_path)
    return bare, candidate, tree, "refs/heads/main"


def policy(
    *, schemes: frozenset[str] = frozenset({"https", "ssh"})
) -> SourceAcquisitionPolicy:
    return SourceAcquisitionPolicy(
        "repo-1",
        frozenset({"provider_remote", "local_bare_repository", "offline_git_bundle"}),
        frozenset({"other", "none"}),
        allowed_remote_schemes=schemes,
    )


def request(
    kind: str, provider: str, locator: str, candidate: str, tree: str, ref: str
) -> SourceAcquisitionRequest:
    return SourceAcquisitionRequest(kind, provider, locator, ref, candidate, tree)


def test_local_bare_acquisition_is_detached_clean_and_validated(
    tmp_path: Path, fixture_repo: tuple[Path, str, str, str]
) -> None:
    bare, candidate, tree, ref = fixture_repo
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = acquire_source(
        request("local_bare_repository", "none", str(bare), candidate, tree, ref),
        policy(),
        workspace_root=workspace,
    )
    assert source.marker == SOURCE_ACQUISITION_V1_OK
    assert source.checkout_path.exists()
    assert git("rev-parse", "HEAD", cwd=source.checkout_path) == candidate
    assert git("status", "--porcelain", cwd=source.checkout_path) == ""
    assert not (
        source.checkout_path / ".git" / "objects" / "info" / "alternates"
    ).exists()
    validate_acquired_source(source)


def test_provider_file_remote_and_digest_are_deterministic(
    tmp_path: Path, fixture_repo: tuple[Path, str, str, str]
) -> None:
    bare, candidate, tree, ref = fixture_repo
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    req = request("provider_remote", "other", bare.as_uri(), candidate, tree, ref)
    p = policy(schemes=frozenset({"file"}))
    left = acquire_source(req, p, workspace_root=first)
    right = acquire_source(req, p, workspace_root=second)
    assert left.source_acquisition_digest == right.source_acquisition_digest


def test_annotated_tag_retains_unpeeled_object_and_rejects_bad_inputs(
    tmp_path: Path, fixture_repo: tuple[Path, str, str, str]
) -> None:
    bare, candidate, tree, _ref = fixture_repo
    work = tmp_path / "tag-work"
    git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    git("config", "user.email", "test@example.invalid", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    git("tag", "-a", "v1", "-m", "v1", cwd=work)
    git("push", "-q", "origin", "--tags", cwd=work)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = acquire_source(
        request(
            "local_bare_repository", "none", str(bare), candidate, tree, "refs/tags/v1"
        ),
        policy(),
        workspace_root=workspace,
    )
    assert source.ref_object_sha != candidate
    with pytest.raises(SourceAcquisitionError):
        acquire_source(
            request(
                "local_bare_repository",
                "none",
                str(bare),
                candidate.upper(),
                tree,
                "HEAD",
            ),
            policy(),
            workspace_root=workspace,
        )


def test_validation_fails_closed_for_dirty_checkout(
    tmp_path: Path, fixture_repo: tuple[Path, str, str, str]
) -> None:
    bare, candidate, tree, ref = fixture_repo
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = acquire_source(
        request("local_bare_repository", "none", str(bare), candidate, tree, ref),
        policy(),
        workspace_root=workspace,
    )
    (source.checkout_path / "untracked").write_text("no", encoding="utf-8")
    with pytest.raises(SourceAcquisitionError) as error:
        validate_acquired_source(source)
    assert error.value.code == "dirty_checkout"
