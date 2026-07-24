"""Provider-neutral bounded clean-room command runner.

This module consumes an already acquired immutable checkout.  It deliberately has no
provider, approval, receipt, or policy-mutation surface.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator

from .source_acquisition import (
    AcquiredSource,
    SourceAcquisitionError,
    validate_acquired_source,
)

CLEAN_ROOM_RUNNER_V1_OK = "CLEAN_ROOM_RUNNER_V1_OK"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|authorization|bearer|credential)\s*[:=]\s*[^\s]{8,}|(?:gh[pousr]_|sk-|AKIA)[A-Za-z0-9_-]{16,}"
)


class CleanRoomRunnerError(RuntimeError):
    def __init__(
        self, code: str, command_id: str | None = None, detail_code: str | None = None
    ) -> None:
        self.code, self.command_id, self.detail_code = code, command_id, detail_code
        super().__init__(":".join(x for x in (code, command_id, detail_code) if x))


@dataclass(frozen=True)
class CleanRoomIsolation:
    network_isolation_enforced: bool
    filesystem_isolation_enforced: bool


@dataclass(frozen=True)
class RunnerLimits:
    total_timeout_seconds: float = 900.0
    max_stdout_bytes: int = 4 * 1024 * 1024
    max_stderr_bytes: int = 4 * 1024 * 1024
    max_total_output_bytes: int = 8 * 1024 * 1024
    max_artifact_bytes: int = 32 * 1024 * 1024
    max_total_artifact_bytes: int = 128 * 1024 * 1024
    terminate_grace_seconds: float = 2.0


@dataclass(frozen=True)
class EvidenceDigest:
    algorithm: str
    value: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactEvidence:
    """Digest-only binding for one policy-named clean-room artifact."""

    name: str
    digest: EvidenceDigest
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class ToolchainEntry:
    argv0: str
    resolved_path: str
    digest: EvidenceDigest
    mode: int


@dataclass(frozen=True)
class CommandExecution:
    command_id: str
    argv: tuple[str, ...]
    proof_class: str
    working_directory: str
    exit_code: int
    started_at: str
    finished_at: str
    stdout_log: Path
    stderr_log: Path
    stdout_digest: EvidenceDigest
    stderr_digest: EvidenceDigest
    toolchain: ToolchainEntry


@dataclass(frozen=True)
class CleanRoomRun:
    marker: str
    policy_id: str
    policy_version: str
    policy_digest: EvidenceDigest
    repository_id: str
    candidate_sha: str
    tree_sha: str
    checkout_id: str
    source_acquisition_digest: str
    producer_id: str
    verifier_id: str
    environment_digest: EvidenceDigest
    toolchain_digest: EvidenceDigest
    commands: tuple[CommandExecution, ...]
    artifacts: tuple[ArtifactEvidence, ...]
    evidence_root: Path
    started_at: str
    finished_at: str


def _fail(code: str, command_id: str | None = None, detail: str | None = None) -> None:
    raise CleanRoomRunnerError(code, command_id, detail)


def _sha(data: bytes) -> EvidenceDigest:
    return EvidenceDigest(
        "sha256", "sha256:" + hashlib.sha256(data).hexdigest(), len(data)
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _schema() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "provider-neutral-verification-policy.schema.json"
    )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _fail("policy_schema_unavailable")


def _artifact_name(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        _fail("unsafe_artifact_requirement")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("unsafe_artifact_requirement")
    assert type(value) is str
    return value


def _validate_policy(
    policy: Mapping[str, object], source: AcquiredSource
) -> tuple[dict[str, object], list[dict[str, object]], tuple[str, ...]]:
    if type(policy) is not dict:
        _fail("invalid_policy")
    errors = list(Draft202012Validator(_schema()).iter_errors(policy))
    if errors:
        _fail("invalid_policy")
    evidence_raw = policy.get("evidence")
    if (
        type(evidence_raw) is not dict
        or evidence_raw.get("artifacts_required") is not True
    ):
        _fail("invalid_artifact_requirement")
    requirements = evidence_raw.get("digest_requirements")
    if type(requirements) is not list:
        _fail("invalid_artifact_requirement")
    names: list[str] = []
    for requirement in requirements:
        if type(requirement) is not dict:
            _fail("invalid_artifact_requirement")
        kind = requirement.get("kind")
        algorithm = requirement.get("algorithm")
        required = requirement.get("required")
        if (
            type(kind) is not str
            or type(algorithm) is not str
            or type(required) is not bool
        ):
            _fail("invalid_artifact_requirement")
        if kind == "artifact":
            if algorithm != "sha256" or required is not True:
                _fail("invalid_artifact_requirement")
            name = _artifact_name(requirement.get("name"))
            if name in names:
                _fail("duplicate_artifact_requirement")
            names.append(name)
    if not names:
        _fail("missing_artifact_requirement")
    copied = json.loads(_canonical(policy))
    if (
        copied["status"] != "active"
        or copied["repository"]["repository_id"] != source.repository_id
    ):
        _fail("policy_binding_invalid")
    commands = copied["commands"]
    if not isinstance(commands, list) or not commands:
        _fail("invalid_commands")
    seen: set[str] = set()
    for command in commands:
        if not isinstance(command, dict) or command.get("required") is not True:
            _fail("optional_command_rejected")
        ident = command.get("id")
        if type(ident) is not str or ident in seen:
            _fail("duplicate_command")
        seen.add(ident)
        if type(command.get("argv")) is not list or not all(
            type(x) is str for x in command["argv"]
        ):
            _fail("invalid_commands", ident)
    return copied, commands, tuple(sorted(names))


def _validate_limits(limits: RunnerLimits) -> None:
    if type(limits) is not RunnerLimits or any(
        type(getattr(limits, name)) not in (int, float) or getattr(limits, name) <= 0
        for name in limits.__dataclass_fields__
    ):
        _fail("invalid_limits")


class _SecretDetector:
    """Streaming credential detector which retains syntax only, never values."""

    _prefix = re.compile(
        rb"(?i)(?:api[_-]?key|token|secret|password|authorization|bearer|credential)\s*[:=]\s*"
    )
    _fixed = re.compile(rb"(?i)(?:gh[pousr]_|sk-|AKIA)[A-Za-z0-9_-]{16,}")

    def __init__(self) -> None:
        self._syntax_tail = b""
        self._value_bytes = 0
        self._awaiting_value = False

    def feed(self, chunk: bytes) -> bool:
        """Return true on a credential without retaining its matched value."""
        if self._awaiting_value:
            for byte in chunk:
                if chr(byte).isspace():
                    self._awaiting_value = False
                    self._value_bytes = 0
                    break
                self._value_bytes += 1
                if self._value_bytes >= 8:
                    return True
            else:
                return False
        data = self._syntax_tail + chunk
        if self._fixed.search(data):
            return True
        match = self._prefix.search(data)
        if match:
            # Do not preserve the candidate value: only count non-whitespace bytes.
            value = data[match.end() :]
            count = 0
            for byte in value:
                if chr(byte).isspace():
                    break
                count += 1
                if count >= 8:
                    return True
            # The value may begin in a later read; retain only the parser state.
            self._awaiting_value = True
            self._value_bytes = count
            self._syntax_tail = b""
            return False
        # The tail contains only syntax candidates, is deliberately bounded, and is
        # kept independently for stdout and stderr.
        self._syntax_tail = data[-128:]
        return False


def _private_dir(path: Path) -> Path:
    """Create/bind a private child below an already-authorized directory."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _fail("unsafe_evidence_root")
    try:
        before = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.geteuid()
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            _fail("unsafe_evidence_root")
        os.fchmod(fd, 0o700)
        after = os.fstat(fd)
        named_after = os.stat(path, follow_symlinks=False)
        if (
            stat.S_IMODE(after.st_mode) != 0o700
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or (named_after.st_dev, named_after.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            _fail("unsafe_evidence_root")
    except OSError:
        _fail("unsafe_evidence_root")
    finally:
        os.close(fd)
    return path


def _bind_evidence_root(evidence_root: Path, checkout: Path) -> Path:
    """Bind only an absolute, canonical, no-follow external evidence directory.

    The caller's authority boundary is checked before any mkdir/chmod/process spawn.
    Existing path components must be real directories owned by this effective user;
    the final leaf may be safely created through its bound parent descriptor.
    """
    if not isinstance(evidence_root, Path) or not evidence_root.is_absolute():
        _fail("unsafe_evidence_root")
    raw = os.fspath(evidence_root)
    canonical = os.path.realpath(raw)
    if raw != canonical or raw != os.path.normpath(raw):
        _fail("unsafe_evidence_root")
    checkout_canonical = os.path.realpath(os.fspath(checkout))
    try:
        if os.path.commonpath((canonical, checkout_canonical)) == checkout_canonical:
            _fail("unsafe_evidence_root")
    except ValueError:
        _fail("unsafe_evidence_root")

    parts = Path(canonical).parts
    try:
        parent_fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _fail("unsafe_evidence_root")
    try:
        for component in parts[1:-1]:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            except OSError:
                _fail("unsafe_evidence_root")
            current = os.fstat(next_fd)
            try:
                named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or (
                    current.st_dev,
                    current.st_ino,
                ) != (named.st_dev, named.st_ino):
                    _fail("unsafe_evidence_root")
            except OSError:
                _fail("unsafe_evidence_root")
            os.close(parent_fd)
            parent_fd = next_fd
        leaf = parts[-1]
        try:
            os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError:
            _fail("unsafe_evidence_root")
        try:
            root_fd = os.open(
                leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
            )
        except OSError:
            _fail("unsafe_evidence_root")
        try:
            before = os.fstat(root_fd)
            named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.geteuid()
                or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            ):
                _fail("unsafe_evidence_root")
            os.fchmod(root_fd, 0o700)
            after = os.fstat(root_fd)
            named_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if (
                stat.S_IMODE(after.st_mode) != 0o700
                or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or (named_after.st_dev, named_after.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                _fail("unsafe_evidence_root")
        except OSError:
            _fail("unsafe_evidence_root")
        finally:
            os.close(root_fd)
    finally:
        os.close(parent_fd)
    return Path(canonical)


def _safe_cwd(checkout: Path, relative: object) -> Path:
    if relative is None:
        return checkout
    if (
        type(relative) is not str
        or not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(ord(c) < 32 for c in relative)
    ):
        _fail("unsafe_working_directory")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("unsafe_working_directory")
    current = checkout
    for part in parts:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            _fail("unsafe_working_directory")
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            _fail("unsafe_working_directory")
    try:
        current.relative_to(checkout)
    except ValueError:
        _fail("unsafe_working_directory")
    return current


def _toolchain(argv0: str) -> ToolchainEntry:
    if not argv0 or "/" in argv0:
        path = Path(argv0)
    else:
        found = shutil.which(
            argv0, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        if not found:
            _fail("executable_not_found")
        path = Path(found)
    try:
        # A command name may resolve through a system-managed symlink (for example
        # /usr/bin/python3).  Bind the final regular executable and re-resolve it
        # before/after execution, so a replacement is still detected.
        path = path.resolve(strict=True)
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
            _fail("unsafe_executable")
        data = path.read_bytes()
    except OSError:
        _fail("executable_not_found")
    return ToolchainEntry(argv0, str(path), _sha(data), stat.S_IMODE(st.st_mode))


def _toolchain_unchanged(entry: ToolchainEntry) -> None:
    current = _toolchain(entry.argv0)
    if current != entry:
        _fail("toolchain_changed")


def _capture_artifacts(
    artifact_root: Path, names: tuple[str, ...], limits: RunnerLimits
) -> tuple[ArtifactEvidence, ...]:
    """Hash only exact policy names through no-follow descriptor traversal."""
    try:
        root_fd = os.open(artifact_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _fail("unsafe_artifact_root")
    total = 0
    captured: list[ArtifactEvidence] = []
    try:
        for name in names:
            current_fd = root_fd
            owned_fds: list[int] = []
            try:
                parts = name.split("/")
                for component in parts[:-1]:
                    try:
                        next_fd = os.open(
                            component,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=current_fd,
                        )
                    except OSError:
                        _fail("unsafe_artifact")
                    owned_fds.append(next_fd)
                    current_fd = next_fd
                try:
                    fd = os.open(
                        parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd
                    )
                except OSError:
                    _fail("missing_or_unsafe_artifact")
                try:
                    before = os.fstat(fd)
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_nlink != 1
                        or before.st_size > limits.max_artifact_bytes
                        or total + before.st_size > limits.max_total_artifact_bytes
                    ):
                        _fail("unsafe_artifact")
                    digest = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if (
                            size > limits.max_artifact_bytes
                            or total + size > limits.max_total_artifact_bytes
                        ):
                            _fail("artifact_overflow")
                        digest.update(chunk)
                    after = os.fstat(fd)
                    try:
                        named = os.stat(
                            parts[-1], dir_fd=current_fd, follow_symlinks=False
                        )
                    except OSError:
                        _fail("artifact_replaced")
                    identity = (
                        before.st_dev,
                        before.st_ino,
                        before.st_mode,
                        before.st_nlink,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    current_identity = (
                        after.st_dev,
                        after.st_ino,
                        after.st_mode,
                        after.st_nlink,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    named_identity = (named.st_dev, named.st_ino)
                    if (
                        identity != current_identity
                        or named_identity != identity[:2]
                        or size != before.st_size
                    ):
                        _fail("artifact_changed")
                    total += size
                    captured.append(
                        ArtifactEvidence(
                            name,
                            EvidenceDigest(
                                "sha256", "sha256:" + digest.hexdigest(), size
                            ),
                            stat.S_IMODE(before.st_mode),
                            before.st_uid,
                            before.st_gid,
                        )
                    )
                finally:
                    os.close(fd)
            finally:
                for owned_fd in reversed(owned_fds):
                    os.close(owned_fd)
    finally:
        os.close(root_fd)
    return tuple(captured)


def _environment(home: Path, temp: Path, artifact_root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temp),
        "TEMP": str(temp),
        "TMP": str(temp),
        "PRISMATIC_ARTIFACT_ROOT": str(artifact_root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _terminate(proc: subprocess.Popen[bytes], grace: float) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=max(grace, 0.1))
    except subprocess.TimeoutExpired:
        _fail("process_not_reaped")


def _execute(
    command: dict[str, object],
    checkout: Path,
    env: dict[str, str],
    logs: Path,
    deadline: float,
    limits: RunnerLimits,
) -> CommandExecution:
    """Execute with bounded, private file streaming; never buffer command output."""
    ident, argv = command["id"], command["argv"]
    assert isinstance(ident, str) and isinstance(argv, list)
    cwd = _safe_cwd(checkout, command.get("working_directory"))
    tool = _toolchain(argv[0])
    _toolchain_unchanged(tool)
    command_deadline = min(
        deadline, time.monotonic() + float(command["timeout_seconds"])
    )
    if command_deadline <= time.monotonic():
        _fail("total_timeout", ident)
    stdout_path, stderr_path = logs / (ident + ".stdout"), logs / (ident + ".stderr")
    started = _now()
    try:
        proc = subprocess.Popen(
            tuple(argv),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError:
        _fail("spawn_failed", ident)
    assert proc.stdout and proc.stderr
    selector = selectors.DefaultSelector()
    output = {"out": stdout_path, "err": stderr_path}
    fds = {
        name: os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        for name, path in output.items()
    }
    sizes = {"out": 0, "err": 0}
    hashes = {"out": hashlib.sha256(), "err": hashlib.sha256()}
    detectors = {"out": _SecretDetector(), "err": _SecretDetector()}
    failed: str | None = None
    code: int | None = None
    try:
        selector.register(proc.stdout, selectors.EVENT_READ, "out")
        selector.register(proc.stderr, selectors.EVENT_READ, "err")
        while selector.get_map():
            if time.monotonic() >= command_deadline:
                failed = "command_timeout"
                break
            for key, _ in selector.select(
                timeout=min(0.05, command_deadline - time.monotonic())
            ):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                name = str(key.data)
                if (
                    sizes[name] + len(chunk) > getattr(limits, f"max_std{name}_bytes")
                    or sizes["out"] + sizes["err"] + len(chunk)
                    > limits.max_total_output_bytes
                ):
                    failed = "output_overflow"
                    break
                if detectors[name].feed(chunk):
                    failed = "secret_output"
                    break
                os.write(fds[name], chunk)
                hashes[name].update(chunk)
                sizes[name] += len(chunk)
            if failed:
                break
        if failed:
            _terminate(proc, limits.terminate_grace_seconds)
            _fail(failed, ident)
        code = proc.wait(timeout=max(0.1, command_deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _terminate(proc, limits.terminate_grace_seconds)
        _fail("command_timeout", ident)
    finally:
        selector.close()
        for fd in fds.values():
            os.close(fd)
    if code != 0:
        _fail("command_failed", ident)
    _toolchain_unchanged(tool)
    return CommandExecution(
        ident,
        tuple(argv),
        str(command["proof_class"]),
        str(cwd.relative_to(checkout)) if cwd != checkout else ".",
        code,
        started,
        _now(),
        stdout_path,
        stderr_path,
        EvidenceDigest("sha256", "sha256:" + hashes["out"].hexdigest(), sizes["out"]),
        EvidenceDigest("sha256", "sha256:" + hashes["err"].hexdigest(), sizes["err"]),
        tool,
    )


def run_clean_room(
    source: AcquiredSource,
    policy: Mapping[str, object],
    *,
    producer_id: str,
    verifier_id: str,
    evidence_root: Path,
    isolation: CleanRoomIsolation,
    limits: RunnerLimits = RunnerLimits(),
) -> CleanRoomRun:
    if type(source) is not AcquiredSource:
        _fail("invalid_acquired_source")
    if (
        type(producer_id) is not str
        or type(verifier_id) is not str
        or not _ID.fullmatch(producer_id)
        or not _ID.fullmatch(verifier_id)
        or producer_id == verifier_id
    ):
        _fail("invalid_identity")
    if (
        type(isolation) is not CleanRoomIsolation
        or isolation.network_isolation_enforced is not True
        or isolation.filesystem_isolation_enforced is not True
    ):
        _fail("isolation_not_enforced")
    _validate_limits(limits)
    checked_policy, commands, artifact_names = _validate_policy(policy, source)
    # Validation immediately precedes command zero and is repeated after every command.
    try:
        validate_acquired_source(source)
    except SourceAcquisitionError as error:
        _fail("source_validation_failed", detail=error.code)
    root = _bind_evidence_root(evidence_root, source.checkout_path)
    with tempfile.TemporaryDirectory(prefix="clean-room-", dir=root) as temp_name:
        temp = Path(temp_name)
        home = _private_dir(temp / "home")
        scratch = _private_dir(temp / "tmp")
        artifact_root = _private_dir(temp / "artifacts")
        logs = _private_dir(temp / "logs")
        env = _environment(home, scratch, artifact_root)
        started = _now()
        deadline = time.monotonic() + limits.total_timeout_seconds
        executions: list[CommandExecution] = []
        for command in commands:
            executions.append(
                _execute(command, source.checkout_path, env, logs, deadline, limits)
            )
            try:
                validate_acquired_source(source)
            except SourceAcquisitionError as error:
                _fail("source_validation_failed", str(command["id"]), error.code)
        artifacts = _capture_artifacts(artifact_root, artifact_names, limits)
        try:
            validate_acquired_source(source)
        except SourceAcquisitionError as error:
            _fail("source_validation_failed", detail=error.code)
        # Promote only complete, non-secret successful logs atomically.
        final_logs = _private_dir(root / "logs")
        promoted: list[CommandExecution] = []
        for execution in executions:
            out, err = (
                final_logs / execution.stdout_log.name,
                final_logs / execution.stderr_log.name,
            )
            os.replace(execution.stdout_log, out)
            os.replace(execution.stderr_log, err)
            promoted.append(
                CommandExecution(
                    **{**execution.__dict__, "stdout_log": out, "stderr_log": err}
                )
            )
        try:
            validate_acquired_source(source)
        except SourceAcquisitionError as error:
            _fail("source_validation_failed", detail=error.code)
        tools = _sha(_canonical([entry.toolchain.digest.value for entry in promoted]))
        run = CleanRoomRun(
            CLEAN_ROOM_RUNNER_V1_OK,
            str(checked_policy["policy_id"]),
            str(checked_policy["policy_version"]),
            _sha(_canonical(checked_policy)),
            source.repository_id,
            source.candidate_sha,
            source.tree_sha,
            source.checkout_id,
            source.source_acquisition_digest,
            producer_id,
            verifier_id,
            _sha(_canonical(env)),
            tools,
            tuple(promoted),
            artifacts,
            root,
            started,
            _now(),
        )
        if type(run.marker) is not str or run.marker != CLEAN_ROOM_RUNNER_V1_OK:
            _fail("invalid_result")
        return run
