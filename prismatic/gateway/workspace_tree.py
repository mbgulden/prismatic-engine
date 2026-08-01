"""Opaque, descriptor-relative workspace tree boundary.

The public surface in this module deliberately never returns configured host paths.
Registry and filesystem failures are translated to small generic errors by callers.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import platform
import re
import stat
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

REGISTRY_ENV = "PRISMATIC_WORKSPACE_REGISTRY_FILE"
REGISTRY_MAX_BYTES = 128 * 1024
MAX_WORKSPACES = 64
MAX_DISPLAY_NAME_CHARS = 256
MAX_PATH_CHARS = 4096
MAX_PATH_COMPONENTS = 128
MAX_CHILDREN = 200
MAX_PREVIEW_BYTES = 524288
WORKSPACE_ID_RE = re.compile(r"^ws-[0-9a-f]{32}$")
PREVIEW_EXTENSIONS = frozenset(
    {
        ".cfg",
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
IGNORED_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "venv",
    }
)
_SENSITIVE_SUFFIXES = (
    ".db",
    ".sql",
    ".sqlite",
    ".sqlite3",
    ".journal",
    ".wal",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)
_SENSITIVE_EXACT = frozenset(
    {
        "authorized_keys",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "private_key",
        "secrets",
        "session",
        "sessions",
        "shadow",
    }
)
_SENSITIVE_WORD = re.compile(
    r"(?:^|[._-])(credential|secret|token|key|password|passwd|private[-_]?key|cookie|session|ssh|aws|gcp|azure)(?:$|[._-])",
    re.IGNORECASE,
)


class WorkspaceTreeError(Exception):
    """A path-free error suitable for translation into an HTTP response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RegistryError(WorkspaceTreeError):
    def __init__(self) -> None:
        super().__init__(503, "workspace registry unavailable")


class _DuplicateKey(ValueError):
    pass


@dataclass
class Workspace:
    workspace_id: str
    display_name: str
    fd: int
    mount_id: int
    identity: tuple[int, int]
    enabled: bool


class WorkspaceRegistry:
    """Request-scoped registry whose approved root descriptors remain pinned."""

    def __init__(self, workspaces: list[Workspace]) -> None:
        self._workspaces = workspaces
        self._by_id = {item.workspace_id: item for item in workspaces if item.enabled}

    def __enter__(self) -> WorkspaceRegistry:
        return self

    def __exit__(self, *_: object) -> None:
        for workspace in self._workspaces:
            with contextlib.suppress(OSError):
                os.close(workspace.fd)
        self._workspaces.clear()
        self._by_id.clear()

    @property
    def enabled(self) -> list[Workspace]:
        return [item for item in self._workspaces if item.enabled]

    def resolve(self, workspace_id: str) -> Workspace:
        if not isinstance(workspace_id, str) or not WORKSPACE_ID_RE.fullmatch(
            workspace_id
        ):
            raise WorkspaceTreeError(400, "invalid workspace identifier")
        workspace = self._by_id.get(workspace_id)
        if workspace is None:
            raise WorkspaceTreeError(404, "workspace not found")
        return workspace


# Linux UAPI values.
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_OPENAT2_RESOLVE = (
    _RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_XDEV
)
_AT_EMPTY_PATH = 0x1000
_STATX_MNT_ID = 0x1000
_STATX_MNT_ID_UNIQUE = 0x4000


