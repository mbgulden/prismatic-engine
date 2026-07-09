from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugins.pwp.theme_diff import check_theme_compatibility
from plugins.pwp.theme_validator import load_json, validate_theme_package

PWP_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = PWP_DIR / "themes" / "registry.json"


@dataclass
class ThemeInstallResult:
    theme_path: Path
    target_project: Path
    tenant: str
    theme_id: str | None
    theme_version: str | None
    install_root: Path
    copied_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    token_hash: str | None = None
    module_hash: str | None = None
    content_schema_hash: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "themePath": str(self.theme_path),
            "targetProject": str(self.target_project),
            "tenant": self.tenant,
            "themeId": self.theme_id,
            "themeVersion": self.theme_version,
            "installRoot": str(self.install_root),
            "copiedFiles": self.copied_files,
            "tokenHash": self.token_hash,
            "moduleHash": self.module_hash,
            "contentSchemaHash": self.content_schema_hash,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_tree(root: Path) -> str | None:
    if not root.exists():
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _copy_file(
    src: Path, dest: Path, result: ThemeInstallResult, *, force: bool
) -> None:
    if dest.exists() and not force:
        result.errors.append(
            f"Refusing to overwrite existing file without --force: {dest}"
        )
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    result.copied_files.append(dest.relative_to(result.target_project).as_posix())


def _copy_tree(
    src: Path, dest: Path, result: ThemeInstallResult, *, force: bool
) -> None:
    if not src.exists():
        return
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = path.relative_to(src)
        _copy_file(path, dest / rel, result, force=force)


def _registry_candidates(registry_path: Path) -> list[dict[str, Any]]:
    if not registry_path.exists():
        return []
    payload = load_json(registry_path)
    themes = payload.get("themes") if isinstance(payload, dict) else None
    return [theme for theme in themes or [] if isinstance(theme, dict)]


def resolve_theme_reference(
    reference: str | Path, registry_path: str | Path | None = None
) -> Path:
    ref_text = str(reference)
    direct = Path(ref_text).expanduser()
    if direct.exists():
        return direct.resolve()

    registry = Path(registry_path).expanduser() if registry_path else DEFAULT_REGISTRY
    registry = registry.resolve()
    registry_root = registry.parent
    requested = ref_text.removeprefix("pwp.theme.")
    for entry in _registry_candidates(registry):
        theme_id = entry.get("id")
        name = entry.get("name")
        version = entry.get("version")
        matches = {
            str(theme_id or ""),
            str(theme_id or "").removeprefix("pwp.theme."),
            str(name or ""),
            f"{theme_id}@{version}" if theme_id and version else "",
            f"{str(theme_id or '').removeprefix('pwp.theme.')}@{version}"
            if theme_id and version
            else "",
        }
        if ref_text in matches or requested in matches:
            rel = entry.get("path")
            if not _is_safe_relative_path(rel):
                raise ValueError(
                    f"Registry theme path is missing or unsafe for {ref_text}"
                )
            rel_text = str(rel)
            manifest_path = (registry_root / rel_text).resolve()
            return (
                manifest_path.parent
                if manifest_path.name == "theme.json"
                else manifest_path
            )

    raise FileNotFoundError(
        f"Theme reference not found as a path or registry entry: {ref_text}"
    )


