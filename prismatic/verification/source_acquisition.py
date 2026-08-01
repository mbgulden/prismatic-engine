"""Clean-room acquisition of immutable Git source objects.

This module intentionally does not execute verification commands or make decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
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
    """Return an existing absolute path with no control characters or symlinks."""
    if not path.is_absolute() or any(ord(char) < 32 for char in str(path)):
        _fail("invalid_path", f"{kind} must be an absolute control-free path")
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


def _validated_auth_environment(
    request: SourceAcquisitionRequest,
    *,
    askpass_helper: Path | None,
    ssh_auth_socket: Path | None,
) -> dict[str, str]:
    """Validate opaque caller-owned handles and return only scrubbed env additions."""
    if askpass_helper is None and ssh_auth_socket is None:
        return {}
    if request.source_kind != "provider_remote":
        _fail("auth_not_allowed", "authentication is only valid for provider remotes")
    additions: dict[str, str] = {}
    if askpass_helper is not None:
        helper = _safe_path(askpass_helper, kind="askpass helper")
        mode = os.lstat(helper).st_mode
        if not stat.S_ISREG(mode) or not os.access(helper, os.X_OK):
            _fail("invalid_askpass_helper")
        # Git/SSH receive a path only; no token/password is accepted by this API.
        additions.update({"GIT_ASKPASS": str(helper), "SSH_ASKPASS": str(helper)})
    if ssh_auth_socket is not None:
        socket_path = _safe_path(ssh_auth_socket, kind="ssh auth socket")
        if not stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            _fail("invalid_ssh_auth_socket")
        additions["SSH_AUTH_SOCK"] = str(socket_path)
    return additions


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


_SAFE_ENVIRONMENT_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"})


def _environment(home: Path) -> dict[str, str]:
    """Build a clean-room Git environment from an explicit safe allowlist.

    Authentication is deliberately absent here.  Callers may add only the validated
    opaque paths returned by :func:`_validated_auth_environment`.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _SAFE_ENVIRONMENT_KEYS and isinstance(value, str)
    }
    # A minimal inherited environment may omit PATH; os.defpath keeps Git lookup
    # portable without importing any caller-controlled credential/config variables.
    env.setdefault("PATH", os.defpath)
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
    allowed_returncodes: frozenset[int] = frozenset({0}),
) -> bytes:
    """Run without shell expansion, enforcing immutable command and total deadlines."""
    launch_monotonic = time.monotonic()
    if deadline <= launch_monotonic:
        _fail("total_timeout", "deadline exceeded")
    command_deadline = launch_monotonic + policy.command_timeout_seconds
    effective_deadline = min(command_deadline, deadline)
    timeout_code = "command_timeout" if command_deadline < deadline else "total_timeout"
    limit = min(
        policy.max_command_output_bytes,
        policy.max_tree_listing_bytes if binary else policy.max_command_output_bytes,
    )
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    retained = bytearray()
    total = 0
    selector = selectors.DefaultSelector()

    def remaining_to_deadline() -> float:
        return effective_deadline - time.monotonic()

    def terminate_and_reap() -> None:
        """Kill the isolated session and discard pipes without buffering child output."""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # The direct child is the session leader; this fallback still guarantees reap.
            proc.kill()
            proc.wait()

    assert proc.stdout is not None and proc.stderr is not None
    if os.name == "nt":
        try:
            out_bytes, err_bytes = proc.communicate(timeout=remaining_to_deadline())
            retained.extend(out_bytes or b"")
            retained.extend(err_bytes or b"")
            if len(retained) > limit:
                _fail("tree_listing_overflow" if binary else "output_overflow", label)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _fail(timeout_code, label)
    else:
        selector.register(proc.stdout, selectors.EVENT_READ)
        selector.register(proc.stderr, selectors.EVENT_READ)
        try:
            while selector.get_map():
                remaining = remaining_to_deadline()
                if remaining <= 0:
                    terminate_and_reap()
                    _fail(timeout_code, label)
                events = selector.select(remaining)
                if not events:
                    if remaining_to_deadline() <= 0:
                        terminate_and_reap()
                        _fail(timeout_code, label)
                    continue
                for key, _ in events:
                    chunk = os.read(key.fd, 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > limit:
                        terminate_and_reap()
                        _fail(
                            "tree_listing_overflow" if binary else "output_overflow", label
                        )
                    retained.extend(chunk)
            remaining = remaining_to_deadline()
            if remaining <= 0:
                terminate_and_reap()
                _fail(timeout_code, label)
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                terminate_and_reap()
                _fail(timeout_code, label)
        finally:
            selector.close()
    if proc.returncode not in allowed_returncodes:
        _fail("git_command_failed", label)
    return bytes(retained)


def _git(args: list[str], **kwargs: object) -> bytes:
    return _run(["git", *args], **kwargs)  # type: ignore[arg-type]


def _git_dir(
    repo: Path, *, policy: SourceAcquisitionPolicy, env: dict[str, str], deadline: float
) -> Path:
    value = (
        _git(
            ["rev-parse", "--absolute-git-dir"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="git-dir",
        )
        .decode()
        .strip()
    )
    return _safe_path(Path(value), kind="git directory")


def _assert_detached(
    repo: Path, *, policy: SourceAcquisitionPolicy, env: dict[str, str], deadline: float
) -> None:
    # symbolic-ref exits 1 when HEAD is detached and 0 when it is attached.
    attached = _git(
        ["symbolic-ref", "-q", "HEAD"],
        cwd=repo,
        env=env,
        policy=policy,
        deadline=deadline,
        label="symbolic-head",
        allowed_returncodes=frozenset({0, 1}),
    )
    if attached:
        _fail("attached_head")


def _check_complete_objects(
    repo: Path,
    candidate: str,
    *,
    policy: SourceAcquisitionPolicy,
    env: dict[str, str],
    deadline: float,
) -> None:
    git_dir = _git_dir(repo, policy=policy, env=env, deadline=deadline)
    config = (
        _git(
            ["config", "--null", "--list"],
            cwd=repo,
            env=env,
            policy=policy,
            deadline=deadline,
            label="object-config",
        )
        .decode("utf-8", "strict")
        .casefold()
    )
    forbidden = (
        "extensions.partialclone",
        "extensions.partial",
        ".promisor",
        ".partialclonefilter",
        "partialclonefilter=",
    )
    if any(item in config for item in forbidden):
        _fail("incomplete_objects")
    alternates = git_dir / "objects" / "info" / "alternates"
    if alternates.exists() or any((git_dir / "objects").rglob("*.promisor")):
        _fail("incomplete_objects")
    # --missing=print enumerates the full reachable closure without lazy fetching.
    closure = _git(
        ["rev-list", "--objects", "--missing=print", candidate],
        cwd=repo,
        env=env,
        policy=policy,
        deadline=deadline,
        label="reachability",
    )
    if any(line.startswith(b"?") for line in closure.splitlines()):
        _fail("missing_reachable_object")


@dataclass(frozen=True)
class _BundleFence:
    device: int
    inode: int
    size: int
    mtime_ns: int
    digest: str


def _bundle_fence(bundle: Path) -> _BundleFence:
    bundle = _safe_path(bundle, kind="bundle")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(bundle, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("invalid_bundle")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 64 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        _fail("bundle_changed")
    return _BundleFence(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        digest.hexdigest(),
    )


def _assert_bundle_unchanged(bundle: Path, fence: _BundleFence) -> None:
    if _bundle_fence(bundle) != fence:
        _fail("bundle_changed")


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
    git_dir = _git_dir(repo, policy=policy, env=env, deadline=deadline)
    if shallow != "false" or (git_dir / "shallow").exists():
        _fail("incomplete_objects")
    _assert_detached(repo, policy=policy, env=env, deadline=deadline)
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
    _check_complete_objects(repo, head, policy=policy, env=env, deadline=deadline)
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
    auth_env = _validated_auth_environment(
        request, askpass_helper=askpass_helper, ssh_auth_socket=ssh_auth_socket
    )
    destination: Path | None = None
    bundle_fence: _BundleFence | None = None
    bundle_ref_object: str | None = None
    deadline = time.monotonic() + policy.total_timeout_seconds
    try:
        destination = Path(tempfile.mkdtemp(prefix="source-", dir=workspace_root))
        os.chmod(destination, 0o700)
        home = destination / "home"
        (home / "empty-template").mkdir(parents=True, mode=0o700)
        env = _environment(home)
        env.update(auth_env)
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
            bundle_fence = _bundle_fence(bundle)
            _git(
                ["bundle", "verify", str(bundle)],
                cwd=repo,
                env=env,
                policy=policy,
                deadline=deadline,
                label="bundle-verify",
            )
            heads = (
                _git(
                    ["bundle", "list-heads", str(bundle)],
                    cwd=repo,
                    env=env,
                    policy=policy,
                    deadline=deadline,
                    label="bundle-heads",
                )
                .decode("utf-8", "strict")
                .splitlines()
            )
            exact_entries = [
                line.split()
                for line in heads
                if len(line.split()) == 2 and line.split()[1] == request.source_ref
            ]
            if len(exact_entries) != 1 or not _SHA.fullmatch(exact_entries[0][0]):
                _fail("bundle_ref_mismatch")
            # list-heads reports the exact ref object.  For annotated tags this is
            # deliberately the tag object, which is checked before later peeling.
            bundle_ref_object = exact_entries[0][0]
            _assert_bundle_unchanged(bundle, bundle_fence)
            fetch_source = locator
        else:
            scheme = urlsplit(locator).scheme
            if scheme not in policy.allowed_remote_schemes:
                _fail("remote_scheme_not_allowed")
            fetch_source = locator
        private_ref = "refs/prismatic/acquired/" + uuid.uuid4().hex
        if bundle_fence is not None:
            _assert_bundle_unchanged(Path(locator), bundle_fence)
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
        if bundle_fence is not None:
            _assert_bundle_unchanged(Path(locator), bundle_fence)
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
        if bundle_ref_object is not None and ref_object != bundle_ref_object:
            _fail("bundle_ref_mismatch")
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
