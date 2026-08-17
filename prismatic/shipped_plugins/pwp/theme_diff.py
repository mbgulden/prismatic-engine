from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .theme_validator import load_json, validate_theme_package

_VERSION_RE = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?\s*$")
_RANGE_RE = re.compile(r"^(>=|<=|>|<|==|=|\^|~)?\s*(\d+(?:\.\d+){0,2}(?:[-+][A-Za-z0-9.-]+)?)$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _VERSION_RE.match(value)
        if not match:
            raise ValueError(f"Unsupported semantic version: {value}")
        return cls(*(int(part or 0) for part in match.groups()))

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


@dataclass
class ThemeChange:
    kind: str
    path: str
    before: Any = None
    after: Any = None
    breaking: bool = False
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "before": self.before,
            "after": self.after,
            "breaking": self.breaking,
            "message": self.message,
        }


@dataclass
class ThemeCompatibilityResult:
    theme_path: Path
    engine_version: str
    range: str
    compatible: bool
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.compatible and not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "themePath": str(self.theme_path),
            "engineVersion": self.engine_version,
            "range": self.range,
            "compatible": self.compatible,
            "errors": self.errors,
        }


@dataclass
class ThemeDiffResult:
    from_path: Path
    to_path: Path
    from_id: str | None
    to_id: str | None
    from_version: str | None
    to_version: str | None
    changes: list[ThemeChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def breaking_changes(self) -> list[ThemeChange]:
        return [change for change in self.changes if change.breaking]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.breaking_changes

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fromPath": str(self.from_path),
            "toPath": str(self.to_path),
            "fromId": self.from_id,
            "toId": self.to_id,
            "fromVersion": self.from_version,
            "toVersion": self.to_version,
            "errors": self.errors,
            "changes": [change.as_dict() for change in self.changes],
            "breakingChanges": [change.as_dict() for change in self.breaking_changes],
        }


def _split_constraints(range_text: str) -> list[str]:
    return [part for part in re.split(r"[,\s]+", range_text.strip()) if part]


def _satisfies_constraint(version: Version, constraint: str) -> bool:
    match = _RANGE_RE.match(constraint)
    if not match:
        raise ValueError(f"Unsupported engine compatibility constraint: {constraint}")
    operator = match.group(1) or "=="
    target = Version.parse(match.group(2))
    if operator in {"=", "=="}:
        return version == target
    if operator == ">=":
        return version >= target
    if operator == ">":
        return version > target
    if operator == "<=":
        return version <= target
    if operator == "<":
        return version < target
    if operator == "^":
        upper = Version(target.major + 1, 0, 0) if target.major else Version(0, target.minor + 1, 0)
        return target <= version < upper
    if operator == "~":
        upper = Version(target.major, target.minor + 1, 0)
        return target <= version < upper
    raise ValueError(f"Unsupported engine compatibility operator: {operator}")


def engine_version_satisfies(range_text: str, engine_version: str) -> bool:
    version = Version.parse(engine_version)
    constraints = _split_constraints(range_text)
    if not constraints:
        raise ValueError("Empty engine compatibility range")
    return all(_satisfies_constraint(version, constraint) for constraint in constraints)


def check_theme_compatibility(theme_path: str | Path, engine_version: str) -> ThemeCompatibilityResult:
    root = Path(theme_path).resolve()
    validation = validate_theme_package(root)
    errors = list(validation.errors)
    manifest: dict[str, Any] = {}
    range_text = ""
    manifest_path = root / "theme.json"
    if manifest_path.exists():
        try:
            loaded = load_json(manifest_path)
            if isinstance(loaded, dict):
                manifest = loaded
        except json.JSONDecodeError as exc:
            errors.append(f"theme.json is not valid JSON: {exc}")
    value = manifest.get("engineCompatibility")
    if isinstance(value, str):
        range_text = value
    else:
        errors.append("theme.json engineCompatibility must be a string")
    compatible = False
    if not errors:
        try:
            compatible = engine_version_satisfies(range_text, engine_version)
        except ValueError as exc:
            errors.append(str(exc))
    return ThemeCompatibilityResult(
        theme_path=root,
        engine_version=engine_version,
        range=range_text,
        compatible=compatible,
        errors=errors,
    )


