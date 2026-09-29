#!/usr/bin/env python3
"""Fail-closed validation for the source-owned runtime services manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
COMPONENT_IDS = frozenset(
    {
        "gateway",
        "consumer",
        "curator",
        "supervisor",
        "watchdog",
        "webhook-drain",
        "merge-daemon",
    }
)
TOP_LEVEL_FIELDS = frozenset({"schema_version", "components"})
COMPONENT_FIELDS = frozenset(
    {
        "id",
        "owner",
        "project",
        "deployment_mode",
        "release_binding",
        "separately_versioned",
        "release_path_template",
        "virtualenv_path_template",
        "executable_path",
        "module_path",
        "source_path",
        "working_directory",
        "import_paths",
        "state_paths",
        "environment_files",
    }
)
STRING_FIELDS = frozenset(
    {
        "id",
        "owner",
        "project",
        "deployment_mode",
        "release_binding",
        "release_path_template",
        "virtualenv_path_template",
        "executable_path",
        "module_path",
        "source_path",
        "working_directory",
    }
)
LIST_FIELDS = frozenset({"import_paths", "state_paths", "environment_files"})
EXECUTION_PATH_FIELDS = frozenset(
    {
        "release_path_template",
        "virtualenv_path_template",
        "executable_path",
        "source_path",
        "working_directory",
    }
)
# Canonical templates in {PRISMATIC_HOME} placeholder form: one shipped manifest
# validates identically on every machine. Resolved against the effective home
# at validation time (never at import — import must stay side-effect free).
MUTABLE_EXECUTION_PREFIXES = (
    "{PRISMATIC_HOME}/work/",
    "{PRISMATIC_HOME}/.prismatic/runtime/",
    "{PRISMATIC_HOME}/.hermes/profiles/",
)
ENGINE_RELEASE_TEMPLATE = "{PRISMATIC_HOME}/.prismatic/releases/{release_id}"
SEPARATE_RELEASE_PREFIX = (
    "{PRISMATIC_HOME}/.prismatic/components/merge-daemon/releases/"
)
STATE_ROOTS = (
    "{PRISMATIC_HOME}/.prismatic/",
    "/archive/agy_sandboxes",
    "{PRISMATIC_HOME}/mounts/synology-agentic-context/agy_sandboxes",
)
HOME_PLACEHOLDER = "{PRISMATIC_HOME}"
SECRET_MARKER = re.compile(
    r"(?:\btoken\b|\bpassword\b|\bsecret\b|private[-_ ]key|begin private key)",
    re.IGNORECASE,
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:token|password|secret|api[-_]?key|private[-_]?key)\s*=",
    re.IGNORECASE,
)
MODULE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
DEFAULT_MANIFEST = Path(__file__).parent.parent / "config" / "runtime-services.json"


class DuplicateKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def load_manifest(path: Path) -> Any:
    """Load JSON while rejecting duplicate object keys."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_reject_duplicate_keys)


