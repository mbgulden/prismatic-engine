from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PWP_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = PWP_DIR / "schemas"

REQUIRED_ENTRYPOINTS = {
    "tokens",
    "css",
    "layout",
    "components",
    "contentSchema",
    "emdashMap",
}

REQUIRED_TOKEN_GROUPS = {
    "color",
    "font",
    "space",
    "size",
    "radius",
    "shadow",
    "motion",
    "breakpoint",
    "zIndex",
}

MODULE_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


@dataclass
class ThemeValidationResult:
    theme_path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _iter_schema_refs(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "propsSchema"} and isinstance(child, str):
                yield child
            else:
                yield from _iter_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_schema_refs(child)


def _validate_with_jsonschema(
    instance: Any, schema_path: Path, label: str, result: ThemeValidationResult
) -> None:
    if not schema_path.exists():
        result.add_error(f"Missing bundled schema: {schema_path.relative_to(PWP_DIR)}")
        return
    try:
        import jsonschema  # type: ignore
    except ImportError:
        result.add_warning(
            f"jsonschema not installed; using structural checks for {label}"
        )
        return

    try:
        jsonschema.validate(instance=instance, schema=load_json(schema_path))
    except (
        Exception
    ) as exc:  # jsonschema.ValidationError when installed; keep fallback import-free.
        message = getattr(exc, "message", str(exc))
        result.add_error(f"{label} failed schema validation: {message}")