def _read_manifest(root: Path, errors: list[str], label: str) -> dict[str, Any]:
    validation = validate_theme_package(root)
    errors.extend(f"{label}: {error}" for error in validation.errors)
    manifest_path = root / "theme.json"
    try:
        manifest = load_json(manifest_path)
    except FileNotFoundError:
        errors.append(f"{label}: Missing theme.json manifest")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: theme.json is not valid JSON: {exc}")
        return {}
    if not isinstance(manifest, dict):
        errors.append(f"{label}: theme.json must be an object")
        return {}
    return manifest


def _safe_read_json(root: Path, rel: Any) -> dict[str, Any]:
    if not isinstance(rel, str):
        return {}
    path = root / rel
    try:
        value = load_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _add_change(
    changes: list[ThemeChange],
    kind: str,
    path: str,
    before: Any,
    after: Any,
    *,
    breaking: bool,
    message: str,
) -> None:
    if before != after:
        changes.append(
            ThemeChange(
                kind=kind,
                path=path,
                before=before,
                after=after,
                breaking=breaking,
                message=message,
            )
        )


def diff_theme_packages(
    from_theme: str | Path,
    to_theme: str | Path,
    *,
    engine_version: str | None = None,
) -> ThemeDiffResult:
    from_root = Path(from_theme).resolve()
    to_root = Path(to_theme).resolve()
    errors: list[str] = []
    before = _read_manifest(from_root, errors, "from")
    after = _read_manifest(to_root, errors, "to")
    result = ThemeDiffResult(
        from_path=from_root,
        to_path=to_root,
        from_id=before.get("id") if isinstance(before.get("id"), str) else None,
        to_id=after.get("id") if isinstance(after.get("id"), str) else None,
        from_version=before.get("version") if isinstance(before.get("version"), str) else None,
        to_version=after.get("version") if isinstance(after.get("version"), str) else None,
        errors=errors,
    )
    if errors:
        return result

    _add_change(
        result.changes,
        "manifest",
        "id",
        before.get("id"),
        after.get("id"),
        breaking=True,
        message=("Theme package id changed; install/upgrade target is no longer the same theme family."),
    )
    _add_change(
        result.changes,
        "manifest",
        "version",
        before.get("version"),
        after.get("version"),
        breaking=False,
        message="Theme package version changed.",
    )
    _add_change(
        result.changes,
        "manifest",
        "engineCompatibility",
        before.get("engineCompatibility"),
        after.get("engineCompatibility"),
        breaking=False,
        message="Engine compatibility range changed.",
    )

    before_entrypoints_value = before.get("entrypoints")
    after_entrypoints_value = after.get("entrypoints")
    before_entrypoints: dict[str, Any] = before_entrypoints_value if isinstance(before_entrypoints_value, dict) else {}
    after_entrypoints: dict[str, Any] = after_entrypoints_value if isinstance(after_entrypoints_value, dict) else {}
    for key in sorted(set(before_entrypoints) | set(after_entrypoints)):
        before_value = before_entrypoints.get(key)
        after_value = after_entrypoints.get(key)
        if before_value != after_value:
            result.changes.append(
                ThemeChange(
                    kind="entrypoint",
                    path=f"entrypoints.{key}",
                    before=before_value,
                    after=after_value,
                    breaking=key not in after_entrypoints,
                    message=(
                        "Required entrypoint removed; existing installs cannot resolve package assets."
                        if key not in after_entrypoints
                        else "Theme entrypoint path changed."
                    ),
                )
            )

    before_modules_value = before.get("modules")
    after_modules_value = after.get("modules")
    before_modules = set(before_modules_value if isinstance(before_modules_value, list) else [])
    after_modules = set(after_modules_value if isinstance(after_modules_value, list) else [])
    for module_id in sorted(before_modules - after_modules):
        result.changes.append(
            ThemeChange(
                kind="module",
                path=f"modules.{module_id}",
                before=module_id,
                after=None,
                breaking=True,
                message="Module removed; existing pages using this block require migration.",
            )
        )
    for module_id in sorted(after_modules - before_modules):
        result.changes.append(
            ThemeChange(
                kind="module",
                path=f"modules.{module_id}",
                before=None,
                after=module_id,
                breaking=False,
                message="Module added.",
            )
        )

    before_tokens = _safe_read_json(from_root, before_entrypoints.get("tokens"))
    after_tokens = _safe_read_json(to_root, after_entrypoints.get("tokens"))
    for group in sorted(set(before_tokens) | set(after_tokens)):
        before_group = before_tokens.get(group)
        after_group = after_tokens.get(group)
        if before_group != after_group:
            removed = group not in after_tokens
            result.changes.append(
                ThemeChange(
                    kind="token",
                    path=f"tokens.{group}",
                    before=before_group,
                    after=after_group,
                    breaking=removed,
                    message="Token group removed." if removed else "Token group changed.",
                )
            )

    for module_id in sorted(before_modules & after_modules):
        before_contract = _safe_read_json(from_root, f"modules/{module_id}.json")
        after_contract = _safe_read_json(to_root, f"modules/{module_id}.json")
        for field_name in ("component", "propsSchema"):
            _add_change(
                result.changes,
                "moduleContract",
                f"modules.{module_id}.{field_name}",
                before_contract.get(field_name),
                after_contract.get(field_name),
                breaking=field_name == "propsSchema",
                message=(
                    "Module props schema changed; content records may need migration." if field_name == "propsSchema" else "Module component path changed."
                ),
            )

    if engine_version:
        compat = check_theme_compatibility(to_root, engine_version)
        if not compat.ok:
            result.changes.append(
                ThemeChange(
                    kind="compatibility",
                    path="engineCompatibility",
                    before=before.get("engineCompatibility"),
                    after=after.get("engineCompatibility"),
                    breaking=True,
                    message=(
                        f"Target theme is not compatible with PWP engine {engine_version}: "
                        + "; ".join(compat.errors or [f"range {compat.range} does not match"])
                    ),
                )
            )

    return result


