"""Managed, portable Antigravity customization bundle for Prismatic Engine.

The installer only manages files listed in the shipped bundle manifest. It never
copies Antigravity OAuth state, conversations, logs, caches, model bindings, or
machine-local runtime configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

BUNDLE_SCHEMA = "prismatic.antigravity-customizations.v1"
MANAGED_SCHEMA = "prismatic.antigravity-managed.v2"
MANAGED_REL = Path(".agents/prismatic-managed.json")
BACKUP_ROOT_REL = Path(".agents/.prismatic-backups")
RESOURCE_PARTS = ("resources", "antigravity", "workspace")
REQUIRED_FILES = {
    ".agents/skills.json",
    ".agents/rules/prismatic-engine.md",
    ".agents/skills/prismatic-engine-operations/SKILL.md",
    ".agents/skills/prismatic-agy-execution/SKILL.md",
    ".agents/skills/prismatic-agy-execution/templates/task.md",
    ".agents/skills/prismatic-agy-execution/templates/implementation-plan.md",
    ".agents/skills/prismatic-agy-execution/templates/result.md",
    ".agents/skills/prismatic-context-discipline/SKILL.md",
    ".agents/skills/prismatic-evidence-and-review/SKILL.md",
    ".agents/skills/prismatic-worktree-safety/SKILL.md",
}
BLOCKED_RUNTIME_PARTS = {
    "brain",
    "cache",
    "conversations",
    "implicit",
    "log",
    "logs",
    "mcp",
    "projects",
    "scratch",
    "bin",
    "updater",
    ".system_generated",
}
PORTABLE_NAMES = {
    "AGENTS.md",
    "GEMINI.md",
    "SKILL.md",
    "hooks.json",
    "mcp_config.json",
    "plugins.json",
    "skills.json",
}
ABSOLUTE_USER_PATH = re.compile(
    r"(?:/home/[^/\s`]+|/Users/[^/\s`]+|[A-Za-z]:\\\\Users\\\\[^\\\s`]+)"
)
SECRET_MATERIAL = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{16,})",
    re.IGNORECASE,
)
DANGEROUS_PATTERNS = {
    "skip-permissions": re.compile(r"dangerously-skip-permissions", re.IGNORECASE),
    "unrestricted-execution": re.compile(
        r"unrestricted[_ -]execution|bypass all", re.IGNORECASE
    ),
    "raw-agy-launch": re.compile(
        r"(?:^|\s)agy(?:-bin)?\s+.*--print", re.IGNORECASE | re.MULTILINE
    ),
    "mutable-user-path": ABSOLUTE_USER_PATH,
}


class CustomizationError(RuntimeError):
    """Raised when a managed customization operation cannot proceed safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resource_root():
    root = resources.files("prismatic")
    for part in RESOURCE_PARTS:
        root = root.joinpath(part)
    return root


def _walk_resource(node, prefix: Path = Path("")) -> Iterable[tuple[Path, bytes]]:
    for child in sorted(node.iterdir(), key=lambda item: item.name):
        relative = prefix / child.name
        if child.is_dir():
            yield from _walk_resource(child, relative)
        elif child.is_file():
            yield relative, child.read_bytes()


def bundle_files() -> dict[str, bytes]:
    """Return the shipped workspace bundle as destination-relative bytes."""

    try:
        packaged = {
            path.as_posix(): data for path, data in _walk_resource(_resource_root())
        }
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise CustomizationError(
            f"packaged Antigravity bundle unavailable: {exc}"
        ) from exc
    files: dict[str, bytes] = {}
    for relative, data in packaged.items():
        path = Path(relative)
        if not path.parts or path.parts[0] != "agents":
            raise CustomizationError(
                f"unexpected packaged customization path: {relative}"
            )
        destination = Path(".agents", *path.parts[1:]).as_posix()
        files[destination] = data
    return files


