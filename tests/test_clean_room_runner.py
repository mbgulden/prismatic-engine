from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic.verification import (
    CleanRoomIsolation,
    CleanRoomRunnerError,
    SourceAcquisitionPolicy,
    SourceAcquisitionRequest,
    acquire_source,
    run_clean_room,
)


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def policy(
    commands: list[dict[str, object]], repository_id: str = "runner-test"
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "policy_id": "runner-policy",
        "policy_version": "1.0.0",
        "status": "active",
        "repository": {
            "repository_id": repository_id,
            "source_requirements": {
                "require_full_git_objects": True,
                "allowed_source_kinds": ["local_bare_repository"],
                "allowed_source_providers": ["none"],
            },
        },
        "approved_backends": [{"id": "backend", "class": "self_hosted_clean_room"}],
        "approved_verifiers": {
            "identities": [
                {
                    "id": "verifier",
                    "key_id": "key",
                    "algorithm": "ed25519",
                    "public_key_pem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAonIAm5bXuYIKs/REfChIGowpzL9SabNGIL3/H2shVJs=\n-----END PUBLIC KEY-----\n",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "require_producer_verifier_separation": True,
        },
        "clean_room": {
            "required": True,
            "source_acquisition_required": True,
            "network_isolation_required": True,
        },
        "bindings": {
            "require_base_sha": True,
            "require_candidate_sha": True,
            "require_tree_sha": True,
            "require_changed_paths": True,
        },
        "commands": commands,
        "evidence": {
            "logs_required": True,
            "artifacts_required": True,
            "digest_requirements": [
                {"kind": "log", "algorithm": "sha256", "required": True},
                {
                    "kind": "artifact",
                    "algorithm": "sha256",
                    "required": True,
                    "name": "result.txt",
                },
            ],
        },
        "environment": {
            "environment_digest_required": True,
            "toolchain_digest_required": True,
            "digest_algorithm": "sha256",
        },
        "freshness": {
            "max_age_seconds": 3600,
            "expiry_required": True,
            "supersession_required": True,
            "revocation_required": True,
        },
        "attestation": {
            "required": True,
            "allowed_algorithms": ["ed25519"],
            "allowed_key_ids": ["key"],
        },
        "required_proof_classes": ["unit"],
        "non_claims": ["no merge authority"],
        "authorization_boundary": {"merge_authorization_external": True},
    }


@pytest.fixture
def source(tmp_path: Path):
    work, bare, root = tmp_path / "work", tmp_path / "bare.git", tmp_path / "acquired"
    work.mkdir()
    root.mkdir()
    git("init", cwd=work)
    git("config", "user.email", "test@example.test", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    (work / "nested").mkdir()
    (work / "nested" / "input.txt").write_text("immutable\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "fixture", cwd=work)
    git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
    candidate, tree = (
        git("rev-parse", "HEAD", cwd=work),
        git("rev-parse", "HEAD^{tree}", cwd=work),
    )
    request = SourceAcquisitionRequest(
        "local_bare_repository", "none", str(bare), "refs/heads/master", candidate, tree
    )
    acquired = acquire_source(
        request,
        SourceAcquisitionPolicy(
            "runner-test", frozenset({"local_bare_repository"}), frozenset({"none"})
        ),
        workspace_root=root,
    )
    return acquired, tmp_path / "evidence"


def command(identifier: str, code: str, cwd: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "id": identifier,
        "argv": [sys.executable, "-c", code],
        "proof_class": "unit",
        "timeout_seconds": 20,
        "required": True,
    }
    if cwd is not None:
        result["working_directory"] = cwd
    return result


def test_split_secret_detector_fails_closed_on_each_stream_boundary():
    from prismatic.verification.clean_room_runner import _SecretDetector

    for parts in ((b"token=", b"abcdefghijklmnop"), (b"token=abcd", b"efghijklmnop")):
        detector = _SecretDetector()
        assert detector.feed(parts[0]) is False
        assert detector.feed(parts[1]) is True


def test_required_named_artifact_is_captured_after_command_execution(source):
    acquired, evidence = source
    code = (
        "import os, pathlib; "
        "pathlib.Path(os.environ['PRISMATIC_ARTIFACT_ROOT'], 'result.txt').write_text('proof')"
    )
    run = run_clean_room(
        acquired,
        policy([command("first", code)]),
        producer_id="producer",
        verifier_id="verifier",
        evidence_root=evidence,
        isolation=CleanRoomIsolation(True, True),
    )
    assert run.marker == "CLEAN_ROOM_RUNNER_V1_OK"
    assert [
        (artifact.name, artifact.digest.size_bytes) for artifact in run.artifacts
    ] == [("result.txt", 5)]
    assert run.commands[0].stdout_log.parent == evidence / "logs"
    assert stat.S_IMODE(os.stat(evidence).st_mode) == 0o700


def test_missing_or_unsafe_artifact_requirement_fails_before_command_execution(source):
    acquired, evidence = source
    value = policy([command("first", "raise SystemExit(99)")])
    value["evidence"]["digest_requirements"] = [
        {"kind": "artifact", "algorithm": "sha256", "required": True}
    ]
    with pytest.raises(CleanRoomRunnerError, match="unsafe_artifact_requirement"):
        run_clean_room(
            acquired,
            value,
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(True, True),
        )
    assert not evidence.exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update({"status": "revoked"}),
        lambda p: p["commands"].append(dict(p["commands"][0])),
    ],
)
def test_policy_rejections(source, change):
    acquired, evidence = source
    value = policy([command("one", "print(1)")])
    change(value)
    with pytest.raises(CleanRoomRunnerError):
        run_clean_room(
            acquired,
            value,
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(True, True),
        )


def test_authority_and_cwd_rejections(source):
    acquired, evidence = source
    with pytest.raises(CleanRoomRunnerError, match="invalid_identity"):
        run_clean_room(
            acquired,
            policy([command("one", "print(1)")]),
            producer_id="same",
            verifier_id="same",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(True, True),
        )
    with pytest.raises(CleanRoomRunnerError, match="isolation_not_enforced"):
        run_clean_room(
            acquired,
            policy([command("one", "print(1)")]),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(False, True),
        )
    # The canonical policy schema rejects traversal before any process is spawned.
    with pytest.raises(CleanRoomRunnerError, match="invalid_policy"):
        run_clean_room(
            acquired,
            policy([command("one", "print(1)", "../escape")]),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(True, True),
        )


def test_evidence_root_authority_rejects_before_spawn_or_mutation(source, monkeypatch):
    acquired, _ = source
    checkout = acquired.checkout_path
    original = {
        path.relative_to(checkout): path.read_bytes()
        for path in checkout.rglob("*")
        if path.is_file()
    }
    command_zero = checkout / "command-zero"
    value = policy(
        [
            command(
                "one",
                "from pathlib import Path; Path('command-zero').write_text('ran')",
            )
        ]
    )
    monkeypatch.chdir(checkout)
    with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
        run_clean_room(
            acquired,
            value,
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=Path("evidence"),
            isolation=CleanRoomIsolation(True, True),
        )
    assert not command_zero.exists()
    assert not (checkout / "evidence").exists()
    assert {
        path.relative_to(checkout): path.read_bytes()
        for path in checkout.rglob("*")
        if path.is_file()
    } == original
    assert git("status", "--porcelain", cwd=checkout) == ""


def test_evidence_root_symlink_rejects_without_target_mutation(source, tmp_path):
    acquired, _ = source
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    sentinel = target / "sentinel"
    sentinel.write_text("unchanged")
    before = os.stat(target)
    root = tmp_path / "linked-evidence"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
        run_clean_room(
            acquired,
            policy([command("one", "raise SystemExit(91)")]),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=root,
            isolation=CleanRoomIsolation(True, True),
        )
    after = os.stat(target)
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert (after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    assert sentinel.read_text() == "unchanged"


@pytest.mark.parametrize("kind", ["file", "fifo", "socket"])
def test_unsafe_evidence_root_leaf_types_reject_without_spawn(source, tmp_path, kind):
    acquired, _ = source
    root = tmp_path / kind
    if kind == "file":
        root.write_text("not a directory")
    elif kind == "fifo":
        os.mkfifo(root)
    else:
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(root))
    try:
        with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
            run_clean_room(
                acquired,
                policy([command("one", "raise SystemExit(92)")]),
                producer_id="producer",
                verifier_id="verifier",
                evidence_root=root,
                isolation=CleanRoomIsolation(True, True),
            )
    finally:
        if kind == "socket":
            listener.close()


def test_checkout_and_noncanonical_evidence_roots_reject_before_mutation(source):
    acquired, evidence = source
    for root in (
        acquired.checkout_path,
        acquired.checkout_path / "evidence",
        evidence.parent / "x" / ".." / "evidence",
    ):
        with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
            run_clean_room(
                acquired,
                policy([command("one", "raise SystemExit(93)")]),
                producer_id="producer",
                verifier_id="verifier",
                evidence_root=root,
                isolation=CleanRoomIsolation(True, True),
            )
    assert not (acquired.checkout_path / "evidence").exists()
    assert not evidence.exists()


def test_intermediate_evidence_component_symlink_rejects_without_mutation(
    source, tmp_path
):
    acquired, _ = source
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    root = alias / "evidence"
    before = os.stat(target)
    with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
        run_clean_room(
            acquired,
            policy([command("one", "raise SystemExit(94)")]),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=root,
            isolation=CleanRoomIsolation(True, True),
        )
    after = os.stat(target)
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert not (target / "evidence").exists()


def test_intermediate_evidence_component_replacement_race_rejects_without_mutation(
    source, tmp_path, monkeypatch
):
    acquired, _ = source
    parent = tmp_path / "parent"
    target = tmp_path / "target"
    parent.mkdir()
    target.mkdir(mode=0o755)
    before = os.stat(target)
    original_open = os.open
    replaced = False

    def replace_then_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == "parent" and not replaced:
            replaced = True
            parent.rmdir()
            parent.symlink_to(target, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(
        "prismatic.verification.clean_room_runner.os.open", replace_then_open
    )
    with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
        run_clean_room(
            acquired,
            policy([command("one", "raise SystemExit(95)")]),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=parent / "evidence",
            isolation=CleanRoomIsolation(True, True),
        )
    after = os.stat(target)
    assert replaced is True
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert not (target / "evidence").exists()


@pytest.mark.parametrize("replacement", ["rename_recreate", "symlink_swap"])
def test_post_bind_evidence_leaf_replacement_rejects_before_command_zero(
    source, tmp_path, monkeypatch, replacement
):
    """The held root descriptor must not authorize a replaced named leaf."""
    acquired, evidence = source
    evidence.mkdir(mode=0o700)
    target = tmp_path / "attacker-target"
    target.mkdir(mode=0o755)
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"unchanged")
    target_before = os.stat(target)
    command_zero = acquired.checkout_path / "command-zero"
    runner = __import__(
        "prismatic.verification.clean_room_runner", fromlist=["_create_bound_run_dir"]
    )
    original_create = runner._create_bound_run_dir

    def create_then_replace(binding):
        run_dir = original_create(binding)
        evidence.rename(tmp_path / "bound-evidence-original")
        if replacement == "symlink_swap":
            evidence.symlink_to(target, target_is_directory=True)
        else:
            evidence.mkdir(mode=0o700)
        return run_dir

    monkeypatch.setattr(
        "prismatic.verification.clean_room_runner._create_bound_run_dir",
        create_then_replace,
    )
    fds_before = _fd_snapshot()
    with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
        run_clean_room(
            acquired,
            policy(
                [
                    command(
                        "zero",
                        "from pathlib import Path; Path('command-zero').write_text('ran')",
                    )
                ]
            ),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(True, True),
        )
    assert _fd_snapshot() == fds_before
    target_after = os.stat(target)
    assert not command_zero.exists()
    assert sentinel.read_bytes() == b"unchanged"
    assert [child.name for child in target.iterdir()] == ["sentinel"]
    assert stat.S_IMODE(target_after.st_mode) == stat.S_IMODE(target_before.st_mode)
    assert target_after.st_mtime_ns == target_before.st_mtime_ns


def _fd_snapshot() -> set[int]:
    return {int(entry) for entry in os.listdir("/proc/self/fd")}


def _assert_bind_failure_without_fd_leak(root: Path, checkout: Path) -> None:
    from prismatic.verification.clean_room_runner import _bind_evidence_root

    before = _fd_snapshot()
    with pytest.raises(CleanRoomRunnerError, match="unsafe_evidence_root"):
        _bind_evidence_root(root, checkout)
    assert _fd_snapshot() == before


def test_intermediate_bind_failures_close_untransferred_descriptor(
    source, tmp_path, monkeypatch
):
    acquired, _ = source
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    root = intermediate / "evidence"
    runner = __import__(
        "prismatic.verification.clean_room_runner", fromlist=["_bind_evidence_root"]
    )
    original_open, original_fstat = os.open, os.fstat
    opened: set[int] = set()

    def tracked_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "intermediate":
            opened.add(fd)
        return fd

    def fail_fstat(fd):
        if fd in opened:
            raise OSError("injected intermediate fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(runner.os, "open", tracked_open)
    monkeypatch.setattr(runner.os, "fstat", fail_fstat)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)


def test_intermediate_stat_identity_and_type_failures_close_descriptor(
    source, tmp_path, monkeypatch
):
    acquired, _ = source
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    root = intermediate / "evidence"
    runner = __import__(
        "prismatic.verification.clean_room_runner", fromlist=["_bind_evidence_root"]
    )
    original_stat = os.stat

    def fail_stat(path, *args, **kwargs):
        if path == "intermediate":
            raise OSError("injected intermediate stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runner.os, "stat", fail_stat)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)

    monkeypatch.undo()
    original_open, original_fstat, original_stat = os.open, os.fstat, os.stat
    opened: set[int] = set()

    def tracked_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "intermediate":
            opened.add(fd)
        return fd

    def mismatched_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == "intermediate":
            fields = list(value)
            fields[1] += 1
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(runner.os, "open", tracked_open)
    monkeypatch.setattr(runner.os, "stat", mismatched_stat)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)

    monkeypatch.undo()
    opened.clear()

    def typed_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "intermediate":
            opened.add(fd)
        return fd

    def nondirectory_fstat(fd):
        value = original_fstat(fd)
        if fd in opened:
            fields = list(value)
            fields[0] = stat.S_IFREG | stat.S_IMODE(value.st_mode)
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(runner.os, "open", typed_open)
    monkeypatch.setattr(runner.os, "fstat", nondirectory_fstat)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)


def test_next_component_open_and_leaf_checks_close_descriptors(
    source, tmp_path, monkeypatch
):
    acquired, _ = source
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    runner = __import__(
        "prismatic.verification.clean_room_runner", fromlist=["_bind_evidence_root"]
    )
    original_open = os.open

    def fail_next_open(path, flags, *args, **kwargs):
        if path == "next":
            raise OSError("injected next component open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(runner.os, "open", fail_next_open)
    _assert_bind_failure_without_fd_leak(
        intermediate / "next" / "evidence", acquired.checkout_path
    )

    monkeypatch.undo()
    root = tmp_path / "leaf-evidence"

    def fail_leaf_open(path, flags, *args, **kwargs):
        if path == "leaf-evidence":
            raise OSError("injected root open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(runner.os, "open", fail_leaf_open)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)

    monkeypatch.undo()
    original_open, original_fstat, original_stat, original_fchmod = (
        os.open,
        os.fstat,
        os.stat,
        os.fchmod,
    )
    leaf_fds: set[int] = set()

    def tracked_leaf_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "leaf-evidence":
            leaf_fds.add(fd)
        return fd

    def fail_leaf_fstat(fd):
        if fd in leaf_fds:
            raise OSError("injected root fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(runner.os, "open", tracked_leaf_open)
    monkeypatch.setattr(runner.os, "fstat", fail_leaf_fstat)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)

    monkeypatch.undo()
    leaf_fds.clear()

    def stat_leaf_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "leaf-evidence":
            leaf_fds.add(fd)
        return fd

    def fail_leaf_stat(path, *args, **kwargs):
        if path == "leaf-evidence":
            raise OSError("injected root stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runner.os, "open", stat_leaf_open)
    monkeypatch.setattr(runner.os, "stat", fail_leaf_stat)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)

    monkeypatch.undo()
    leaf_fds.clear()

    def chmod_leaf_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "leaf-evidence":
            leaf_fds.add(fd)
        return fd

    def fail_fchmod(fd, mode):
        if fd in leaf_fds:
            raise OSError("injected fchmod failure")
        return original_fchmod(fd, mode)

    monkeypatch.setattr(runner.os, "open", chmod_leaf_open)
    monkeypatch.setattr(runner.os, "fchmod", fail_fchmod)
    _assert_bind_failure_without_fd_leak(root, acquired.checkout_path)


def test_post_chmod_leaf_checks_and_runner_paths_do_not_leak(
    source, tmp_path, monkeypatch
):
    acquired, evidence = source
    runner = __import__(
        "prismatic.verification.clean_room_runner", fromlist=["_bind_evidence_root"]
    )
    original_fstat = os.fstat
    calls = 0

    def fail_post_chmod_fstat(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected post-chmod fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(runner.os, "fstat", fail_post_chmod_fstat)
    _assert_bind_failure_without_fd_leak(evidence, acquired.checkout_path)

    monkeypatch.undo()
    original_stat = os.stat
    calls = 0

    def fail_post_chmod_stat(path, *args, **kwargs):
        nonlocal calls
        if path == "evidence":
            calls += 1
            if calls == 2:
                raise OSError("injected post-chmod stat failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runner.os, "stat", fail_post_chmod_stat)
    _assert_bind_failure_without_fd_leak(evidence, acquired.checkout_path)

    monkeypatch.undo()
    before = _fd_snapshot()
    run = run_clean_room(
        acquired,
        policy(
            [
                command(
                    "ok",
                    "import os, pathlib; pathlib.Path(os.environ['PRISMATIC_ARTIFACT_ROOT'], 'result.txt').write_text('ok')",
                )
            ]
        ),
        producer_id="producer",
        verifier_id="verifier",
        evidence_root=evidence,
        isolation=CleanRoomIsolation(True, True),
    )
    assert run.marker == "CLEAN_ROOM_RUNNER_V1_OK"
    assert _fd_snapshot() == before

    for _ in range(3):
        before = _fd_snapshot()
        with pytest.raises(CleanRoomRunnerError, match="command_failed"):
            run_clean_room(
                acquired,
                policy([command("bad", "raise SystemExit(6)")]),
                producer_id="producer",
                verifier_id="verifier",
                evidence_root=evidence,
                isolation=CleanRoomIsolation(True, True),
            )
        assert _fd_snapshot() == before


def test_private_run_directory_creation_failure_closes_binding(source, monkeypatch):
    acquired, evidence = source
    runner = __import__(
        "prismatic.verification.clean_room_runner", fromlist=["_create_bound_run_dir"]
    )
    before = _fd_snapshot()

    def fail_private_run_directory(binding):
        raise OSError("injected private run directory failure")

    monkeypatch.setattr(runner, "_create_bound_run_dir", fail_private_run_directory)
    with pytest.raises(OSError, match="private run directory"):
        run_clean_room(
            acquired,
            policy([command("one", "print(1)")]),
            producer_id="producer",
            verifier_id="verifier",
            evidence_root=evidence,
            isolation=CleanRoomIsolation(True, True),
        )
    assert _fd_snapshot() == before


def test_literal_argv_is_not_executed(source):
    acquired, evidence = source
    literal = "a b;$(touch should-not-exist)|*"
    code = (
        "import os, pathlib; "
        "pathlib.Path(os.environ['PRISMATIC_ARTIFACT_ROOT'], 'result.txt').write_text('ok')"
    )
    run = run_clean_room(
        acquired,
        policy(
            [
                {
                    "id": "literal",
                    "argv": [sys.executable, "-c", code, literal],
                    "proof_class": "unit",
                    "timeout_seconds": 20,
                    "required": True,
                }
            ]
        ),
        producer_id="producer",
        verifier_id="verifier",
        evidence_root=evidence,
        isolation=CleanRoomIsolation(True, True),
    )
    assert run.marker == "CLEAN_ROOM_RUNNER_V1_OK"
    assert not (acquired.checkout_path / "should-not-exist").exists()
