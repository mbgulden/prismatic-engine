"""Clean-room acquisition of immutable Git source objects.

This module intentionally does not execute verification commands or make decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

SOURCE_ACQUISITION_V1_OK = "SOURCE_ACQUISITION_V1_OK"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(
    r"^refs/(?:heads|tags)/[^/\\\x00-\x1f ~^:?*\[]+(?:/[^/\\\x00-\x1f ~^:?*\[]+)*$"
)
_KINDS = frozenset({"provider_remote", "local_bare_repository", "offline_git_bundle"})
_PROVIDERS = frozenset(
    {"github", "gitlab", "bitbucket", "forgejo", "gitea", "other", "none"}
)


class SourceAcquisitionError(RuntimeError):
    def __init__(self, code: str, message: str = "source acquisition failed") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SourceAcquisitionPolicy:
    repository_id: str
    allowed_source_kinds: frozenset[str]
    allowed_source_providers: frozenset[str]
    require_full_git_objects: bool = True
    allowed_remote_schemes: frozenset[str] = frozenset({"https", "ssh"})
    command_timeout_seconds: float = 60.0
    total_timeout_seconds: float = 300.0
    max_command_output_bytes: int = 1_048_576
    max_tree_entries: int = 200_000
    max_tree_listing_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True)
class SourceAcquisitionRequest:
    source_kind: str
    source_provider: str
    source_locator: str
    source_ref: str
    candidate_sha: str
    tree_sha: str


@dataclass(frozen=True)
class AcquiredSource:
    marker: str
    schema_version: str
    repository_id: str
    source_kind: str
    source_provider: str
    source_locator: str
    source_ref: str
    ref_object_sha: str
    candidate_sha: str
    tree_sha: str
    git_object_format: str
    checkout_id: str
    checkout_path: Path
    source_acquisition_digest: str


def _fail(code: str, message: str = "source acquisition failed") -> None:
    raise SourceAcquisitionError(code, message)


def _safe_path(path: Path, *, kind: str) -> Path:
    if not path.is_absolute():
        _fail("invalid_path", f"{kind} must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            _fail("invalid_path", f"{kind} component missing")
        if stat.S_ISLNK(mode):
            _fail("symlink_path", f"{kind} contains symlink")
    return path


def _canonical_locator(request: SourceAcquisitionRequest) -> str:
    if request.source_kind != "provider_remote":
        return str(_safe_path(Path(request.source_locator), kind="source locator"))
    parsed = urlsplit(request.source_locator)
    if (
        parsed.scheme not in {"https", "ssh", "file"}
        or (parsed.scheme != "file" and not parsed.netloc)
        or (parsed.scheme == "file" and not parsed.path)
    ):
        _fail("invalid_locator", "unsupported remote locator")
    if (
        parsed.query
        or parsed.fragment
        or parsed.password
        or (parsed.username and parsed.scheme == "https")
    ):
        _fail("unsafe_locator", "remote credentials or decorations are not allowed")
    if request.source_locator.startswith("ext::") or any(
        c in request.source_locator for c in "\r\n\x00"
    ):
        _fail("unsafe_locator", "unsafe remote transport")
    return parsed.geturl()


def _validate_inputs(
    request: SourceAcquisitionRequest, policy: SourceAcquisitionPolicy
) -> str:
    if not policy.require_full_git_objects:
        _fail("full_objects_required")
    if (
        request.source_kind not in _KINDS
        or request.source_kind not in policy.allowed_source_kinds
    ):
        _fail("source_kind_not_allowed")
    if (
        request.source_provider not in _PROVIDERS
        or request.source_provider not in policy.allowed_source_providers
    ):
        _fail("source_provider_not_allowed")
    if (request.source_kind == "provider_remote") != (
        request.source_provider != "none"
    ):
        _fail("incoherent_source")
    if not _REF.fullmatch(request.source_ref):
        _fail("invalid_ref")
    if not _SHA.fullmatch(request.candidate_sha) or not _SHA.fullmatch(
        request.tree_sha
    ):
        _fail("invalid_sha")
    if (
        not policy.repository_id
        or policy.command_timeout_seconds <= 0
        or policy.total_timeout_seconds <= 0
    ):
        _fail("invalid_policy")
    return _canonical_locator(request)


def _environment(home: Path) -> dict[str, str]:
    forbidden = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_OBJECT_DIRECTORY",
        "GIT_CONFIG",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in forbidden
        and not any(
            x in k.upper() for x in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")
        )
    }
    env.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(home / "empty-config"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TEMPLATE_DIR": str(home / "empty-template"),
        }
    )
    return env


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    policy: SourceAcquisitionPolicy,
    deadline: float,
    label: str,
    binary: bool = False,
) -> bytes:
    remaining = min(policy.command_timeout_seconds, deadline - time.monotonic())
    if remaining <= 0:
        _fail("total_timeout", "deadline exceeded")
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        _fail("command_timeout", label)
    if len(out) + len(err) > policy.max_command_output_bytes and not binary:
        _fail("output_overflow", label)
    if proc.returncode:
        _fail("git_command_failed", label)
    return out


def _git(args: list[str], **kwargs: object) -> bytes:
    return _run(["git", *args], **kwargs)  # type: ignore[arg-type]


def _digest(source: AcquiredSource) -> str:
    payload = {
        "schema_version": source.schema_version,
        "repository_id": source.repository_id,
        "source_kind": source.source_kind,
        "source_provider": source.source_provider,
        "source_locator": source.source_locator,
        "source_ref": source.source_ref,
        "ref_object_sha": source.ref_object_sha,
        "candidate_sha": source.candidate_sha,
        "tree_sha": source.tree_sha,
        "git_object_format": source.git_object_format,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return (
        "sha256:"
        + hashlib.sha256(b"prismatic.source-acquisition.v1\0" + encoded).hexdigest()
    )


def _check_repository(
    repo: Path,
    source: AcquiredSource | None,
    *,
    policy: SourceAcquisitionPolicy,
    env: dict[str, str],
    deadline: float,
) -> tuple[str, str, str]:
    fmt = (
        _git(
            ["rev-parse", "--show-object-format"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="object-format",
        )
        .decode()
        .strip()
    )
    if fmt != "sha1":
        _fail("unsupported_object_format")
    shallow = (
        _git(
            ["rev-parse", "--is-shallow-repository"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="shallow",
        )
        .decode()
        .strip()
    )
    if (
        shallow != "false"
        or (repo / ".git" / "objects" / "info" / "alternates").exists()
    ):
        _fail("incomplete_objects")
    head = (
        _git(
            ["rev-parse", "HEAD"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="head",
        )
        .decode()
        .strip()
    )
    tree = (
        _git(
            ["rev-parse", "HEAD^{tree}"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="tree",
        )
        .decode()
        .strip()
    )
    status = _git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        env=env,
        policy=policy,
        deadline=deadline,
        label="status",
    ).decode()
    if status:
        _fail("dirty_checkout")
    return fmt, head, tree


def _check_tree(
    repo: Path, *, policy: SourceAcquisitionPolicy, env: dict[str, str], deadline: float
) -> None:
    data = _run(
        ["git", "ls-tree", "-rz", "--full-tree", "-r", "HEAD"],
        cwd=repo,
        env=env,
        policy=policy,
        deadline=deadline,
        label="tree-listing",
        binary=True,
    )
    if len(data) > policy.max_tree_listing_bytes:
        _fail("tree_listing_overflow")
    rows = [row for row in data.split(b"\0") if row]
    if len(rows) > policy.max_tree_entries:
        _fail("tree_entry_overflow")
    for row in rows:
        try:
            meta, raw_path = row.split(b"\t", 1)
            mode, typ, _sha = meta.split()
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            _fail("unsafe_tree")
        if mode not in {b"100644", b"100755"} or typ != b"blob":
            _fail("unsafe_tree")
        parts = path.split("/")
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or any(
                not p
                or p in {".", ".."}
                or any(ord(c) < 32 for c in p)
                or p.casefold() == ".git"
                for p in parts
            )
        ):
            _fail("unsafe_tree")


def acquire_source(
    request: SourceAcquisitionRequest,
    policy: SourceAcquisitionPolicy,
    *,
    workspace_root: Path,
    askpass_helper: Path | None = None,
    ssh_auth_socket: Path | None = None,
) -> AcquiredSource:
    locator = _validate_inputs(request, policy)
    _safe_path(workspace_root, kind="workspace root")
    if not workspace_root.is_dir():
        _fail("invalid_workspace")
    if askpass_helper is not None or ssh_auth_socket is not None:
        _fail(
            "auth_not_supported",
            "auth helpers are not enabled by this bounded implementation",
        )
    destination: Path | None = None
    deadline = time.monotonic() + policy.total_timeout_seconds
    try:
        destination = Path(tempfile.mkdtemp(prefix="source-", dir=workspace_root))
        os.chmod(destination, 0o700)
        home = destination / "home"
        (home / "empty-template").mkdir(parents=True, mode=0o700)
        env = _environment(home)
        repo = destination / "checkout"
        _git(
            ["init", "--quiet", str(repo)],
            cwd=destination,
            env=env,
            policy=policy,
            deadline=deadline,
            label="init",
        )
        if request.source_kind == "local_bare_repository":
            source = Path(locator)
            bare = (
                _git(
                    ["-C", str(source), "rev-parse", "--is-bare-repository"],
                    cwd=destination,
                    env=env,
                    policy=policy,
                    deadline=deadline,
                    label="source-bare",
                )
                .decode()
                .strip()
            )
            if bare != "true":
                _fail("non_bare_source")
            if (
                _git(
                    ["-C", str(source), "rev-parse", "--is-shallow-repository"],
                    cwd=destination,
                    env=env,
                    policy=policy,
                    deadline=deadline,
                    label="source-shallow",
                )
                .decode()
                .strip()
                != "false"
            ):
                _fail("shallow_source")
            fetch_source = locator
        elif request.source_kind == "offline_git_bundle":
            bundle = Path(locator)
            if not stat.S_ISREG(os.lstat(bundle).st_mode):
                _fail("invalid_bundle")
            _git(
                ["bundle", "verify", str(bundle)],
                cwd=repo,
                env=env,
                policy=policy,
                deadline=deadline,
                label="bundle-verify",
            )
            fetch_source = locator
        else:
            scheme = urlsplit(locator).scheme
            if scheme not in policy.allowed_remote_schemes:
                _fail("remote_scheme_not_allowed")
            fetch_source = locator
        private_ref = "refs/prismatic/acquired/" + uuid.uuid4().hex
        _git(
            [
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                fetch_source,
                f"{request.source_ref}:{private_ref}",
            ],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="fetch",
        )
        ref_object = (
            _git(
                ["rev-parse", private_ref],
                cwd=repo,
                env=env,
                policy=policy,
                deadline=deadline,
                label="private-ref",
            )
            .decode()
            .strip()
        )
        candidate = (
            _git(
                ["rev-parse", f"{private_ref}^{{commit}}"],
                cwd=repo,
                env=env,
                policy=policy,
                deadline=deadline,
                label="peel",
            )
            .decode()
            .strip()
        )
        if candidate != request.candidate_sha:
            _fail("candidate_mismatch")
        if (
            _git(
                ["cat-file", "-t", candidate],
                cwd=repo,
                env=env,
                policy=policy,
                deadline=deadline,
                label="candidate-type",
            )
            .decode()
            .strip()
            != "commit"
        ):
            _fail("candidate_not_commit")
        tree = (
            _git(
                ["rev-parse", f"{candidate}^{{tree}}"],
                cwd=repo,
                env=env,
                policy=policy,
                deadline=deadline,
                label="candidate-tree",
            )
            .decode()
            .strip()
        )
        if tree != request.tree_sha:
            _fail("tree_mismatch")
        _git(
            ["fsck", "--full", "--strict"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="fsck",
        )
        _git(
            ["config", "core.protectHFS", "true"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="protect-hfs",
        )
        _git(
            ["config", "core.protectNTFS", "true"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="protect-ntfs",
        )
        _git(
            ["checkout", "--detach", "--quiet", candidate],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="checkout",
        )
        _check_tree(repo, policy=policy, env=env, deadline=deadline)
        fmt, head, checked_tree = _check_repository(
            repo, None, policy=policy, env=env, deadline=deadline
        )
        if head != candidate or checked_tree != tree:
            _fail("checkout_mismatch")
        source = AcquiredSource(
            SOURCE_ACQUISITION_V1_OK,
            "1.0",
            policy.repository_id,
            request.source_kind,
            request.source_provider,
            locator,
            request.source_ref,
            ref_object,
            candidate,
            tree,
            fmt,
            destination.name,
            repo,
            "",
        )
        source = replace(source, source_acquisition_digest=_digest(source))
        validate_acquired_source(source)
        return source
    except Exception:
        if destination is not None:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def validate_acquired_source(source: AcquiredSource) -> None:
    if (
        source.marker != SOURCE_ACQUISITION_V1_OK
        or source.schema_version != "1.0"
        or not _SHA.fullmatch(source.candidate_sha)
        or not _SHA.fullmatch(source.tree_sha)
        or not _SHA.fullmatch(source.ref_object_sha)
    ):
        _fail("invalid_acquired_source")
    repo = source.checkout_path
    if not repo.is_dir() or repo.is_symlink():
        _fail("checkout_missing")
    validation_home = repo.parent / "validation-home"
    (validation_home / "empty-template").mkdir(parents=True, exist_ok=True)
    env = _environment(validation_home)
    policy = SourceAcquisitionPolicy(
        source.repository_id, frozenset(_KINDS), frozenset(_PROVIDERS)
    )
    deadline = time.monotonic() + policy.total_timeout_seconds
    fmt, head, tree = _check_repository(
        repo, source, policy=policy, env=env, deadline=deadline
    )
    _check_tree(repo, policy=policy, env=env, deadline=deadline)
    if (
        fmt != source.git_object_format
        or head != source.candidate_sha
        or tree != source.tree_sha
        or _digest(source) != source.source_acquisition_digest
    ):
        _fail("source_changed")
