from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import prismatic.verification.source_acquisition as acquisition
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


def test_validation_rejects_attached_head_at_identical_commit(
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
    assert (
        subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"], cwd=source.checkout_path
        ).returncode
        == 1
    )
    git("switch", "-qc", "same-candidate", candidate, cwd=source.checkout_path)
    with pytest.raises(SourceAcquisitionError) as error:
        validate_acquired_source(source)
    assert error.value.code == "attached_head"


def test_auth_handles_are_validated_and_scrubbed(tmp_path: Path) -> None:
    helper = tmp_path / "askpass"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(0o700)
    sock_path = tmp_path / "agent.sock"
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(sock_path))
    try:
        remote = request(
            "provider_remote",
            "other",
            "file:///tmp/source.git",
            "0" * 40,
            "1" * 40,
            "refs/heads/main",
        )
        env = acquisition._validated_auth_environment(
            remote, askpass_helper=helper, ssh_auth_socket=sock_path
        )
        assert env == {
            "GIT_ASKPASS": str(helper),
            "SSH_ASKPASS": str(helper),
            "SSH_AUTH_SOCK": str(sock_path),
        }
        local = request(
            "local_bare_repository",
            "none",
            str(tmp_path),
            "0" * 40,
            "1" * 40,
            "refs/heads/main",
        )
        with pytest.raises(SourceAcquisitionError, match="auth_not_allowed"):
            acquisition._validated_auth_environment(
                local, askpass_helper=helper, ssh_auth_socket=None
            )
    finally:
        sock.close()


@pytest.mark.parametrize("bad", [Path("relative"), Path("missing")])
def test_auth_helper_fails_closed_for_bad_paths(tmp_path: Path, bad: Path) -> None:
    remote = request(
        "provider_remote",
        "other",
        "file:///tmp/source.git",
        "0" * 40,
        "1" * 40,
        "refs/heads/main",
    )
    with pytest.raises(SourceAcquisitionError):
        acquisition._validated_auth_environment(
            remote, askpass_helper=bad, ssh_auth_socket=None
        )
    wrong_type = tmp_path / "not-executable"
    wrong_type.write_text("no", encoding="utf-8")
    with pytest.raises(SourceAcquisitionError, match="invalid_askpass_helper"):
        acquisition._validated_auth_environment(
            remote, askpass_helper=wrong_type, ssh_auth_socket=None
        )


def test_streaming_runner_enforces_binary_and_text_caps(tmp_path: Path) -> None:
    p = SourceAcquisitionPolicy(
        "repo",
        frozenset({"provider_remote"}),
        frozenset({"other"}),
        max_command_output_bytes=1024,
        max_tree_listing_bytes=512,
    )
    env = acquisition._environment(tmp_path)
    argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*4096)"]
    for binary, code in ((False, "output_overflow"), (True, "tree_listing_overflow")):
        with pytest.raises(SourceAcquisitionError) as error:
            acquisition._run(
                argv,
                cwd=tmp_path,
                env=env,
                policy=p,
                deadline=time.monotonic() + 10,
                label="canary",
                binary=binary,
            )
        assert error.value.code == code


def test_bundle_fence_detects_replacement(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.write_bytes(b"first")
    fence = acquisition._bundle_fence(bundle)
    bundle.write_bytes(b"second")
    with pytest.raises(SourceAcquisitionError, match="bundle_changed"):
        acquisition._assert_bundle_unchanged(bundle, fence)
