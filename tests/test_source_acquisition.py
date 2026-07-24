from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
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


@pytest.mark.parametrize("mode", ("stdout", "stderr", "mixed"))
def test_streaming_runner_periodic_output_cannot_refresh_command_deadline(
    tmp_path: Path, mode: str
) -> None:
    policy = SourceAcquisitionPolicy(
        "repo",
        frozenset({"provider_remote"}),
        frozenset({"other"}),
        command_timeout_seconds=0.15,
        total_timeout_seconds=2,
    )
    code = """import sys, time
mode = sys.argv[1]
for _ in range(30):
    if mode in (\"stdout\", \"mixed\"):
        sys.stdout.write(\"stdout-canary\\n\")
        sys.stdout.flush()
    if mode in (\"stderr\", \"mixed\"):
        sys.stderr.write(\"stderr-canary\\n\")
        sys.stderr.flush()
    time.sleep(0.03)
"""
    started = time.monotonic()
    with pytest.raises(SourceAcquisitionError) as error:
        acquisition._run(
            [sys.executable, "-c", code, mode],
            cwd=tmp_path,
            env=acquisition._environment(tmp_path),
            policy=policy,
            deadline=time.monotonic() + 2,
            label="timeout-canary",
        )
    elapsed = time.monotonic() - started
    assert error.value.code == "command_timeout"
    assert "stdout-canary" not in str(error.value)
    assert "stderr-canary" not in str(error.value)
    # Natural completion is ~0.9s; periodic readable events must not reset 0.15s.
    assert 0.08 <= elapsed < 0.55


def test_streaming_runner_silent_and_total_deadlines_are_stable(tmp_path: Path) -> None:
    env = acquisition._environment(tmp_path)

    def make_policy(
        command_timeout: float, total_timeout: float
    ) -> SourceAcquisitionPolicy:
        return SourceAcquisitionPolicy(
            "repo",
            frozenset({"provider_remote"}),
            frozenset({"other"}),
            command_timeout_seconds=command_timeout,
            total_timeout_seconds=total_timeout,
        )

    cases = (
        (make_policy(0.1, 2), 1, "command_timeout"),
        (make_policy(2, 2), 0.1, "total_timeout"),
    )
    for policy, total_remaining, expected in cases:
        with pytest.raises(SourceAcquisitionError) as error:
            acquisition._run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env=env,
                policy=policy,
                deadline=time.monotonic() + total_remaining,
                label="silent-canary",
            )
        assert error.value.code == expected
    assert (
        acquisition._run(
            [sys.executable, "-c", "print('short-success')"],
            cwd=tmp_path,
            env=env,
            policy=make_policy(1, 2),
            deadline=time.monotonic() + 1,
            label="short-success",
        )
        == b"short-success\n"
    )


def test_streaming_runner_timeout_kills_and_reaps_process_group(tmp_path: Path) -> None:
    policy = SourceAcquisitionPolicy(
        "repo",
        frozenset({"provider_remote"}),
        frozenset({"other"}),
        command_timeout_seconds=0.15,
        total_timeout_seconds=2,
    )
    parent_pid, child_pid = tmp_path / "parent.pid", tmp_path / "child.pid"
    code = """import os, subprocess, sys, time
open(sys.argv[1], \"w\").write(str(os.getpid()))
child = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(60)\"])
open(sys.argv[2], \"w\").write(str(child.pid))
time.sleep(60)
"""
    with pytest.raises(SourceAcquisitionError, match="command_timeout"):
        acquisition._run(
            [sys.executable, "-c", code, str(parent_pid), str(child_pid)],
            cwd=tmp_path,
            env=acquisition._environment(tmp_path),
            policy=policy,
            deadline=time.monotonic() + 2,
            label="group-canary",
        )
    for pid_path in (parent_pid, child_pid):
        pid = int(pid_path.read_text())
        until = time.monotonic() + 1
        while time.monotonic() < until:
            if not Path(f"/proc/{pid}").exists():
                break
            time.sleep(0.02)
        assert not Path(f"/proc/{pid}").exists(), f"unreaped process {pid}"


