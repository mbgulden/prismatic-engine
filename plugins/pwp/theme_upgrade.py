from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugins.pwp.theme_diff import ThemeDiffResult, diff_theme_packages
from plugins.pwp.theme_validator import load_json


@dataclass
class UpgradeConflict:
    path: str
    reason: str
    override: Any = None
    before: Any = None
    after: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "reason": self.reason,
            "override": self.override,
            "before": self.before,
            "after": self.after,
        }


@dataclass
class ThemeUpgradePlan:
    from_path: Path
    to_path: Path
    tenant_overrides_path: Path
    preserved_overrides: dict[str, Any] = field(default_factory=dict)
    conflicts: list[UpgradeConflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    theme_diff: ThemeDiffResult | None = None

    @property
    def ok(self) -> bool:
        return not self.errors and not self.conflicts

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fromPath": str(self.from_path),
            "toPath": str(self.to_path),
            "tenantOverridesPath": str(self.tenant_overrides_path),
            "preservedOverrides": self.preserved_overrides,
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "errors": self.errors,
            "themeDiff": self.theme_diff.as_dict() if self.theme_diff else None,
        }


def _is_leaf(value: Any) -> bool:
    return not isinstance(value, dict) or "$value" in value


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if _is_leaf(value):
        return {prefix: value} if prefix else {"": value}
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}.{key}" if prefix else key
        flattened.update(_flatten(child, path))
    return flattened