def _parse_skill(relative: str, text: str) -> list[str]:
    errors: list[str] = []
    if not text.startswith("---\n"):
        return [f"{relative}: missing YAML frontmatter"]
    parts = text.split("---", 2)
    if len(parts) != 3:
        return [f"{relative}: malformed YAML frontmatter"]
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return [f"{relative}: invalid YAML frontmatter: {type(exc).__name__}"]
    name = metadata.get("name")
    description = metadata.get("description")
    expected = Path(relative).parent.name
    if name != expected:
        errors.append(f"{relative}: name must equal directory {expected!r}")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{relative}: description must be a non-empty string")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append(f"{relative}: name must be lowercase hyphenated")
    return errors


def validate_bundle(files: dict[str, bytes] | None = None) -> dict[str, Any]:
    """Validate structure, skill metadata, paths, and secret-free content."""

    files = files or bundle_files()
    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(REQUIRED_FILES - set(files))
    unexpected = sorted(set(files) - REQUIRED_FILES)
    if missing:
        errors.append(f"missing required files: {missing}")
    if unexpected:
        errors.append(f"unexpected bundle files: {unexpected}")
    for relative, raw in sorted(files.items()):
        path = Path(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not relative.startswith(".agents/")
        ):
            errors.append(f"unsafe bundle path: {relative}")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative}: content must be UTF-8 text")
            continue
        if "\x00" in text:
            errors.append(f"{relative}: NUL byte forbidden")
        if ABSOLUTE_USER_PATH.search(text):
            errors.append(f"{relative}: machine-specific absolute user path forbidden")
        if SECRET_MATERIAL.search(text):
            errors.append(f"{relative}: possible embedded secret material")
        if relative.endswith("/SKILL.md"):
            errors.extend(_parse_skill(relative, text))
    skills_raw = files.get(".agents/skills.json")
    if skills_raw:
        try:
            skills_cfg = json.loads(skills_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f".agents/skills.json: invalid JSON: {type(exc).__name__}")
        else:
            if skills_cfg != {"entries": [{"path": ".agents/skills"}]}:
                errors.append(
                    ".agents/skills.json: must declare only the managed workspace-relative skills path"
                )
    return {
        "ok": not errors,
        "schema": BUNDLE_SCHEMA,
        "file_count": len(files),
        "files": {name: _sha256(data) for name, data in sorted(files.items())},
        "errors": errors,
        "warnings": warnings,
    }


def _workspace(path: str | Path) -> Path:
    workspace = Path(path).expanduser().resolve()
    if not workspace.is_dir():
        raise CustomizationError(f"workspace is not a directory: {workspace}")
    agents = workspace / ".agents"
    try:
        agents_stat = os.lstat(agents)
    except FileNotFoundError:
        return workspace
    if stat.S_ISLNK(agents_stat.st_mode):
        raise CustomizationError(f"refusing symlink customization root: {agents}")
    if not stat.S_ISDIR(agents_stat.st_mode):
        raise CustomizationError(f"customization root is not a directory: {agents}")
    return workspace


def _safe_relative(relative: str) -> Path:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or ".." in candidate.parts
        or candidate.parts[0] != ".agents"
    ):
        raise CustomizationError(f"unsafe managed path: {relative!r}")
    return candidate


def _inspect_managed_path(workspace: Path, relative: str) -> tuple[Path, str]:
    candidate = _safe_relative(relative)
    target = workspace / candidate
    current = workspace
    for part in candidate.parts[:-1]:
        current = current / part
        try:
            item_stat = os.lstat(current)
        except FileNotFoundError:
            return target, "missing"
        if stat.S_ISLNK(item_stat.st_mode):
            raise CustomizationError(f"refusing symlink target component: {current}")
        if not stat.S_ISDIR(item_stat.st_mode):
            raise CustomizationError(f"target ancestor is not a directory: {current}")
    try:
        target_stat = os.lstat(target)
    except FileNotFoundError:
        return target, "missing"
    if stat.S_ISLNK(target_stat.st_mode):
        raise CustomizationError(f"refusing symlink target: {target}")
    if not stat.S_ISREG(target_stat.st_mode):
        raise CustomizationError(f"managed target is not a regular file: {target}")
    return target, "regular"


def _target(workspace: Path, relative: str) -> Path:
    target, _ = _inspect_managed_path(workspace, relative)
    return target


