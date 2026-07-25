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
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

BUNDLE_SCHEMA = "prismatic.antigravity-customizations.v1"
MANAGED_SCHEMA = "prismatic.antigravity-managed.v1"
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
ABSOLUTE_USER_PATH = re.compile(r"(?:/home/[^/\s`]+|/Users/[^/\s`]+|[A-Za-z]:\\\\Users\\\\[^\\\s`]+)")
SECRET_MATERIAL = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{16,})",
    re.IGNORECASE,
)
DANGEROUS_PATTERNS = {
    "skip-permissions": re.compile(r"dangerously-skip-permissions", re.IGNORECASE),
    "unrestricted-execution": re.compile(r"unrestricted[_ -]execution|bypass all", re.IGNORECASE),
    "raw-agy-launch": re.compile(r"(?:^|\s)agy(?:-bin)?\s+.*--print", re.IGNORECASE | re.MULTILINE),
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
        packaged = {path.as_posix(): data for path, data in _walk_resource(_resource_root())}
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise CustomizationError(f"packaged Antigravity bundle unavailable: {exc}") from exc
    files: dict[str, bytes] = {}
    for relative, data in packaged.items():
        path = Path(relative)
        if not path.parts or path.parts[0] != "agents":
            raise CustomizationError(f"unexpected packaged customization path: {relative}")
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
        if path.is_absolute() or ".." in path.parts or not relative.startswith(".agents/"):
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
                errors.append(".agents/skills.json: must declare only the managed workspace-relative skills path")
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
    if agents.is_symlink():
        raise CustomizationError(f"refusing symlink customization root: {agents}")
    return workspace


def _target(workspace: Path, relative: str) -> Path:
    target = workspace / relative
    current = workspace
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise CustomizationError(f"refusing symlink target component: {current}")
    resolved_parent = target.parent.resolve()
    if workspace != resolved_parent and workspace not in resolved_parent.parents:
        raise CustomizationError(f"target escapes workspace: {target}")
    return target


def _read_manifest(workspace: Path) -> dict[str, Any] | None:
    path = workspace / MANAGED_REL
    if not path.exists():
        return None
    if path.is_symlink():
        raise CustomizationError(f"refusing symlink managed manifest: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CustomizationError(f"invalid managed manifest: {type(exc).__name__}") from exc
    if data.get("schema") != MANAGED_SCHEMA or not isinstance(data.get("files"), dict):
        raise CustomizationError("invalid managed manifest schema")
    for relative, digest in data["files"].items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise CustomizationError("invalid managed manifest file entry")
        candidate = Path(relative)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or not candidate.parts
            or candidate.parts[0] != ".agents"
            or candidate == MANAGED_REL
            or relative not in REQUIRED_FILES
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise CustomizationError(f"unsafe managed manifest file entry: {relative!r}")
    return data


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _backup_path(workspace: Path, relative: str, stamp: str) -> Path:
    relative_inside_agents = Path(relative).relative_to(".agents")
    return workspace / BACKUP_ROOT_REL / stamp / relative_inside_agents


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
        raise CustomizationError("shipped bundle validation failed: " + "; ".join(validation["errors"]))
    old = _read_manifest(workspace)
    old_files = (old or {}).get("files", {})
    plan: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for relative, source in sorted(files.items()):
        target = _target(workspace, relative)
        expected = _sha256(source)
        if not target.exists():
            action = "create"
        elif not target.is_file():
            action = "non-regular-conflict"
            conflicts.append(relative)
        else:
            current = _sha256(target.read_bytes())
            if current == expected:
                action = "unchanged"
            elif old_files.get(relative) == current:
                action = "update-managed"
            else:
                action = "replace-with-backup" if force else "conflict"
                if not force:
                    conflicts.append(relative)
        plan.append({"path": relative, "action": action, "sha256": expected})
    if conflicts:
        return 2, {
            "ok": False,
            "operation": "install",
            "workspace": str(workspace),
            "dry_run": dry_run,
            "changed": False,
            "conflicts": conflicts,
            "plan": plan,
            "boundary": "no files written because conflict detection is whole-plan and fail-closed",
        }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not dry_run:
        for item in plan:
            if item["action"] == "unchanged":
                continue
            relative = item["path"]
            target = _target(workspace, relative)
            if item["action"] == "replace-with-backup":
                backup = _backup_path(workspace, relative, stamp)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
            _atomic_write(target, files[relative])
        manifest = {
            "schema": MANAGED_SCHEMA,
            "bundle_schema": BUNDLE_SCHEMA,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "files": validation["files"],
        }
        _atomic_write(
            workspace / MANAGED_REL,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
    changed = any(item["action"] != "unchanged" for item in plan)
    return 0, {
        "ok": True,
        "operation": "install",
        "workspace": str(workspace),
        "dry_run": dry_run,
        "changed": changed and not dry_run,
        "would_change": changed,
        "conflicts": [],
        "plan": plan,
        "managed_manifest": MANAGED_REL.as_posix(),
    }


def status_bundle(workspace_path: str | Path) -> tuple[int, dict[str, Any]]:
    workspace = _workspace(workspace_path)
    files = bundle_files()
    validation = validate_bundle(files)
    manifest = _read_manifest(workspace)
    states: list[dict[str, str]] = []
    for relative, expected_bytes in sorted(files.items()):
        target = _target(workspace, relative)
        if not target.exists():
            state = "missing"
        elif not target.is_file():
            state = "non-regular"
        elif _sha256(target.read_bytes()) == _sha256(expected_bytes):
            state = "current"
        else:
            state = "drifted"
        states.append({"path": relative, "state": state})
    manifest_current = manifest is not None and manifest.get("files") == validation.get("files")
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
    roots = [workspace / ".agents" / "skills", workspace / ".agents" / "rules"]
    for root in roots:
        if not root.exists() or root.is_symlink():
            continue
        directories = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            root.rmdir()
        except OSError:
            pass


def uninstall_bundle(
    workspace_path: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    workspace = _workspace(workspace_path)
    manifest = _read_manifest(workspace)
    if manifest is None:
        return 0, {"ok": True, "operation": "uninstall", "workspace": str(workspace), "changed": False, "plan": []}
    plan: list[dict[str, str]] = []
    conflicts: list[str] = []
    for relative, installed_hash in sorted(manifest["files"].items()):
        target = _target(workspace, relative)
        if not target.exists():
            action = "already-absent"
        elif not target.is_file():
            action = "preserve-non-regular"
            conflicts.append(relative)
        elif _sha256(target.read_bytes()) == installed_hash:
            action = "remove"
        else:
            action = "backup-and-remove" if force else "preserve-drift"
            if not force:
                conflicts.append(relative)
        plan.append({"path": relative, "action": action})
    if conflicts:
        return 2, {
            "ok": False,
            "operation": "uninstall",
            "workspace": str(workspace),
            "dry_run": dry_run,
            "changed": False,
            "conflicts": conflicts,
            "plan": plan,
            "boundary": "no files removed because drift is preserved fail-closed",
        }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not dry_run:
        for item in plan:
            target = _target(workspace, item["path"])
            if not target.exists():
                continue
            if item["action"] == "backup-and-remove":
                backup = _backup_path(workspace, item["path"], stamp)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
            target.unlink()
        (workspace / MANAGED_REL).unlink(missing_ok=True)
        _prune_empty_managed_dirs(workspace)
    return 0, {
        "ok": True,
        "operation": "uninstall",
        "workspace": str(workspace),
        "dry_run": dry_run,
        "changed": bool(plan) and not dry_run,
        "would_change": bool(plan),
        "conflicts": [],
        "plan": plan,
    }


def audit_customization_root(root_path: str | Path) -> dict[str, Any]:
    """Return a secret-safe structural audit; never reads generated/private state."""

    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        return {"ok": False, "root": str(root), "error": "not-a-directory"}
    candidates: list[dict[str, Any]] = []
    runtime_files = 0
    sensitive_paths = 0
    other_nonportable_files = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        lower_parts = {part.lower() for part in relative.parts}
        if (
            lower_parts & BLOCKED_RUNTIME_PARTS
            or path.suffix.lower() in {".db", ".wal", ".shm", ".pb"}
            or path.name in {"active_work.json", "agent_status.json", "deploy-status.json", "swarm_locks.json"}
        ):
            runtime_files += 1
            continue
        if any(term in path.name.lower() for term in ("token", "credential", "private-key", "oauth")):
            sensitive_paths += 1
            continue
        is_rule = "rules" in lower_parts and path.suffix.lower() == ".md"
        if path.name not in PORTABLE_NAMES and not is_rule:
            other_nonportable_files += 1
            continue
        hazards: list[str] = []
        metadata: dict[str, Any] = {}
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            hazards.append("unreadable-text")
            raw = b""
            text = ""
        if len(raw) > 256_000:
            hazards.append("oversized")
            text = ""
        for name, pattern in DANGEROUS_PATTERNS.items():
            if text and pattern.search(text):
                hazards.append(name)
        if text and SECRET_MATERIAL.search(text):
            hazards.append("possible-secret-material")
        if path.name == "SKILL.md" and text:
            parts = text.split("---", 2)
            if len(parts) == 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    hazards.append("invalid-frontmatter")
                else:
                    metadata = {key: frontmatter.get(key) for key in ("name", "description", "version") if key in frontmatter}
            else:
                hazards.append("missing-frontmatter")
        candidates.append(
            {
                "path": relative.as_posix(),
                "kind": "skill" if path.name == "SKILL.md" else "rule-or-config",
                "sha256": _sha256(raw),
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
        "boundary": "contents of runtime, sensitive, and other non-portable paths were not read or returned",
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
            code, result = install_bundle(args.workspace, dry_run=args.dry_run, force=args.force)
        elif args.action == "uninstall":
            code, result = uninstall_bundle(args.workspace, dry_run=args.dry_run, force=args.force)
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
            roots = args.config_root or [os.environ.get("AGY_CONFIG_DIR", "~/.gemini/config")]
            audits = [audit_customization_root(root) for root in roots]
            result = {"ok": all(item["ok"] for item in audits), "operation": "audit", "roots": audits}
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