def validate_theme_package(theme_path: str | Path) -> ThemeValidationResult:
    root = Path(theme_path).resolve()
    result = ThemeValidationResult(theme_path=root)

    if not root.exists():
        result.add_error(f"Theme path does not exist: {root}")
        return result
    if not root.is_dir():
        result.add_error(f"Theme path is not a directory: {root}")
        return result

    manifest_path = root / "theme.json"
    if not manifest_path.exists():
        result.add_error("Missing theme.json manifest")
        return result

    try:
        manifest = load_json(manifest_path)
    except json.JSONDecodeError as exc:
        result.add_error(f"theme.json is not valid JSON: {exc}")
        return result

    _validate_with_jsonschema(
        manifest, SCHEMAS_DIR / "pwp-theme.schema.json", "theme.json", result
    )

    schema_decl = manifest.get("$schema")
    if not schema_decl:
        result.add_error("theme.json missing $schema")
    elif (
        not isinstance(schema_decl, str)
        or "pwp" not in schema_decl
        or "theme" not in schema_decl
    ):
        result.add_error("theme.json $schema must reference the PWP theme schema")

    theme_id = manifest.get("id")
    if not isinstance(theme_id, str) or not theme_id.startswith("pwp.theme."):
        result.add_error("theme.json id must be a pwp.theme.* identifier")

    entrypoints = manifest.get("entrypoints")
    if not isinstance(entrypoints, dict):
        result.add_error("theme.json entrypoints must be an object")
        entrypoints = {}

    missing_entrypoints = sorted(REQUIRED_ENTRYPOINTS - set(entrypoints))
    for key in missing_entrypoints:
        result.add_error(f"theme.json missing required entrypoint: {key}")

    for key, rel in entrypoints.items():
        if not _is_safe_relative_path(rel):
            result.add_error(f"entrypoints.{key} must be a safe relative path")
            continue
        target = root / rel
        if not target.exists():
            result.add_error(f"entrypoints.{key} target missing: {rel}")

    modules = manifest.get("modules")
    if not isinstance(modules, list) or not modules:
        result.add_error("theme.json modules must be a non-empty array")
        modules = []

    seen_modules: set[str] = set()
    for module_id in modules:
        if not isinstance(module_id, str):
            result.add_error("theme.json modules must contain string module ids")
            continue
        if module_id in seen_modules:
            result.add_error(f"Duplicate module id in theme.json modules: {module_id}")
        seen_modules.add(module_id)
        if (
            not module_id
            or module_id != module_id.lower()
            or any(ch not in MODULE_ID_CHARS for ch in module_id)
        ):
            result.add_error(
                f"Invalid module id '{module_id}'; use lowercase kebab-case"
            )

    module_manifest_dir = root / "modules"
    if module_manifest_dir.exists():
        module_files = sorted(module_manifest_dir.glob("*.json"))
        module_contract_ids: set[str] = set()
        for module_file in module_files:
            try:
                module_contract = load_json(module_file)
            except json.JSONDecodeError as exc:
                result.add_error(
                    f"Module contract {module_file.relative_to(root)} is not valid JSON: {exc}"
                )
                continue
            _validate_with_jsonschema(
                module_contract,
                SCHEMAS_DIR / "pwp-module.schema.json",
                str(module_file.relative_to(root)),
                result,
            )
            module_id = module_contract.get("id")
            if not isinstance(module_id, str):
                result.add_error(
                    f"Module contract {module_file.relative_to(root)} missing string id"
                )
                continue
            module_contract_ids.add(module_id)
            if module_id not in seen_modules:
                result.add_error(
                    f"Module contract id not listed in theme.json modules: {module_id}"
                )
            component = module_contract.get("component")
            if isinstance(component, str) and _is_safe_relative_path(component):
                component_path = root / "src" / "components" / component
                if not component_path.exists():
                    result.add_error(
                        f"Module {module_id} component target missing: src/components/{component}"
                    )
            else:
                result.add_error(
                    f"Module {module_id} component must be a safe relative path"
                )

            props_schema = module_contract.get("propsSchema")
            if isinstance(props_schema, str):
                if (
                    not _is_safe_relative_path(props_schema)
                    or not (root / props_schema).exists()
                ):
                    result.add_error(
                        f"Module {module_id} propsSchema target missing or unsafe: {props_schema}"
                    )

        for module_id in sorted(seen_modules - module_contract_ids):
            result.add_error(
                f"theme.json module missing modules/<id>.json contract: {module_id}"
            )
    else:
        result.add_error("Missing modules/ contract directory")

    token_rel = entrypoints.get("tokens")
    if (
        isinstance(token_rel, str)
        and _is_safe_relative_path(token_rel)
        and (root / token_rel).exists()
    ):
        try:
            tokens = load_json(root / token_rel)
        except json.JSONDecodeError as exc:
            result.add_error(f"Token file is not valid JSON: {exc}")
            tokens = None
        if isinstance(tokens, dict):
            _validate_with_jsonschema(
                tokens, SCHEMAS_DIR / "pwp-token.schema.json", token_rel, result
            )
            missing_groups = sorted(REQUIRED_TOKEN_GROUPS - set(tokens))
            for group in missing_groups:
                result.add_error(f"Token file missing required group: {group}")
            for group, value in tokens.items():
                if group in REQUIRED_TOKEN_GROUPS and not isinstance(value, dict):
                    result.add_error(f"Token group {group} must be an object")

    emdash_rel = entrypoints.get("emdashMap")
    emdash_block_ids: set[str] = set()
    if (
        isinstance(emdash_rel, str)
        and _is_safe_relative_path(emdash_rel)
        and (root / emdash_rel).exists()
    ):
        try:
            emdash_map = load_json(root / emdash_rel)
        except json.JSONDecodeError as exc:
            result.add_error(f"EmDash map is not valid JSON: {exc}")
            emdash_map = None
        if isinstance(emdash_map, dict):
            _validate_with_jsonschema(
                emdash_map,
                SCHEMAS_DIR / "pwp-emdash-map.schema.json",
                emdash_rel,
                result,
            )
            blocks = emdash_map.get("blocks")
            if not isinstance(blocks, list):
                result.add_error("EmDash map blocks must be an array")
            else:
                for block in blocks:
                    if not isinstance(block, dict):
                        result.add_error("EmDash map blocks must contain objects")
                        continue
                    block_id = block.get("blockId")
                    if not isinstance(block_id, str) or not block_id:
                        result.add_error("EmDash map block missing blockId")
                    else:
                        emdash_block_ids.add(block_id)
                    fields = block.get("fields")
                    if not isinstance(fields, dict) or not fields:
                        result.add_error(
                            f"EmDash block {block_id or '<unknown>'} must define fields"
                        )

    for module_id in sorted(seen_modules - emdash_block_ids):
        result.add_error(f"EmDash map missing block reference for module: {module_id}")
    for block_id in sorted(emdash_block_ids - seen_modules):
        result.add_error(f"EmDash map references unknown module blockId: {block_id}")

    for ref in _iter_schema_refs(manifest):
        if (
            ref.startswith("http://")
            or ref.startswith("https://")
            or ref.startswith("#")
        ):
            continue
        if not _is_safe_relative_path(ref) or not (root / ref).exists():
            result.add_error(f"Schema reference target missing or unsafe: {ref}")

    return result


def format_result(result: ThemeValidationResult) -> str:
    rel = result.theme_path
    lines = [f"PWP theme validation: {rel}"]
    if result.ok:
        lines.append("OK")
    else:
        lines.append("FAILED")
    if result.errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result.errors)
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a PWP theme package contract."
    )
    parser.add_argument(
        "path", help="Path to a theme package directory containing theme.json"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable validation result"
    )
    args = parser.parse_args(argv)

    result = validate_theme_package(args.path)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "themePath": str(result.theme_path),
                    "errors": result.errors,
                    "warnings": result.warnings,
                },
                indent=2,
            )
        )
    else:
        print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