def install_theme_package(
    theme: str | Path,
    target_project: str | Path,
    *,
    tenant: str,
    registry_path: str | Path | None = None,
    engine_version: str | None = None,
    force: bool = False,
) -> ThemeInstallResult:
    target = Path(target_project).expanduser().resolve()
    theme_root = resolve_theme_reference(theme, registry_path)
    manifest_path = theme_root / "theme.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = load_json(manifest_path)
            if isinstance(loaded, dict):
                manifest = loaded
        except json.JSONDecodeError:
            pass
    install_root = target / "pwp" / "themes" / tenant
    result = ThemeInstallResult(
        theme_path=theme_root,
        target_project=target,
        tenant=tenant,
        theme_id=manifest.get("id") if isinstance(manifest.get("id"), str) else None,
        theme_version=manifest.get("version")
        if isinstance(manifest.get("version"), str)
        else None,
        install_root=install_root,
    )

    validation = validate_theme_package(theme_root)
    result.errors.extend(validation.errors)
    result.warnings.extend(validation.warnings)
    if engine_version:
        compat = check_theme_compatibility(theme_root, engine_version)
        if not compat.ok:
            result.errors.append(
                f"Theme is not compatible with PWP engine {engine_version}: "
                + "; ".join(compat.errors or [f"range {compat.range} does not match"])
            )
    if result.errors:
        return result

    target.mkdir(parents=True, exist_ok=True)
    entrypoints = manifest.get("entrypoints") if isinstance(manifest, dict) else {}
    if not isinstance(entrypoints, dict):
        result.errors.append("theme.json entrypoints must be an object")
        return result

    def entrypoint_path(key: str) -> Path:
        rel = entrypoints.get(key)
        if not _is_safe_relative_path(rel):
            raise ValueError(f"entrypoints.{key} must be a safe relative path")
        return theme_root / str(rel)

    _copy_file(manifest_path, install_root / "theme.json", result, force=force)
    _copy_file(
        entrypoint_path("tokens"),
        install_root / "tokens" / "tokens.json",
        result,
        force=force,
    )
    _copy_file(
        entrypoint_path("emdashMap"),
        install_root / "emdash" / "fields.json",
        result,
        force=force,
    )
    _copy_file(
        entrypoint_path("contentSchema"),
        target / "src" / "content.config.ts",
        result,
        force=force,
    )
    _copy_file(
        entrypoint_path("css"),
        target / "src" / "styles" / "pwp-theme.css",
        result,
        force=force,
    )
    _copy_file(
        entrypoint_path("layout"),
        target / "src" / "layouts" / "PwpBaseLayout.astro",
        result,
        force=force,
    )

    components_entrypoint = entrypoint_path("components")
    _copy_tree(
        components_entrypoint.parent,
        target / "src" / "components" / "pwp",
        result,
        force=force,
    )
    _copy_tree(theme_root / "modules", install_root / "modules", result, force=force)
    _copy_tree(
        theme_root / "schemas" / "modules",
        install_root / "schemas" / "modules",
        result,
        force=force,
    )

    if result.errors:
        return result

    token_path = install_root / "tokens" / "tokens.json"
    content_schema_path = target / "src" / "content.config.ts"
    result.token_hash = _sha256_file(token_path) if token_path.exists() else None
    result.module_hash = _sha256_tree(install_root / "modules")
    result.content_schema_hash = (
        _sha256_file(content_schema_path) if content_schema_path.exists() else None
    )
    install_manifest = {
        "tenant": tenant,
        "themeId": result.theme_id,
        "themeVersion": result.theme_version,
        "sourceThemePath": str(theme_root),
        "tokenHash": result.token_hash,
        "moduleHash": result.module_hash,
        "contentSchemaHash": result.content_schema_hash,
        "copiedFiles": sorted(result.copied_files),
    }
    manifest_dest = install_root / "install-manifest.json"
    if manifest_dest.exists() and not force:
        result.errors.append(
            f"Refusing to overwrite existing file without --force: {manifest_dest}"
        )
        return result
    manifest_dest.parent.mkdir(parents=True, exist_ok=True)
    manifest_dest.write_text(
        json.dumps(install_manifest, indent=2) + "\n", encoding="utf-8"
    )
    result.copied_files.append(manifest_dest.relative_to(target).as_posix())
    return result


def format_install_result(result: ThemeInstallResult) -> str:
    lines = [
        f"PWP theme install: {result.theme_id or '<unknown>'}@{result.theme_version or '<unknown>'}",
        f"Tenant: {result.tenant}",
        f"Target: {result.target_project}",
        "OK" if result.ok else "FAILED",
    ]
    if result.copied_files:
        lines.append("Copied files:")
        lines.extend(f"- {path}" for path in sorted(result.copied_files))
    if result.errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result.errors)
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install a validated PWP theme package into an Astro project."
    )
    parser.add_argument("theme", help="Theme package path or registry reference")
    parser.add_argument(
        "--target",
        required=True,
        help="Target Astro project root that will receive the theme files",
    )
    parser.add_argument(
        "--tenant", required=True, help="Tenant/client slug for install state"
    )
    parser.add_argument(
        "--registry", help="Optional registry.json path for theme references"
    )
    parser.add_argument(
        "--engine-version",
        help="Require theme compatibility with this PWP engine version",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing install files"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable result"
    )
    args = parser.parse_args(argv)

    try:
        result = install_theme_package(
            args.theme,
            args.target,
            tenant=args.tenant,
            registry_path=args.registry,
            engine_version=args.engine_version,
            force=args.force,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        target = Path(args.target).expanduser().resolve()
        result = ThemeInstallResult(
            theme_path=Path(args.theme).expanduser(),
            target_project=target,
            tenant=args.tenant,
            theme_id=None,
            theme_version=None,
            install_root=target / "pwp" / "themes" / args.tenant,
            errors=[str(exc)],
        )
    print(
        json.dumps(result.as_dict(), indent=2)
        if args.json
        else format_install_result(result)
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