def _set_path(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    if not dotted_path:
        return
    cursor = target
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def _value(value: Any) -> Any:
    if isinstance(value, dict) and "$value" in value:
        return value.get("$value")
    return value


def _type(value: Any) -> Any:
    if isinstance(value, dict) and "$type" in value:
        return value.get("$type")
    return None


def _load_entrypoint_json(
    theme_root: Path, manifest: dict[str, Any], key: str
) -> dict[str, Any]:
    entrypoints = manifest.get("entrypoints")
    if not isinstance(entrypoints, dict):
        return {}
    rel = entrypoints.get(key)
    if not isinstance(rel, str):
        return {}
    try:
        loaded = load_json(theme_root / rel)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_manifest(root: Path, errors: list[str], label: str) -> dict[str, Any]:
    try:
        loaded = load_json(root / "theme.json")
    except FileNotFoundError:
        errors.append(f"{label}: Missing theme.json manifest")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: theme.json is not valid JSON: {exc}")
        return {}
    if not isinstance(loaded, dict):
        errors.append(f"{label}: theme.json must be an object")
        return {}
    return loaded


def _partition_overrides(
    overrides: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tokens = overrides.get("tokens")
    content = overrides.get("content")
    if isinstance(tokens, dict) or isinstance(content, dict):
        return (
            tokens if isinstance(tokens, dict) else {},
            content if isinstance(content, dict) else {},
        )
    return overrides, {}


def _preserve_section(
    *,
    section_name: str,
    before_defaults: dict[str, Any],
    after_defaults: dict[str, Any],
    tenant_overrides: dict[str, Any],
    target: dict[str, Any],
    conflicts: list[UpgradeConflict],
) -> None:
    before_flat = _flatten(before_defaults)
    after_flat = _flatten(after_defaults)
    override_flat = _flatten(tenant_overrides)
    section_target: dict[str, Any] = {}

    for dotted_path, override_node in sorted(override_flat.items()):
        path = f"{section_name}.{dotted_path}" if dotted_path else section_name
        before_node = before_flat.get(dotted_path)
        after_node = after_flat.get(dotted_path)
        if dotted_path not in after_flat:
            conflicts.append(
                UpgradeConflict(
                    path=path,
                    reason="override-target-removed",
                    override=override_node,
                    before=before_node,
                    after=None,
                )
            )
            continue
        before_type = _type(before_node)
        after_type = _type(after_node)
        override_type = _type(override_node)
        if before_type and after_type and before_type != after_type:
            conflicts.append(
                UpgradeConflict(
                    path=path,
                    reason="default-type-changed",
                    override=override_node,
                    before=before_node,
                    after=after_node,
                )
            )
            continue
        if after_type and override_type and override_type != after_type:
            conflicts.append(
                UpgradeConflict(
                    path=path,
                    reason="override-type-incompatible-with-target",
                    override=override_node,
                    before=before_node,
                    after=after_node,
                )
            )
            continue
        if before_node is not None and _value(before_node) == _value(override_node):
            # The tenant file merely restates the old default. Drop it so the upgrade
            # receives the new default instead of freezing stale theme values.
            continue
        if before_node is not None and _value(before_node) != _value(after_node):
            conflicts.append(
                UpgradeConflict(
                    path=path,
                    reason="target-default-changed-under-tenant-override",
                    override=override_node,
                    before=before_node,
                    after=after_node,
                )
            )
        _set_path(section_target, dotted_path, override_node)

    if section_target:
        target[section_name] = section_target


def plan_theme_upgrade(
    from_theme: str | Path,
    to_theme: str | Path,
    tenant_overrides: str | Path,
    *,
    engine_version: str | None = None,
) -> ThemeUpgradePlan:
    from_root = Path(from_theme).resolve()
    to_root = Path(to_theme).resolve()
    overrides_path = Path(tenant_overrides).resolve()
    errors: list[str] = []
    before_manifest = _read_manifest(from_root, errors, "from")
    after_manifest = _read_manifest(to_root, errors, "to")
    try:
        loaded_overrides = load_json(overrides_path)
    except FileNotFoundError:
        errors.append(f"tenant overrides file does not exist: {overrides_path}")
        loaded_overrides = {}
    except json.JSONDecodeError as exc:
        errors.append(f"tenant overrides file is not valid JSON: {exc}")
        loaded_overrides = {}
    if loaded_overrides and not isinstance(loaded_overrides, dict):
        errors.append("tenant overrides file must contain a JSON object")
        loaded_overrides = {}

    diff = diff_theme_packages(from_root, to_root, engine_version=engine_version)
    errors.extend(diff.errors)
    plan = ThemeUpgradePlan(
        from_path=from_root,
        to_path=to_root,
        tenant_overrides_path=overrides_path,
        errors=errors,
        theme_diff=diff,
    )
    if errors:
        return plan

    token_overrides, content_overrides = _partition_overrides(loaded_overrides)
    before_tokens = _load_entrypoint_json(from_root, before_manifest, "tokens")
    after_tokens = _load_entrypoint_json(to_root, after_manifest, "tokens")
    _preserve_section(
        section_name="tokens",
        before_defaults=before_tokens,
        after_defaults=after_tokens,
        tenant_overrides=token_overrides,
        target=plan.preserved_overrides,
        conflicts=plan.conflicts,
    )

    before_content = _load_entrypoint_json(from_root, before_manifest, "contentSchema")
    after_content = _load_entrypoint_json(to_root, after_manifest, "contentSchema")
    if content_overrides:
        _preserve_section(
            section_name="content",
            before_defaults=before_content,
            after_defaults=after_content,
            tenant_overrides=content_overrides,
            target=plan.preserved_overrides,
            conflicts=plan.conflicts,
        )

    return plan


def format_upgrade_plan(plan: ThemeUpgradePlan) -> str:
    lines = [f"PWP theme upgrade: {plan.from_path} -> {plan.to_path}"]
    lines.append(f"Tenant overrides: {plan.tenant_overrides_path}")
    lines.append("FAILED" if plan.errors else ("CONFLICTS" if plan.conflicts else "OK"))
    if plan.errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in plan.errors)
    if plan.conflicts:
        lines.append("Conflicts:")
        lines.extend(
            f"- {conflict.path}: {conflict.reason}" for conflict in plan.conflicts
        )
    preserved = _flatten(plan.preserved_overrides)
    if preserved:
        lines.append("Preserved overrides:")
        lines.extend(f"- {path}" for path in sorted(preserved))
    else:
        lines.append("No tenant overrides require preservation.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan a PWP theme upgrade while preserving tenant overrides."
    )
    parser.add_argument(
        "--from",
        dest="from_theme",
        required=True,
        help="Installed/source theme package path",
    )
    parser.add_argument(
        "--to", dest="to_theme", required=True, help="Target theme package path"
    )
    parser.add_argument(
        "--tenant-overrides", required=True, help="Tenant token/content overrides JSON"
    )
    parser.add_argument(
        "--engine-version",
        help="Require target theme compatibility with this PWP engine version",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable upgrade plan"
    )
    args = parser.parse_args(argv)

    plan = plan_theme_upgrade(
        args.from_theme,
        args.to_theme,
        args.tenant_overrides,
        engine_version=args.engine_version,
    )
    print(
        json.dumps(plan.as_dict(), indent=2) if args.json else format_upgrade_plan(plan)
    )
    return 0 if not plan.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