class _OpenHow(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


class _StatxTimestamp(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    _fields_ = [
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        ("stx_mnt_id", ctypes.c_uint64),
        ("stx_dio_mem_align", ctypes.c_uint32),
        ("stx_dio_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 12),
    ]


try:
    _LIBC = ctypes.CDLL(None, use_errno=True)
except (TypeError, OSError):
    _LIBC = None
_ARCH_SYSCALLS = {
    "x86_64": (437, 332),
    "amd64": (437, 332),
    "aarch64": (437, 291),
    "arm64": (437, 291),
    "riscv64": (437, 291),
    "ppc64": (437, 383),
    "ppc64le": (437, 383),
    "s390x": (437, 379),
}
_OPENAT2_NR, _STATX_NR = _ARCH_SYSCALLS.get(platform.machine().lower(), (None, None))
_OPENAT2_AVAILABLE: bool | None = None


def _syscall(number: int, *args: object) -> int:
    result = int(_LIBC.syscall(number, *args))
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return result


def _openat2_raw(
    dir_fd: int, path: str, flags: int, resolve: int = _OPENAT2_RESOLVE
) -> int:
    if _OPENAT2_NR is None:
        raise OSError(errno.ENOSYS, "openat2 unavailable")
    how = _OpenHow(flags=flags, mode=0, resolve=resolve)
    return _syscall(
        _OPENAT2_NR,
        dir_fd,
        ctypes.c_char_p(os.fsencode(path)),
        ctypes.byref(how),
        ctypes.sizeof(how),
    )


def _probe_openat2() -> bool:
    """Probe once; request-time failures must never trigger fallback."""
    global _OPENAT2_AVAILABLE
    if _OPENAT2_AVAILABLE is not None:
        return _OPENAT2_AVAILABLE
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            probe = _openat2_raw(fd, ".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError as exc:
            if exc.errno in {errno.ENOSYS, errno.EINVAL, errno.EPERM}:
                _OPENAT2_AVAILABLE = False
            else:
                _OPENAT2_AVAILABLE = True
        else:
            os.close(probe)
            _OPENAT2_AVAILABLE = True
    finally:
        os.close(fd)
    return bool(_OPENAT2_AVAILABLE)


def _mount_id(fd: int) -> int:
    if _STATX_NR is None:
        raise OSError(errno.ENOSYS, "statx unavailable")
    for requested in (_STATX_MNT_ID_UNIQUE, _STATX_MNT_ID):
        value = _Statx()
        try:
            _syscall(
                _STATX_NR,
                fd,
                ctypes.c_char_p(b""),
                _AT_EMPTY_PATH,
                requested,
                ctypes.byref(value),
            )
        except OSError as exc:
            if requested == _STATX_MNT_ID_UNIQUE and exc.errno == errno.EINVAL:
                continue
            raise
        if value.stx_mask & requested and value.stx_mnt_id:
            return int(value.stx_mnt_id)
        if requested == _STATX_MNT_ID_UNIQUE:
            continue
        raise OSError(errno.ENOTSUP, "mount identity unavailable")
    raise OSError(errno.ENOTSUP, "mount identity unavailable")


def _duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> None:
    raise ValueError


def _read_registry_file(path: str) -> Any:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RegistryError
        if metadata.st_uid not in {0, os.geteuid()} or metadata.st_mode & 0o022:
            raise RegistryError
        if metadata.st_size > REGISTRY_MAX_BYTES:
            raise RegistryError
        raw = bytearray()
        while len(raw) <= REGISTRY_MAX_BYTES:
            chunk = os.read(fd, min(65536, REGISTRY_MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > REGISTRY_MAX_BYTES:
            raise RegistryError
        text = bytes(raw).decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_duplicate_object,
            parse_constant=_invalid_json_constant,
        )
    finally:
        os.close(fd)


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _valid_display_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_DISPLAY_NAME_CHARS
        and not _has_control(value)
        and "/" not in value
        and "\\" not in value
    )


def _open_root(root: str) -> tuple[int, int, tuple[int, int]]:
    if not isinstance(root, str) or not root.startswith("/") or root == "/":
        raise RegistryError
    if len(root) > MAX_PATH_CHARS or "\x00" in root or "//" in root:
        raise RegistryError
    components = root.split("/")[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise RegistryError
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for component in components:
            if _has_control(component):
                raise RegistryError
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            try:
                metadata = os.fstat(child)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise RegistryError
                parent = fd
                fd = -1
                os.close(parent)
                fd = child
                child = -1
            finally:
                if child >= 0:
                    os.close(child)
        metadata = os.fstat(fd)
        mount_id = _mount_id(fd)
        return fd, mount_id, (metadata.st_dev, metadata.st_ino)
    except Exception:
        if fd >= 0:
            os.close(fd)
        raise


@contextlib.contextmanager
def load_registry(path: str | None = None) -> Iterator[WorkspaceRegistry]:
    """Load and pin the strict registry. Missing configuration is empty."""
    configured = os.environ.get(REGISTRY_ENV) if path is None else path
    if not configured:
        registry = WorkspaceRegistry([])
        with registry:
            yield registry
        return
    opened: list[Workspace] = []
    try:
        document = _read_registry_file(configured)
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "workspaces",
        }:
            raise RegistryError
        if document["schema_version"] != 1 or isinstance(
            document["schema_version"], bool
        ):
            raise RegistryError
        entries = document["workspaces"]
        if not isinstance(entries, list) or len(entries) > MAX_WORKSPACES:
            raise RegistryError
        ids: set[str] = set()
        identities: set[tuple[int, int]] = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "workspace_id",
                "display_name",
                "root",
                "enabled",
            }:
                raise RegistryError
            workspace_id = entry["workspace_id"]
            if not isinstance(workspace_id, str) or not WORKSPACE_ID_RE.fullmatch(
                workspace_id
            ):
                raise RegistryError
            if workspace_id in ids or not _valid_display_name(entry["display_name"]):
                raise RegistryError
            if not isinstance(entry["enabled"], bool):
                raise RegistryError
            ids.add(workspace_id)
            fd, mount_id, identity = _open_root(entry["root"])
            if identity in identities:
                os.close(fd)
                raise RegistryError
            identities.add(identity)
            opened.append(
                Workspace(
                    workspace_id,
                    entry["display_name"],
                    fd,
                    mount_id,
                    identity,
                    entry["enabled"],
                )
            )
        registry = WorkspaceRegistry(opened)
        opened = []
        with registry:
            yield registry
    except WorkspaceTreeError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RegistryError from exc
    finally:
        for workspace in opened:
            with contextlib.suppress(OSError):
                os.close(workspace.fd)


def validate_relative_path(value: str, *, allow_empty: bool) -> tuple[str, list[str]]:
    if not isinstance(value, str) or len(value) > MAX_PATH_CHARS:
        raise WorkspaceTreeError(400, "invalid workspace path")
    if not value:
        if allow_empty:
            return "", []
        raise WorkspaceTreeError(400, "invalid workspace path")
    if unicodedata.normalize("NFKC", value) != value:
        raise WorkspaceTreeError(400, "invalid workspace path")
    if (
        value.startswith("/")
        or value.startswith("//")
        or value.startswith("\\")
        or re.match(r"^[A-Za-z]:", value)
        or "%" in value
        or "\\" in value
        or _has_control(value)
    ):
        raise WorkspaceTreeError(400, "invalid workspace path")
    components = value.split("/")
    if len(components) > MAX_PATH_COMPONENTS:
        raise WorkspaceTreeError(400, "invalid workspace path")
    for component in components:
        if (
            component in {"", ".", ".."}
            or component.startswith(".")
            or len(component.encode("utf-8")) > 255
        ):
            raise WorkspaceTreeError(400, "invalid workspace path")
    return "/".join(components), components


def _is_previewable(relative_path: str, metadata: os.stat_result | None = None) -> bool:
    components = relative_path.split("/")
    name = components[-1].lower()
    if any(part.startswith(".") for part in components):
        return False
    if (
        name in _SENSITIVE_EXACT
        or name.endswith(_SENSITIVE_SUFFIXES)
        or _SENSITIVE_WORD.search(name)
    ):
        return False
    if os.path.splitext(name)[1] not in PREVIEW_EXTENSIONS:
        return False
    return metadata is None or (
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
    )


def _verify_opened(fd: int, workspace: Workspace, *, directory: bool) -> os.stat_result:
    metadata = os.fstat(fd)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode) or _mount_id(fd) != workspace.mount_id:
        raise OSError(errno.EACCES, "object denied")
    return metadata


def _fallback_open(
    workspace: Workspace, components: list[str], *, directory: bool
) -> int:
    current = os.dup(workspace.fd)
    try:
        if not components:
            if not directory:
                raise OSError(errno.EISDIR, "object denied")
            _verify_opened(current, workspace, directory=True)
            result = current
            current = -1
            return result
        for component in components[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            try:
                _verify_opened(child, workspace, directory=True)
            except Exception:
                os.close(child)
                raise
            parent = current
            current = -1
            try:
                os.close(parent)
            except Exception:
                os.close(child)
                raise
            current = child
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        if directory:
            flags |= os.O_DIRECTORY
        else:
            flags |= os.O_NONBLOCK
        result = os.open(components[-1], flags, dir_fd=current)
        try:
            _verify_opened(result, workspace, directory=directory)
        except Exception:
            os.close(result)
            raise
        parent = current
        current = -1
        try:
            os.close(parent)
        except Exception:
            os.close(result)
            raise
        return result
    finally:
        if current >= 0:
            os.close(current)


def _secure_open(workspace: Workspace, relative_path: str, *, directory: bool) -> int:
    _, components = validate_relative_path(relative_path, allow_empty=directory)
    if _probe_openat2():
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        if directory:
            flags |= os.O_DIRECTORY
        else:
            flags |= os.O_NONBLOCK
        fd = _openat2_raw(workspace.fd, relative_path or ".", flags)
        try:
            _verify_opened(fd, workspace, directory=directory)
        except Exception:
            os.close(fd)
            raise
        return fd
    return _fallback_open(workspace, components, directory=directory)


def list_workspaces(registry: WorkspaceRegistry) -> dict[str, Any]:
    items = [
        {"workspace_id": workspace.workspace_id, "name": workspace.display_name}
        for workspace in registry.enabled
    ]
    return {
        "ok": True,
        "workspaces": items,
        "workspace_count": len(items),
        "max_preview_bytes": MAX_PREVIEW_BYTES,
    }


def _public_node(
    workspace: Workspace,
    fd: int,
    relative_path: str,
    name: str,
    depth: int,
) -> dict[str, Any]:
    metadata = os.fstat(fd)
    if stat.S_ISREG(metadata.st_mode):
        return {
            "name": name,
            "type": "file",
            "relative_path": relative_path,
            "size": metadata.st_size,
            "previewable": _is_previewable(relative_path, metadata),
        }
    node: dict[str, Any] = {
        "name": name,
        "type": "directory",
        "relative_path": relative_path,
        "previewable": False,
        "children": [],
    }
    if depth <= 0:
        return node
    children: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(fd), key=lambda value: value.casefold())
    except OSError:
        return node
    for child_name in names:
        if len(children) >= MAX_CHILDREN:
            break
        if child_name.startswith(".") or child_name in IGNORED_NAMES:
            continue
        try:
            child_stat = os.stat(child_name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            continue
        child_relative = (
            f"{relative_path}/{child_name}" if relative_path else child_name
        )
        if stat.S_ISDIR(child_stat.st_mode):
            try:
                child_fd = _secure_open(workspace, child_relative, directory=True)
            except OSError:
                continue
            try:
                children.append(
                    _public_node(
                        workspace, child_fd, child_relative, child_name, depth - 1
                    )
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(child_stat.st_mode):
            children.append(
                {
                    "name": child_name,
                    "type": "file",
                    "relative_path": child_relative,
                    "size": child_stat.st_size,
                    "previewable": _is_previewable(child_relative, child_stat),
                }
            )
    node["children"] = children
    return node


def get_node(
    registry: WorkspaceRegistry,
    workspace_id: str,
    relative_path: str = "",
    depth: int = 1,
) -> dict[str, Any]:
    workspace = registry.resolve(workspace_id)
    normalized, _ = validate_relative_path(relative_path, allow_empty=True)
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 3:
        raise WorkspaceTreeError(400, "invalid tree depth")
    try:
        fd = _secure_open(workspace, normalized, directory=True)
    except OSError as exc:
        status_code = 404 if exc.errno in {errno.ENOENT, errno.ENOTDIR} else 403
        raise WorkspaceTreeError(status_code, "workspace object unavailable") from None
    try:
        name = normalized.rsplit("/", 1)[-1] if normalized else workspace.display_name
        tree = _public_node(workspace, fd, normalized, name, depth)
    finally:
        os.close(fd)
    return {
        "ok": True,
        "workspace_id": workspace.workspace_id,
        "workspace_name": workspace.display_name,
        "tree": tree,
    }


def get_preview(
    registry: WorkspaceRegistry,
    workspace_id: str,
    relative_path: str,
) -> dict[str, Any]:
    workspace = registry.resolve(workspace_id)
    normalized, _ = validate_relative_path(relative_path, allow_empty=False)
    if not _is_previewable(normalized):
        raise WorkspaceTreeError(403, "workspace preview denied")
    try:
        fd = _secure_open(workspace, normalized, directory=False)
    except OSError as exc:
        status_code = 404 if exc.errno in {errno.ENOENT, errno.ENOTDIR} else 403
        raise WorkspaceTreeError(status_code, "workspace object unavailable") from None
    try:
        metadata = os.fstat(fd)
        if metadata.st_nlink != 1 or metadata.st_size > MAX_PREVIEW_BYTES:
            raise WorkspaceTreeError(403, "workspace preview denied")
        raw = bytearray()
        while len(raw) <= MAX_PREVIEW_BYTES:
            chunk = os.read(fd, min(65536, MAX_PREVIEW_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > MAX_PREVIEW_BYTES or b"\x00" in raw:
            raise WorkspaceTreeError(403, "workspace preview denied")
        try:
            content = bytes(raw).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise WorkspaceTreeError(403, "workspace preview denied") from None
    finally:
        os.close(fd)
    return {
        "ok": True,
        "workspace_id": workspace.workspace_id,
        "workspace_name": workspace.display_name,
        "name": normalized.rsplit("/", 1)[-1],
        "relative_path": normalized,
        "size": len(raw),
        "content": content,
        "lines": content.count("\n") + 1,
    }