def _read_fd_limited(fd: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_regular_nofollow(
    path: Path, *, max_bytes: int = 1_000_000
) -> tuple[bytes, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CustomizationError(
            f"unable to open regular file safely: {path}: {type(exc).__name__}"
        ) from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise CustomizationError(f"refusing non-regular file: {path}")
        if file_stat.st_size > max_bytes:
            raise CustomizationError(f"file exceeds safe read limit: {path}")
        data = _read_fd_limited(fd, max_bytes)
        if len(data) > max_bytes:
            raise CustomizationError(f"file exceeds safe read limit: {path}")
        return data, stat.S_IMODE(file_stat.st_mode)
    finally:
        os.close(fd)


def _manifest_payload(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": MANAGED_SCHEMA,
        "bundle_schema": BUNDLE_SCHEMA,
        "files": validation["files"],
    }


def _manifest_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_manifest(workspace: Path) -> dict[str, Any] | None:
    path, state = _inspect_managed_path(workspace, MANAGED_REL.as_posix())
    if state == "missing":
        return None
    try:
        raw, _ = _read_regular_nofollow(path)
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CustomizationError(
            f"invalid managed manifest: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict):
        raise CustomizationError("invalid managed manifest object")
    if set(data) != {"schema", "bundle_schema", "files"}:
        raise CustomizationError("invalid managed manifest fields")
    if (
        data.get("schema") != MANAGED_SCHEMA
        or data.get("bundle_schema") != BUNDLE_SCHEMA
        or not isinstance(data.get("files"), dict)
    ):
        raise CustomizationError("invalid managed manifest schema")
    for relative, digest in data["files"].items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise CustomizationError("invalid managed manifest file entry")
        candidate = _safe_relative(relative)
        if (
            candidate == MANAGED_REL
            or relative not in REQUIRED_FILES
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise CustomizationError(
                f"unsafe managed manifest file entry: {relative!r}"
            )
    return data


def _ensure_parent_directories(workspace: Path, target: Path) -> list[Path]:
    try:
        relative = target.relative_to(workspace)
    except ValueError as exc:
        raise CustomizationError(f"target escapes workspace: {target}") from exc
    created: list[Path] = []
    current = workspace
    for part in relative.parts[:-1]:
        current = current / part
        try:
            item_stat = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o755)
            except OSError as exc:
                raise CustomizationError(
                    f"unable to create managed directory: {current}: {type(exc).__name__}"
                ) from exc
            created.append(current)
            item_stat = os.lstat(current)
        if stat.S_ISLNK(item_stat.st_mode) or not stat.S_ISDIR(item_stat.st_mode):
            raise CustomizationError(f"unsafe managed directory: {current}")
    return created


def _stage_file(path: Path, data: bytes, mode: int = 0o644) -> Path:
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError as exc:
        raise CustomizationError(
            f"unable to stage managed file: {path}: {type(exc).__name__}"
        ) from exc
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        return tmp
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    tmp = _stage_file(path, data, mode)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _cleanup_created_directories(created: Iterable[Path]) -> None:
    for directory in sorted(
        set(created), key=lambda item: len(item.parts), reverse=True
    ):
        try:
            directory.rmdir()
        except OSError:
            pass


def _restore_original(path: Path, original: tuple[bytes, int] | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
    data, mode = original
    _atomic_write(path, data, mode)


def _backup_operation_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{uuid.uuid4().hex}"


def _open_directory_chain_nofollow(
    root: Path, parts: Iterable[str]
) -> tuple[int, list[Path]]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        current_fd = os.open(root, flags)
    except OSError as exc:
        raise CustomizationError(
            f"unable to open workspace safely: {type(exc).__name__}"
        ) from exc
    created: list[Path] = []
    current_path = root
    try:
        for part in parts:
            current_path = current_path / part
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
                created.append(current_path)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as exc:
                raise CustomizationError(
                    f"unsafe backup directory component: {current_path}: {type(exc).__name__}"
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, created
    except Exception:
        os.close(current_fd)
        _cleanup_created_directories(created)
        raise


def _create_backup_operation(workspace: Path) -> tuple[Path, int, list[Path]]:
    root_fd, created = _open_directory_chain_nofollow(workspace, BACKUP_ROOT_REL.parts)
    operation_id = _backup_operation_id()
    operation_path = workspace / BACKUP_ROOT_REL / operation_id
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    operation_created = False
    try:
        os.mkdir(operation_id, 0o700, dir_fd=root_fd)
        operation_created = True
        operation_fd = os.open(operation_id, flags, dir_fd=root_fd)
    except OSError as exc:
        if operation_created:
            try:
                os.rmdir(operation_id, dir_fd=root_fd)
            except OSError:
                pass
        os.close(root_fd)
        _cleanup_created_directories(created)
        raise CustomizationError(
            f"unable to create exclusive backup operation: {type(exc).__name__}"
        ) from exc
    os.close(root_fd)
    return operation_path, operation_fd, created


def _write_backup(operation_fd: int, relative: str, data: bytes) -> None:
    inside = Path(relative).relative_to(".agents")
    current_fd = os.dup(operation_fd)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in inside.parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as exc:
                raise CustomizationError(
                    f"unsafe backup ancestor: {relative}: {type(exc).__name__}"
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        create_flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            file_fd = os.open(inside.name, create_flags, 0o600, dir_fd=current_fd)
        except OSError as exc:
            raise CustomizationError(
                f"refusing existing or unsafe backup file: {relative}: {type(exc).__name__}"
            ) from exc
        try:
            view = memoryview(data)
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise CustomizationError("backup write made no progress")
                view = view[written:]
            os.fsync(file_fd)
            os.fchmod(file_fd, 0o600)
        finally:
            os.close(file_fd)
    finally:
        os.close(current_fd)


def _remove_backup_operation(operation: Path | None, created: Iterable[Path]) -> None:
    if operation is not None:
        try:
            operation_stat = os.lstat(operation)
            if stat.S_ISDIR(operation_stat.st_mode) and not stat.S_ISLNK(
                operation_stat.st_mode
            ):
                shutil.rmtree(operation)
        except FileNotFoundError:
            pass
    _cleanup_created_directories(created)


def _conflict_result(
    operation: str,
    workspace: Path,
    dry_run: bool,
    conflicts: list[str],
    plan: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    return 2, {
        "ok": False,
        "operation": operation,
        "workspace": str(workspace),
        "dry_run": dry_run,
        "changed": False,
        "conflicts": sorted(set(conflicts)),
        "plan": plan,
        "boundary": "no managed files mutated because preflight is whole-plan and fail-closed",
    }


def install_bundle(
    workspace_path: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    workspace = _workspace(workspace_path)
    files = bundle_files()
    validation = validate_bundle(files)
    if not validation["ok"]:
        raise CustomizationError(
            "shipped bundle validation failed: " + "; ".join(validation["errors"])
        )
    desired_manifest = _manifest_payload(validation)
    existing_manifest = _read_manifest(workspace)
    plan: list[dict[str, Any]] = []
    conflicts: list[str] = []
    originals: dict[Path, tuple[bytes, int] | None] = {}
    for relative, source in sorted(files.items()):
        try:
            target, state = _inspect_managed_path(workspace, relative)
        except CustomizationError:
            plan.append(
                {
                    "path": relative,
                    "action": "unsafe-path-conflict",
                    "sha256": _sha256(source),
                }
            )
            conflicts.append(relative)
            continue
        expected = _sha256(source)
        if state == "missing":
            action = "create"
            originals[target] = None
        else:
            current_data, current_mode = _read_regular_nofollow(target)
            originals[target] = (current_data, current_mode)
            if _sha256(current_data) == expected:
                action = "unchanged"
            elif force:
                action = "replace-with-backup"
            else:
                action = "conflict"
                conflicts.append(relative)
        plan.append({"path": relative, "action": action, "sha256": expected})
    all_files_current = bool(plan) and all(
        item["action"] == "unchanged" for item in plan
    )
    manifest_path, manifest_state = _inspect_managed_path(
        workspace, MANAGED_REL.as_posix()
    )
    if manifest_state == "missing":
        manifest_action = "create-manifest"
        originals[manifest_path] = None
    else:
        manifest_raw, manifest_mode = _read_regular_nofollow(manifest_path)
        originals[manifest_path] = (manifest_raw, manifest_mode)
        if existing_manifest == desired_manifest:
            manifest_action = "unchanged-manifest"
        elif all_files_current or force:
            manifest_action = "update-manifest"
        else:
            manifest_action = "manifest-conflict"
            conflicts.append(MANAGED_REL.as_posix())
    plan.append(
        {
            "path": MANAGED_REL.as_posix(),
            "action": manifest_action,
            "sha256": _sha256(_manifest_bytes(desired_manifest)),
        }
    )
    if conflicts:
        return _conflict_result("install", workspace, dry_run, conflicts, plan)
    changed_actions = {
        "create",
        "replace-with-backup",
        "create-manifest",
        "update-manifest",
    }
    would_change = any(item["action"] in changed_actions for item in plan)
    if dry_run:
        return 0, {
            "ok": True,
            "operation": "install",
            "workspace": str(workspace),
            "dry_run": True,
            "changed": False,
            "would_change": would_change,
            "conflicts": [],
            "plan": plan,
            "managed_manifest": MANAGED_REL.as_posix(),
        }
    created_dirs: list[Path] = []
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    backup_operation: Path | None = None
    backup_fd: int | None = None
    backup_created_dirs: list[Path] = []
    try:
        for item in plan:
            if item["action"] not in changed_actions:
                continue
            relative = item["path"]
            target = workspace / _safe_relative(relative)
            created_dirs.extend(_ensure_parent_directories(workspace, target))
            data = (
                _manifest_bytes(desired_manifest)
                if relative == MANAGED_REL.as_posix()
                else files[relative]
            )
            staged[target] = _stage_file(target, data)
        backup_items = [
            item for item in plan if item["action"] == "replace-with-backup"
        ]
        if backup_items:
            backup_operation, backup_fd, backup_created_dirs = _create_backup_operation(
                workspace
            )
            for item in backup_items:
                original = originals[workspace / _safe_relative(item["path"])]
                assert original is not None
                _write_backup(backup_fd, item["path"], original[0])
        for item in plan:
            if item["action"] not in changed_actions:
                continue
            target = workspace / _safe_relative(item["path"])
            os.replace(staged.pop(target), target)
            committed.append(target)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                _restore_original(target, originals[target])
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}:{type(rollback_exc).__name__}")
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
        if backup_fd is not None:
            os.close(backup_fd)
            backup_fd = None
        _remove_backup_operation(backup_operation, backup_created_dirs)
        _cleanup_created_directories(created_dirs)
        detail = f"; rollback failures={rollback_errors}" if rollback_errors else ""
        raise CustomizationError(
            f"install transaction failed: {type(exc).__name__}: {exc}{detail}"
        ) from exc
    finally:
        if backup_fd is not None:
            os.close(backup_fd)
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)
    return 0, {
        "ok": True,
        "operation": "install",
        "workspace": str(workspace),
        "dry_run": False,
        "changed": would_change,
        "would_change": would_change,
        "conflicts": [],
        "plan": plan,
        "managed_manifest": MANAGED_REL.as_posix(),
        "backup_operation": str(backup_operation) if backup_operation else None,
    }


def status_bundle(workspace_path: str | Path) -> tuple[int, dict[str, Any]]:
    workspace = _workspace(workspace_path)
    files = bundle_files()
    validation = validate_bundle(files)
    manifest = _read_manifest(workspace)
    states: list[dict[str, str]] = []
    for relative, expected_bytes in sorted(files.items()):
        try:
            target, state = _inspect_managed_path(workspace, relative)
        except CustomizationError:
            states.append({"path": relative, "state": "unsafe"})
            continue
        if state == "missing":
            file_state = "missing"
        else:
            current, _ = _read_regular_nofollow(target)
            file_state = (
                "current" if _sha256(current) == _sha256(expected_bytes) else "drifted"
            )
        states.append({"path": relative, "state": file_state})
    desired_manifest = _manifest_payload(validation)
    manifest_current = manifest == desired_manifest
    current = (
        validation["ok"]
        and manifest_current
        and all(item["state"] == "current" for item in states)
    )
    return (0 if current else 1), {
        "ok": current,
        "operation": "status",
        "workspace": str(workspace),
        "managed": manifest is not None,
        "managed_manifest_current": manifest_current,
        "bundle_valid": validation["ok"],
        "files": states,
    }


def _prune_empty_managed_dirs(workspace: Path) -> None:
    directories: set[Path] = set()
    agents = workspace / ".agents"
    for relative in REQUIRED_FILES:
        current = (workspace / relative).parent
        while current != agents and agents in current.parents:
            directories.add(current)
            current = current.parent
    directories.update({agents / "skills", agents / "rules"})
    for directory in sorted(
        directories, key=lambda item: len(item.parts), reverse=True
    ):
        try:
            item_stat = os.lstat(directory)
            if stat.S_ISDIR(item_stat.st_mode) and not stat.S_ISLNK(item_stat.st_mode):
                directory.rmdir()
        except (FileNotFoundError, OSError):
            pass


def uninstall_bundle(
    workspace_path: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    workspace = _workspace(workspace_path)
    validation = validate_bundle()
    if not validation["ok"]:
        raise CustomizationError(
            "shipped bundle validation failed: " + "; ".join(validation["errors"])
        )
    manifest = _read_manifest(workspace)
    if manifest is None:
        return 0, {
            "ok": True,
            "operation": "uninstall",
            "workspace": str(workspace),
            "changed": False,
            "would_change": False,
            "plan": [],
        }
    desired_manifest = _manifest_payload(validation)
    if manifest != desired_manifest:
        raise CustomizationError(
            "managed manifest does not match the trusted shipped bundle"
        )
    plan: list[dict[str, str]] = []
    conflicts: list[str] = []
    originals: dict[Path, tuple[bytes, int] | None] = {}
    for relative, expected_hash in sorted(validation["files"].items()):
        try:
            target, state = _inspect_managed_path(workspace, relative)
        except CustomizationError:
            plan.append({"path": relative, "action": "unsafe-path-conflict"})
            conflicts.append(relative)
            continue
        if state == "missing":
            action = "already-absent"
            originals[target] = None
        else:
            current_data, current_mode = _read_regular_nofollow(target)
            originals[target] = (current_data, current_mode)
            if _sha256(current_data) == expected_hash:
                action = "remove"
            elif force:
                action = "backup-and-remove"
            else:
                action = "preserve-drift"
                conflicts.append(relative)
        plan.append({"path": relative, "action": action})
    manifest_path, _ = _inspect_managed_path(workspace, MANAGED_REL.as_posix())
    originals[manifest_path] = _read_regular_nofollow(manifest_path)
    plan.append({"path": MANAGED_REL.as_posix(), "action": "remove-manifest"})
    if conflicts:
        return _conflict_result("uninstall", workspace, dry_run, conflicts, plan)
    if dry_run:
        return 0, {
            "ok": True,
            "operation": "uninstall",
            "workspace": str(workspace),
            "dry_run": True,
            "changed": False,
            "would_change": True,
            "conflicts": [],
            "plan": plan,
        }
    for path, original in originals.items():
        if original is None:
            continue
        current, _ = _read_regular_nofollow(path)
        if _sha256(current) != _sha256(original[0]):
            raise CustomizationError(f"managed file changed after preflight: {path}")
    deleted: list[Path] = []
    backup_operation: Path | None = None
    backup_fd: int | None = None
    backup_created_dirs: list[Path] = []
    try:
        backup_items = [item for item in plan if item["action"] == "backup-and-remove"]
        if backup_items:
            backup_operation, backup_fd, backup_created_dirs = _create_backup_operation(
                workspace
            )
            for item in backup_items:
                original = originals[workspace / _safe_relative(item["path"])]
                assert original is not None
                _write_backup(backup_fd, item["path"], original[0])
        for item in plan:
            if item["action"] not in {
                "remove",
                "backup-and-remove",
                "remove-manifest",
            }:
                continue
            target = workspace / _safe_relative(item["path"])
            target.unlink()
            deleted.append(target)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(deleted):
            try:
                _restore_original(target, originals[target])
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}:{type(rollback_exc).__name__}")
        if backup_fd is not None:
            os.close(backup_fd)
            backup_fd = None
        _remove_backup_operation(backup_operation, backup_created_dirs)
        detail = f"; rollback failures={rollback_errors}" if rollback_errors else ""
        raise CustomizationError(
            f"uninstall transaction failed: {type(exc).__name__}: {exc}{detail}"
        ) from exc
    finally:
        if backup_fd is not None:
            os.close(backup_fd)
    _prune_empty_managed_dirs(workspace)
    return 0, {
        "ok": True,
        "operation": "uninstall",
        "workspace": str(workspace),
        "dry_run": False,
        "changed": True,
        "would_change": True,
        "conflicts": [],
        "plan": plan,
        "backup_operation": str(backup_operation) if backup_operation else None,
    }


def _read_audit_candidate(
    root: Path, relative: Path, *, max_bytes: int = 256_000
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    dir_flags = flags | getattr(os, "O_DIRECTORY", 0)
    try:
        current_fd = os.open(root, dir_flags)
    except OSError as exc:
        raise CustomizationError(
            f"unable to open audit root safely: {type(exc).__name__}"
        ) from exc
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, dir_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(relative.name, flags, dir_fd=current_fd)
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise CustomizationError("audit candidate is not a regular file")
            if file_stat.st_size > max_bytes:
                raise CustomizationError("audit candidate exceeds safe size limit")
            data = _read_fd_limited(file_fd, max_bytes)
            if len(data) > max_bytes:
                raise CustomizationError("audit candidate exceeds safe size limit")
            return data
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise CustomizationError(
            f"unable to read audit candidate safely: {type(exc).__name__}"
        ) from exc
    finally:
        os.close(current_fd)


def audit_customization_root(root_path: str | Path) -> dict[str, Any]:
    """Return a secret-safe audit without following links or returning metadata values."""

    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        return {"ok": False, "root": str(root), "error": "not-a-directory"}
    candidates: list[dict[str, Any]] = []
    runtime_files = 0
    sensitive_paths = 0
    other_nonportable_files = 0
    symlink_paths = 0
    nonregular_paths = 0
    sensitive_terms = (
        "token",
        "credential",
        "secret",
        "private-key",
        "private_key",
        "api-key",
        "apikey",
        "oauth",
        "password",
    )
    for current_text, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_text)
        safe_dirs: list[str] = []
        for dirname in sorted(dirnames):
            directory = current / dirname
            try:
                directory_stat = os.lstat(directory)
            except OSError:
                nonregular_paths += 1
                continue
            if stat.S_ISLNK(directory_stat.st_mode):
                symlink_paths += 1
            elif stat.S_ISDIR(directory_stat.st_mode):
                safe_dirs.append(dirname)
            else:
                nonregular_paths += 1
        dirnames[:] = safe_dirs
        for filename in sorted(filenames):
            path = current / filename
            relative = path.relative_to(root)
            try:
                path_stat = os.lstat(path)
            except OSError:
                nonregular_paths += 1
                continue
            if stat.S_ISLNK(path_stat.st_mode):
                symlink_paths += 1
                continue
            if not stat.S_ISREG(path_stat.st_mode):
                nonregular_paths += 1
                continue
            lower_parts = {part.lower() for part in relative.parts}
            if (
                lower_parts & BLOCKED_RUNTIME_PARTS
                or path.suffix.lower() in {".db", ".wal", ".shm", ".pb"}
                or path.name
                in {
                    "active_work.json",
                    "agent_status.json",
                    "deploy-status.json",
                    "swarm_locks.json",
                }
            ):
                runtime_files += 1
                continue
            if any(term in part for part in lower_parts for term in sensitive_terms):
                sensitive_paths += 1
                continue
            is_rule = "rules" in lower_parts and path.suffix.lower() == ".md"
            if path.name not in PORTABLE_NAMES and not is_rule:
                other_nonportable_files += 1
                continue
            hazards: list[str] = []
            metadata: dict[str, Any] = {
                "frontmatter_fields": [],
                "name_matches_directory": None,
            }
            try:
                raw = _read_audit_candidate(root, relative)
                text = raw.decode("utf-8")
            except (CustomizationError, UnicodeDecodeError) as exc:
                message = str(exc)
                hazards.append(
                    "oversized" if "size limit" in message else "unreadable-text"
                )
                raw = b""
                text = ""
            for name, pattern in DANGEROUS_PATTERNS.items():
                if text and pattern.search(text):
                    hazards.append(name)
            contains_secret = bool(text and SECRET_MATERIAL.search(text))
            if contains_secret:
                hazards.append("possible-secret-material")
            if path.name == "SKILL.md" and text:
                parts = text.split("---", 2)
                if len(parts) == 3:
                    try:
                        frontmatter = yaml.safe_load(parts[1]) or {}
                    except yaml.YAMLError:
                        hazards.append("invalid-frontmatter")
                    else:
                        if not isinstance(frontmatter, dict):
                            hazards.append("invalid-frontmatter")
                            frontmatter = {}
                        metadata["frontmatter_fields"] = [
                            key
                            for key in ("name", "description", "version")
                            if key in frontmatter
                        ]
                        declared_name = frontmatter.get("name")
                        metadata["name_matches_directory"] = (
                            isinstance(declared_name, str)
                            and declared_name == path.parent.name
                        )
                else:
                    hazards.append("missing-frontmatter")
            candidates.append(
                {
                    "path": relative.as_posix(),
                    "kind": ("skill" if path.name == "SKILL.md" else "rule-or-config"),
                    "sha256": (None if contains_secret or not raw else _sha256(raw)),
                    "metadata": metadata,
                    "hazards": sorted(set(hazards)),
                }
            )
    return {
        "ok": True,
        "root": str(root),
        "portable_candidates": candidates,
        "candidate_count": len(candidates),
        "generated_or_runtime_file_count": runtime_files,
        "sensitive_path_count": sensitive_paths,
        "other_nonportable_file_count": other_nonportable_files,
        "symlink_path_count": symlink_paths,
        "nonregular_path_count": nonregular_paths,
        "boundary": (
            "runtime, sensitive, symlink, non-regular, and other non-portable "
            "contents were not read or returned; metadata values are never returned"
        ),
    }


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prismatic agy customizations")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("install", "uninstall"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", default=".")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--force", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--workspace", default=".")
    validate = commands.add_parser("validate")
    validate.add_argument("--workspace")
    audit = commands.add_parser("audit")
    audit.add_argument("--config-root", action="append")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.action == "install":
            code, result = install_bundle(
                args.workspace, dry_run=args.dry_run, force=args.force
            )
        elif args.action == "uninstall":
            code, result = uninstall_bundle(
                args.workspace, dry_run=args.dry_run, force=args.force
            )
        elif args.action == "status":
            code, result = status_bundle(args.workspace)
        elif args.action == "validate":
            result = validate_bundle()
            code = 0 if result["ok"] else 1
            if args.workspace:
                installed_code, installed = status_bundle(args.workspace)
                result["installed"] = installed
                code = max(code, installed_code)
        else:
            roots = args.config_root or [
                os.environ.get("AGY_CONFIG_DIR", "~/.gemini/config")
            ]
            audits = [audit_customization_root(root) for root in roots]
            result = {
                "ok": all(item["ok"] for item in audits),
                "operation": "audit",
                "roots": audits,
            }
            code = 0 if result["ok"] else 1
    except CustomizationError as exc:
        result = {"ok": False, "error": str(exc)}
        code = 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


__all__ = [
    "CustomizationError",
    "audit_customization_root",
    "bundle_files",
    "cli",
    "install_bundle",
    "status_bundle",
    "uninstall_bundle",
    "validate_bundle",
]