def test_bundle_fence_detects_replacement(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.write_bytes(b"first")
    fence = acquisition._bundle_fence(bundle)
    bundle.write_bytes(b"second")
    with pytest.raises(SourceAcquisitionError, match="bundle_changed"):
        acquisition._assert_bundle_unchanged(bundle, fence)


def test_complete_object_guards_reject_every_partial_state(
    tmp_path: Path, fixture_repo: tuple[Path, str, str, str]
) -> None:
    bare, candidate, tree, ref = fixture_repo
    cases = (
        (
            "partial-extension",
            lambda checkout: git(
                "config", "extensions.partialClone", "origin", cwd=checkout
            ),
        ),
        (
            "promisor-remote",
            lambda checkout: git(
                "config", "remote.origin.promisor", "true", cwd=checkout
            ),
        ),
        (
            "partial-filter",
            lambda checkout: git(
                "config", "remote.origin.partialCloneFilter", "blob:none", cwd=checkout
            ),
        ),
        (
            "promisor-marker",
            lambda checkout: (
                checkout / ".git" / "objects" / "pack" / "test.promisor"
            ).write_text("", encoding="utf-8"),
        ),
        (
            "alternates",
            lambda checkout: (
                checkout / ".git" / "objects" / "info" / "alternates"
            ).write_text("/tmp/objects\n", encoding="utf-8"),
        ),
        (
            "shallow",
            lambda checkout: (checkout / ".git" / "shallow").write_text(
                candidate + "\n", encoding="utf-8"
            ),
        ),
    )
    for name, mutate in cases:
        workspace = tmp_path / name
        workspace.mkdir()
        source = acquire_source(
            request("local_bare_repository", "none", str(bare), candidate, tree, ref),
            policy(),
            workspace_root=workspace,
        )
        mutate(source.checkout_path)
        env = acquisition._environment(source.checkout_path.parent / "check-home")
        (source.checkout_path.parent / "check-home" / "empty-template").mkdir(
            parents=True
        )
        if name == "shallow":
            with pytest.raises(SourceAcquisitionError) as error:
                acquisition._check_repository(
                    source.checkout_path,
                    None,
                    policy=policy(),
                    env=env,
                    deadline=time.monotonic() + 10,
                )
        else:
            with pytest.raises(SourceAcquisitionError) as error:
                acquisition._check_complete_objects(
                    source.checkout_path,
                    candidate,
                    policy=policy(),
                    env=env,
                    deadline=time.monotonic() + 10,
                )
        assert error.value.code == "incomplete_objects"


def test_complete_object_reachability_and_valid_baseline(
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
    validate_acquired_source(source)
    blob = git("rev-parse", "HEAD:file.txt", cwd=source.checkout_path)
    object_path = source.checkout_path / ".git" / "objects" / blob[:2] / blob[2:]
    assert object_path.exists()
    object_path.unlink()
    with pytest.raises(SourceAcquisitionError) as error:
        validate_acquired_source(source)
    assert error.value.code == "missing_reachable_object"


def test_offline_bundle_exact_ref_candidate_and_prerequisite_fences(
    tmp_path: Path, fixture_repo: tuple[Path, str, str, str]
) -> None:
    bare, candidate, tree, ref = fixture_repo
    bundle = tmp_path / "source.bundle"
    git("bundle", "create", str(bundle), ref, cwd=bare)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert (
        acquire_source(
            request("offline_git_bundle", "none", str(bundle), candidate, tree, ref),
            policy(),
            workspace_root=workspace,
        ).candidate_sha
        == candidate
    )
    for bad_ref, bad_candidate in (("refs/heads/missing", candidate), (ref, "0" * 40)):
        with pytest.raises(SourceAcquisitionError) as error:
            acquire_source(
                request(
                    "offline_git_bundle",
                    "none",
                    str(bundle),
                    bad_candidate,
                    tree,
                    bad_ref,
                ),
                policy(),
                workspace_root=workspace,
            )
        assert error.value.code == "bundle_ref_mismatch"
    work = tmp_path / "other-work"
    git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    git("config", "user.email", "test@example.invalid", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    git("switch", "-qc", "other", cwd=work)
    (work / "other.txt").write_text("other\n", encoding="utf-8")
    git("add", ".", cwd=work)
    git("commit", "-qm", "other", cwd=work)
    git("push", "-q", "origin", "other", cwd=work)
    multi = tmp_path / "multi.bundle"
    git("bundle", "create", str(multi), "refs/heads/main", "refs/heads/other", cwd=bare)
    assert (
        acquire_source(
            request("offline_git_bundle", "none", str(multi), candidate, tree, ref),
            policy(),
            workspace_root=workspace,
        ).candidate_sha
        == candidate
    )
    incremental = tmp_path / "incremental.bundle"
    git(
        "bundle",
        "create",
        str(incremental),
        f"^{candidate}",
        "refs/heads/other",
        cwd=bare,
    )
    other, other_tree = (
        git("rev-parse", "refs/heads/other", cwd=bare),
        git("rev-parse", "refs/heads/other^{tree}", cwd=bare),
    )
    with pytest.raises(SourceAcquisitionError) as error:
        acquire_source(
            request(
                "offline_git_bundle",
                "none",
                str(incremental),
                other,
                other_tree,
                "refs/heads/other",
            ),
            policy(),
            workspace_root=workspace,
        )
    assert error.value.code == "git_command_failed"


def test_auth_handle_matrix_and_scrubbed_provider_environment(tmp_path: Path) -> None:
    remote = request(
        "provider_remote",
        "other",
        "file:///tmp/source.git",
        "0" * 40,
        "1" * 40,
        "refs/heads/main",
    )
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(0o700)
    regular, directory = tmp_path / "regular", tmp_path / "directory"
    regular.write_text("x", encoding="utf-8")
    directory.mkdir()
    link = tmp_path / "helper-link"
    link.symlink_to(helper)
    for handle, code in (
        (Path("relative"), "invalid_path"),
        (tmp_path / "missing", "invalid_path"),
        (directory, "invalid_askpass_helper"),
        (link, "symlink_path"),
    ):
        with pytest.raises(SourceAcquisitionError) as error:
            acquisition._validated_auth_environment(
                remote, askpass_helper=handle, ssh_auth_socket=None
            )
        assert error.value.code == code
        assert "secret-canary" not in str(error.value) and str(handle) not in str(
            error.value
        )
    with pytest.raises(SourceAcquisitionError, match="invalid_ssh_auth_socket"):
        acquisition._validated_auth_environment(
            remote, askpass_helper=None, ssh_auth_socket=regular
        )
    socket_parent = tmp_path / "socket-parent"
    socket_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SourceAcquisitionError, match="symlink_path"):
        acquisition._validated_auth_environment(
            remote, askpass_helper=None, ssh_auth_socket=socket_parent / "none"
        )
    for kind, locator in (
        ("local_bare_repository", str(tmp_path)),
        ("offline_git_bundle", str(regular)),
    ):
        with pytest.raises(SourceAcquisitionError, match="auth_not_allowed"):
            acquisition._validated_auth_environment(
                request(kind, "none", locator, "0" * 40, "1" * 40, "refs/heads/main"),
                askpass_helper=helper,
                ssh_auth_socket=None,
            )
    env = acquisition._environment(tmp_path)
    env.update(
        acquisition._validated_auth_environment(
            remote, askpass_helper=helper, ssh_auth_socket=None
        )
    )
    assert {
        key for key in env if key in {"GIT_ASKPASS", "SSH_ASKPASS", "SSH_AUTH_SOCK"}
    } == {"GIT_ASKPASS", "SSH_ASKPASS"}


def test_streaming_runner_reaps_overflow_timeout_and_stderr_only(
    tmp_path: Path,
) -> None:
    p = SourceAcquisitionPolicy(
        "repo",
        frozenset({"provider_remote"}),
        frozenset({"other"}),
        command_timeout_seconds=0.1,
        total_timeout_seconds=1,
        max_command_output_bytes=128,
        max_tree_listing_bytes=64,
    )
    env, pid_file = acquisition._environment(tmp_path), tmp_path / "pid"
    code = "import os,sys;open(sys.argv[1],'w').write(str(os.getpid()));sys.stderr.buffer.write(b'x'*4096)"
    with pytest.raises(SourceAcquisitionError) as error:
        acquisition._run(
            [sys.executable, "-c", code, str(pid_file)],
            cwd=tmp_path,
            env=env,
            policy=p,
            deadline=time.monotonic() + 1,
            label="secret-canary",
        )
    assert error.value.code == "output_overflow" and "x" * 64 not in str(error.value)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    timeout_pid = tmp_path / "timeout-pid"
    timeout_code = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(60)"
    with pytest.raises(SourceAcquisitionError) as error:
        acquisition._run(
            [sys.executable, "-c", timeout_code, str(timeout_pid)],
            cwd=tmp_path,
            env=env,
            policy=p,
            deadline=time.monotonic() + 1,
            label="timeout",
        )
    assert error.value.code == "command_timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int(timeout_pid.read_text()), 0)
    with pytest.raises(SourceAcquisitionError) as error:
        acquisition._run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*1000)"],
            cwd=tmp_path,
            env=env,
            policy=p,
            deadline=time.monotonic() + 1,
            label="tree",
            binary=True,
        )
    assert error.value.code == "tree_listing_overflow"