def _is_nonempty_string(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _path_error(value: str, *, require_absolute: bool = True) -> str | None:
    path = PurePosixPath(value)
    if require_absolute and not path.is_absolute():
        return "must be absolute"
    if ".." in path.parts:
        return "must not contain traversal"
    return None


def _has_prefix(path: str, prefix: str) -> bool:
    root = prefix.rstrip("/")
    return path == root or path.startswith(root + "/")


def _secret_error(value: str) -> bool:
    return bool(SECRET_MARKER.search(value) or CREDENTIAL_ASSIGNMENT.search(value))


def _plain_key_set(
    value: dict[Any, Any], label: str
) -> tuple[frozenset[str] | None, str | None]:
    """Return exact built-in string keys without invoking custom key hooks."""
    keys = list(dict.keys(value))
    if any(type(key) is not str for key in keys):
        return None, f"{label}: keys must be plain strings"
    return frozenset(keys), None


def _effective_home() -> str:
    """Effective PRISMATIC_HOME: explicit env first, then the invoking user's home.

    Called at validation time, never at import (import must stay side-effect
    free — see test_import_has_no_runtime_side_effects).
    """
    return os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")


def _expand_home(value: str, home: str) -> str:
    return value.replace(HOME_PLACEHOLDER, home)


def _expand_component_paths(component: dict[str, Any], home: str) -> dict[str, Any]:
    """Expand {PRISMATIC_HOME} in every string path the manifest declares."""
    expanded: dict[str, Any] = {}
    for key, value in component.items():
        if type(value) is str:
            expanded[key] = _expand_home(value, home)
        elif type(value) is list:
            expanded[key] = [
                _expand_home(item, home) if type(item) is str else item
                for item in value
            ]
        else:
            expanded[key] = value
    return expanded


def _canonical_paths(home: str) -> dict[str, Any]:
    """Canonical constants with {PRISMATIC_HOME} resolved for this machine."""
    return {
        "engine_release_template": _expand_home(ENGINE_RELEASE_TEMPLATE, home),
        "separate_release_prefix": _expand_home(SEPARATE_RELEASE_PREFIX, home),
        "mutable_execution_prefixes": tuple(
            _expand_home(prefix, home) for prefix in MUTABLE_EXECUTION_PREFIXES
        ),
        "state_roots": tuple(_expand_home(root, home) for root in STATE_ROOTS),
        "env_d_root": f"{home}/.prismatic/env.d/",
        "engine_releases_root": f"{home}/.prismatic/releases/",
    }


def _validate_component(component: dict[str, Any], index: int, home: str) -> list[str]:
    errors: list[str] = []
    label = f"components[{index}]"
    keys, key_error = _plain_key_set(component, label)
    if key_error:
        return [key_error]
    assert keys is not None
    missing = sorted(COMPONENT_FIELDS - keys)
    unknown = sorted(keys - COMPONENT_FIELDS)
    if missing:
        errors.append(f"{label}: missing fields: {','.join(missing)}")
    if unknown:
        errors.append(f"{label}: unknown fields: {','.join(unknown)}")
    if missing or unknown:
        return errors

    for field in sorted(STRING_FIELDS):
        if not _is_nonempty_string(component[field]):
            errors.append(f"{label}.{field}: must be a non-empty plain string")
    if type(component["separately_versioned"]) is not bool:
        errors.append(f"{label}.separately_versioned: must be a boolean")
    for field in sorted(LIST_FIELDS):
        values = component[field]
        if type(values) is not list or not values:
            errors.append(f"{label}.{field}: must be a non-empty plain list")
            continue
        for item_index, value in enumerate(values):
            if not _is_nonempty_string(value):
                errors.append(
                    f"{label}.{field}[{item_index}]: must be a non-empty plain string"
                )
    if errors:
        return errors

    # From here on, validate the declared paths with {PRISMATIC_HOME} resolved
    # against this machine's effective home, so one shipped manifest validates
    # identically on every machine (and agrees with engine.doctor's probe).
    paths = _canonical_paths(home)
    component = _expand_component_paths(component, home)
    mutable_prefixes = paths["mutable_execution_prefixes"]
    engine_release_template = paths["engine_release_template"]
    separate_release_prefix = paths["separate_release_prefix"]
    state_roots = paths["state_roots"]
    env_d_root = paths["env_d_root"]
    engine_releases_root = paths["engine_releases_root"]

    all_values = [component[field] for field in STRING_FIELDS]
    for field in LIST_FIELDS:
        all_values.extend(component[field])
    if any(_secret_error(value) for value in all_values):
        errors.append(f"{label}: contains a forbidden credential/private-key marker")

    for field in sorted(EXECUTION_PATH_FIELDS):
        value = component[field]
        problem = _path_error(value)
        if problem:
            errors.append(f"{label}.{field}: {problem}")
        if any(_has_prefix(value, prefix) for prefix in mutable_prefixes):
            errors.append(f"{label}.{field}: mutable execution path is forbidden")
    module_path = component["module_path"]
    if "/" in module_path:
        problem = _path_error(module_path)
        if problem:
            errors.append(f"{label}.module_path: {problem}")
        if any(_has_prefix(module_path, prefix) for prefix in mutable_prefixes):
            errors.append(f"{label}.module_path: mutable execution path is forbidden")
    elif MODULE_NAME.fullmatch(module_path) is None:
        errors.append(f"{label}.module_path: must be a dotted module or absolute path")

    for item_index, import_path in enumerate(component["import_paths"]):
        problem = _path_error(import_path)
        if problem:
            errors.append(f"{label}.import_paths[{item_index}]: {problem}")
        if any(_has_prefix(import_path, prefix) for prefix in mutable_prefixes):
            errors.append(
                f"{label}.import_paths[{item_index}]: mutable execution path is forbidden"
            )

    for item_index, environment_file in enumerate(component["environment_files"]):
        problem = _path_error(environment_file)
        if problem:
            errors.append(f"{label}.environment_files[{item_index}]: {problem}")
        if not environment_file.startswith(env_d_root):
            errors.append(
                f"{label}.environment_files[{item_index}]: must be under the env.d root"
            )
        if not environment_file.endswith(".env"):
            errors.append(f"{label}.environment_files[{item_index}]: must end in .env")
        if "=" in environment_file:
            errors.append(
                f"{label}.environment_files[{item_index}]: inline assignments are forbidden"
            )

    for item_index, state_path in enumerate(component["state_paths"]):
        problem = _path_error(state_path)
        if problem:
            errors.append(f"{label}.state_paths[{item_index}]: {problem}")
        if not any(_has_prefix(state_path, root) for root in state_roots):
            errors.append(f"{label}.state_paths[{item_index}]: root is not approved")
        forbidden_state_roots = mutable_prefixes + (
            engine_releases_root,
            separate_release_prefix,
        )
        if any(_has_prefix(state_path, root) for root in forbidden_state_roots):
            errors.append(
                f"{label}.state_paths[{item_index}]: state must be external to code trees"
            )

    component_id = component["id"]
    release_template = component["release_path_template"]
    if component_id == "merge-daemon":
        if component["owner"] == "prismatic-engine":
            errors.append(f"{label}: merge-daemon owner must be separate")
        if component["project"] == "prismatic-engine":
            errors.append(f"{label}: merge-daemon project must be separate")
        if component["deployment_mode"] != "separate-immutable-release":
            errors.append(f"{label}: merge-daemon deployment must be separate")
        if component["release_binding"] != "separate":
            errors.append(f"{label}: merge-daemon release binding must be separate")
        if component["separately_versioned"] is not True:
            errors.append(f"{label}: merge-daemon must be separately versioned")
        if release_template == engine_release_template:
            errors.append(f"{label}: merge-daemon must not bind to an Engine release")
        if not release_template.startswith(separate_release_prefix):
            errors.append(f"{label}: merge-daemon release template is not approved")
    else:
        if component["owner"] != "prismatic-engine":
            errors.append(f"{label}: Engine component owner is invalid")
        if component["project"] != "prismatic-engine":
            errors.append(f"{label}: Engine component project is invalid")
        if component["deployment_mode"] != "immutable-release":
            errors.append(f"{label}: Engine deployment must use immutable-release")
        if component["release_binding"] != "engine":
            errors.append(f"{label}: Engine release binding is invalid")
        if component["separately_versioned"] is not False:
            errors.append(f"{label}: Engine component cannot be separately versioned")
        if release_template != engine_release_template:
            errors.append(f"{label}: Engine release template is invalid")

    if "{release_id}" not in release_template:
        errors.append(f"{label}.release_path_template: release_id placeholder required")
    release_prefix = release_template.rstrip("/") + "/"
    module_path = component["module_path"]
    source_path = component["source_path"]
    if "/" in module_path:
        if module_path != source_path:
            errors.append(
                f"{label}.module_path: absolute entrypoint must equal source_path"
            )
    else:
        expected_source = release_prefix + module_path.replace(".", "/") + ".py"
        if source_path != expected_source:
            errors.append(f"{label}.source_path: must match its dotted module_path")
    for field in (
        "virtualenv_path_template",
        "executable_path",
        "source_path",
        "working_directory",
    ):
        value = component[field]
        if value != release_template and not value.startswith(release_prefix):
            errors.append(
                f"{label}.{field}: must resolve through its immutable release"
            )
    for item_index, import_path in enumerate(component["import_paths"]):
        if import_path != release_template and not import_path.startswith(
            release_prefix
        ):
            errors.append(
                f"{label}.import_paths[{item_index}]: must resolve through its immutable release"
            )
    return errors


def validate_manifest(document: Any) -> list[str]:
    """Return deterministic validation errors for an already-loaded document."""
    if type(document) is not dict:
        return ["manifest: must be a plain object"]
    keys, key_error = _plain_key_set(document, "manifest")
    if key_error:
        return [key_error]
    assert keys is not None
    missing = sorted(TOP_LEVEL_FIELDS - keys)
    unknown = sorted(keys - TOP_LEVEL_FIELDS)
    errors: list[str] = []
    if missing:
        errors.append(f"manifest: missing fields: {','.join(missing)}")
    if unknown:
        errors.append(f"manifest: unknown fields: {','.join(unknown)}")
    if missing or unknown:
        return errors
    if type(document["schema_version"]) is not int:
        errors.append("schema_version: must be an integer")
    elif document["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version: expected {SCHEMA_VERSION}")
    components = document["components"]
    if type(components) is not list or not components:
        errors.append("components: must be a non-empty plain list")
        return errors
    home = _effective_home()
    ids: list[str] = []
    for index, component in enumerate(components):
        if type(component) is not dict:
            errors.append(f"components[{index}]: must be a plain object")
            continue
        component_errors = _validate_component(component, index, home)
        errors.extend(component_errors)
        if component_errors:
            continue
        component_id = component["id"]
        if type(component_id) is str:
            ids.append(component_id)
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    if duplicate_ids:
        errors.append(f"components: duplicate ids: {','.join(duplicate_ids)}")
    actual_ids = set(ids)
    missing_ids = sorted(COMPONENT_IDS - actual_ids)
    extra_ids = sorted(actual_ids - COMPONENT_IDS)
    if missing_ids:
        errors.append(f"components: missing ids: {','.join(missing_ids)}")
    if extra_ids:
        errors.append(f"components: extra ids: {','.join(extra_ids)}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        document = load_manifest(args.manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
        print(
            f"runtime-services: invalid: unable to load manifest ({type(exc).__name__})"
        )
        return 1
    errors = validate_manifest(document)
    if errors:
        print(f"runtime-services: invalid: {errors[0]}")
        return 1
    print("runtime-services: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