def format_compatibility(result: ThemeCompatibilityResult) -> str:
    lines = [f"PWP theme compatibility: {result.theme_path}"]
    lines.append(f"Engine: {result.engine_version}")
    lines.append(f"Range: {result.range or '<missing>'}")
    lines.append("OK" if result.ok else "FAILED")
    lines.extend(f"- {error}" for error in result.errors)
    if not result.compatible and not result.errors:
        lines.append("- Engine version is outside theme compatibility range")
    return "\n".join(lines)


def format_diff(result: ThemeDiffResult) -> str:
    lines = [f"PWP theme diff: {result.from_path} -> {result.to_path}"]
    lines.append(f"Theme: {result.from_id}@{result.from_version} -> {result.to_id}@{result.to_version}")
    lines.append("FAILED" if result.errors else ("BREAKING" if result.breaking_changes else "OK"))
    if result.errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result.errors)
    if result.changes:
        lines.append("Changes:")
        for change in result.changes:
            marker = "BREAKING" if change.breaking else "safe"
            lines.append(f"- [{marker}] {change.path}: {change.message}")
    else:
        lines.append("No changes detected.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff PWP theme packages and resolve engine compatibility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diff_parser = subparsers.add_parser("diff", help="Compare two theme package directories")
    diff_parser.add_argument("--from", dest="from_theme", required=True, help="Source theme package path")
    diff_parser.add_argument("--to", dest="to_theme", required=True, help="Target theme package path")
    diff_parser.add_argument(
        "--engine-version",
        help="Require target theme compatibility with this PWP engine version",
    )
    diff_parser.add_argument("--json", action="store_true", help="Emit machine-readable diff result")

    compat_parser = subparsers.add_parser("check-compat", help="Check a theme against a PWP engine version")
    compat_parser.add_argument("path", help="Theme package path")
    compat_parser.add_argument("--engine-version", required=True, help="PWP engine semantic version")
    compat_parser.add_argument("--json", action="store_true", help="Emit machine-readable compatibility result")

    args = parser.parse_args(argv)
    if args.command == "diff":
        diff = diff_theme_packages(args.from_theme, args.to_theme, engine_version=args.engine_version)
        print(json.dumps(diff.as_dict(), indent=2) if args.json else format_diff(diff))
        return 0 if not diff.errors else 1
    compat = check_theme_compatibility(args.path, args.engine_version)
    print(json.dumps(compat.as_dict(), indent=2) if args.json else format_compatibility(compat))
    return 0 if compat.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