def test_validation_detects_head_digest_metadata_and_tree_mutation(
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
    git("config", "user.email", "test@example.invalid", cwd=source.checkout_path)
    git("config", "user.name", "Test", cwd=source.checkout_path)
    (source.checkout_path / "file.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SourceAcquisitionError) as error:
        validate_acquired_source(source)
    assert error.value.code == "dirty_checkout"
    git("checkout", "--", "file.txt", cwd=source.checkout_path)
    (source.checkout_path / "file.txt").write_text("changed\n", encoding="utf-8")
    git("add", "file.txt", cwd=source.checkout_path)
    git("commit", "-qm", "changed", cwd=source.checkout_path)
    with pytest.raises(SourceAcquisitionError) as error:
        validate_acquired_source(source)
    assert error.value.code == "source_changed"
    clean_workspace = tmp_path / "clean"
    clean_workspace.mkdir()
    clean = acquire_source(
        request("local_bare_repository", "none", str(bare), candidate, tree, ref),
        policy(),
        workspace_root=clean_workspace,
    )
    for altered in (
        replace(clean, source_acquisition_digest="sha256:" + "0" * 64),
        replace(clean, repository_id="other"),
        replace(clean, tree_sha="0" * 40),
    ):
        with pytest.raises(SourceAcquisitionError) as error:
            validate_acquired_source(altered)
        assert error.value.code == "source_changed"
