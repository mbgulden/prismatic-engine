from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate_runtime_services.py"
MANIFEST_PATH = REPO_ROOT / "config" / "runtime-services.json"
SPEC = importlib.util.spec_from_file_location(
    "runtime_services_validator", VALIDATOR_PATH
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
HOME = validator.ENGINE_RELEASE_TEMPLATE.partition("/.prismatic/")[0]


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def component(
    document: dict[str, Any], component_id: str = "gateway"
) -> dict[str, Any]:
    return next(item for item in document["components"] if item["id"] == component_id)


def assert_invalid(document: Any, text: str | None = None) -> None:
    errors = validator.validate_manifest(document)
    assert errors
    if text is not None:
        assert any(text in error for error in errors), errors


def run_cli(path: Path = MANIFEST_PATH) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), str(path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_repository_manifest_passes() -> None:
    assert validator.validate_manifest(manifest()) == []
    assert validator.load_manifest(MANIFEST_PATH) == manifest()


def test_exact_component_set_and_schema_version() -> None:
    document = manifest()
    assert document["schema_version"] == validator.SCHEMA_VERSION == 1
    assert {item["id"] for item in document["components"]} == validator.COMPONENT_IDS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable_path", "" + HOME + "/work/prismatic-engine/bin/run"),
        ("source_path", "" + HOME + "/.prismatic/runtime/prismatic/main.py"),
        ("working_directory", "" + HOME + "/.hermes/profiles/george/runtime"),
        ("module_path", "" + HOME + "/work/prismatic-engine/prismatic/main.py"),
    ],
)
def test_mutable_execution_scalar_paths_rejected(field: str, value: str) -> None:
    document = manifest()
    component(document)[field] = value
    assert_invalid(document, "mutable execution path")


@pytest.mark.parametrize(
    "prefix",
    [
        "" + HOME + "/work/prismatic-engine",
        "" + HOME + "/.prismatic/runtime/engine",
        "" + HOME + "/.hermes/profiles/george/assets",
    ],
)
def test_mutable_import_pythonpath_rejected(prefix: str) -> None:
    document = manifest()
    component(document)["import_paths"] = [prefix]
    assert_invalid(document, "mutable execution path")


def test_mutable_external_state_is_accepted_only_as_state() -> None:
    document = manifest()
    component(document)["state_paths"] = ["" + HOME + "/.prismatic/cache"]
    assert validator.validate_manifest(document) == []
    component(document)["executable_path"] = "" + HOME + "/.prismatic/cache/run"
    assert_invalid(document, "immutable release")


def test_supervisor_declares_dedicated_raw_output_queue_only_as_state() -> None:
    document = manifest()
    supervisor = component(document, "supervisor")
    queue_path = (
        HOME + "/.prismatic/state/agy-result-boundary/agent_raw_output_queue.sqlite3"
    )

    assert queue_path in supervisor["state_paths"]
    for field in (
        "executable_path",
        "module_path",
        "source_path",
        "working_directory",
        "release_path_template",
        "virtualenv_path_template",
    ):
        assert queue_path not in str(supervisor[field])
    assert all(queue_path not in value for value in supervisor["import_paths"])
    assert all(queue_path not in value for value in supervisor["environment_files"])


def test_supervisor_declares_completed_work_paths_only_as_state() -> None:
    document = manifest()
    supervisor = component(document, "supervisor")
    completed_work_paths = (
        HOME + "/.prismatic/state/agy-completed-work/agy_completed_work.db",
        HOME + "/.prismatic/state/agy-completed-work/evidence",
    )

    for state_path in completed_work_paths:
        assert state_path in supervisor["state_paths"]
        for field in (
            "executable_path",
            "module_path",
            "source_path",
            "working_directory",
            "release_path_template",
            "virtualenv_path_template",
        ):
            assert state_path not in str(supervisor[field])
        assert all(state_path not in value for value in supervisor["import_paths"])
        assert all(state_path not in value for value in supervisor["environment_files"])


def test_gateway_declares_native_receipt_state_only_as_state() -> None:
    document = manifest()
    gateway = component(document, "gateway")
    receipt_paths = (
        HOME + "/.prismatic/db/provider_neutral_verification_receipts.sqlite3",
        HOME + "/.prismatic/db/provider_neutral_verification_revocations.json",
    )

    for receipt_path in receipt_paths:
        assert receipt_path in gateway["state_paths"]
        for field in (
            "executable_path",
            "module_path",
            "source_path",
            "working_directory",
            "release_path_template",
            "virtualenv_path_template",
        ):
            assert receipt_path not in str(gateway[field])
        assert all(receipt_path not in value for value in gateway["import_paths"])
        assert all(receipt_path not in value for value in gateway["environment_files"])


def test_release_template_required_for_engine_code() -> None:
    document = manifest()
    component(document)["release_path_template"] = (
        "" + HOME + "/.prismatic/releases/current"
    )
    assert_invalid(document, "Engine release template")


@pytest.mark.parametrize("value", [None, [], "text", 7, True])
def test_wrong_top_level_types_rejected(value: Any) -> None:
    assert_invalid(value, "plain object")


def test_malformed_json_and_duplicate_json_keys_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        validator.load_manifest(malformed)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(validator.DuplicateKeyError):
        validator.load_manifest(duplicate)


def test_wrong_schema_version_rejected() -> None:
    document = manifest()
    document["schema_version"] = 2
    assert_invalid(document, "expected 1")


def test_missing_extra_and_duplicate_component_ids_rejected() -> None:
    missing = manifest()
    missing["components"].pop()
    assert_invalid(missing, "missing ids")

    extra = manifest()
    addition = copy.deepcopy(extra["components"][0])
    addition["id"] = "other"
    extra["components"].append(addition)
    assert_invalid(extra, "extra ids")

    duplicate = manifest()
    duplicate["components"].append(copy.deepcopy(duplicate["components"][0]))
    assert_invalid(duplicate, "duplicate ids")


def test_unknown_and_missing_fields_rejected() -> None:
    document = manifest()
    component(document)["surprise"] = "value"
    assert_invalid(document, "unknown fields")
    document = manifest()
    del component(document)["owner"]
    assert_invalid(document, "missing fields")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", 1),
        ("owner", ""),
        ("state_paths", "not-a-list"),
        ("state_paths", []),
        ("state_paths", [""]),
        ("separately_versioned", 0),
    ],
)
def test_wrong_field_types_and_empty_values_rejected(field: str, value: Any) -> None:
    document = manifest()
    component(document)[field] = value
    assert_invalid(document)


def test_environment_file_policy() -> None:
    safe = manifest()
    assert validator.validate_manifest(safe) == []

    for value in (
        "" + HOME + "/.prismatic/env.d/gateway.env=payload",
        "" + HOME + "/.prismatic/env.d/gateway.txt",
        "/tmp/gateway.env",
        "" + HOME + "/.prismatic/env.d/../gateway.env",
    ):
        document = manifest()
        component(document)["environment_files"] = [value]
        assert_invalid(document)


@pytest.mark.parametrize(
    "value",
    [
        "contains token marker",
        "PASSWORD=payload",
        "a-secret-value",
        "private-key-material",
        "-----BEGIN " + "PRIVATE KEY-----",
        "api_key=payload",
    ],
)
def test_secret_and_private_key_markers_rejected(value: str) -> None:
    document = manifest()
    component(document)["project"] = value
    assert_invalid(document, "credential/private-key marker")


def test_environment_files_field_name_is_not_a_secret_false_positive() -> None:
    document = manifest()
    assert "environment_files" in component(document)
    assert validator.validate_manifest(document) == []


@pytest.mark.parametrize(
    "state_path",
    [
        "" + HOME + "/.prismatic/state/../release",
        "" + HOME + "/.prismatic/releases/{release_id}/state.sqlite",
        "" + HOME + "/work/prismatic-engine/state.sqlite",
        "" + HOME + "/.prismatic/runtime/state.sqlite",
        "" + HOME + "/.hermes/profiles/george/state.sqlite",
        "relative/state.sqlite",
        "/tmp/unapproved-state",
    ],
)
def test_invalid_state_paths_rejected(state_path: str) -> None:
    document = manifest()
    component(document)["state_paths"] = [state_path]
    assert_invalid(document)


@pytest.mark.parametrize(
    "state_path",
    [
        "" + HOME + "/.prismatic/state",
        "/archive/agy_sandboxes",
        "" + HOME + "/mounts/synology-agentic-context/agy_sandboxes/archive",
    ],
)
def test_approved_external_state_roots_accepted(state_path: str) -> None:
    document = manifest()
    component(document)["state_paths"] = [state_path]
    assert validator.validate_manifest(document) == []


def test_archive_roots_are_not_executable_roots() -> None:
    document = manifest()
    component(document)["source_path"] = "/archive/agy_sandboxes/source.py"
    assert_invalid(document, "immutable release")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "prismatic-engine"),
        ("project", "prismatic-engine"),
        ("deployment_mode", "immutable-release"),
        ("release_binding", "engine"),
        ("separately_versioned", False),
        ("release_path_template", validator.ENGINE_RELEASE_TEMPLATE),
    ],
)
def test_merge_daemon_separate_version_contract(field: str, value: Any) -> None:
    document = manifest()
    component(document, "merge-daemon")[field] = value
    assert_invalid(document, "merge-daemon")


def test_import_has_no_runtime_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("side effect during import")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    import_spec = importlib.util.spec_from_file_location(
        "runtime_services_import_trap", VALIDATOR_PATH
    )
    assert import_spec is not None and import_spec.loader is not None
    imported = importlib.util.module_from_spec(import_spec)
    import_spec.loader.exec_module(imported)
    assert callable(imported.validate_manifest)


def test_cli_success_is_compact() -> None:
    result = run_cli()
    assert result.returncode == 0
    assert result.stdout == "runtime-services: ok\n"
    assert result.stderr == ""


def test_cli_failure_is_compact_and_nonzero(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    result = run_cli(invalid)
    assert result.returncode != 0
    assert (
        result.stdout == "runtime-services: invalid: manifest: must be a plain object\n"
    )
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1


def test_cli_malformed_json_is_compact(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    result = run_cli(invalid)
    assert result.returncode != 0
    assert result.stdout == (
        "runtime-services: invalid: unable to load manifest (JSONDecodeError)\n"
    )
    assert result.stderr == ""


class HostileString(str):
    def __eq__(self, other: object) -> bool:
        raise AssertionError("custom comparison ran")

    def __hash__(self) -> int:
        raise AssertionError("custom hash ran")


class HostileDict(dict[str, Any]):
    def __iter__(self):  # type: ignore[no-untyped-def]
        raise AssertionError("custom iteration ran")


def test_mapping_subclass_rejected_without_custom_hooks() -> None:
    assert validator.validate_manifest(HostileDict()) == [
        "manifest: must be a plain object"
    ]


def test_string_subclass_rejected_without_custom_hooks() -> None:
    document = manifest()
    component(document)["id"] = HostileString("gateway")
    assert_invalid(document, "plain string")


class HostileKey(str):
    __hash__ = str.__hash__

    def __eq__(self, other: object) -> bool:
        raise AssertionError("custom key comparison ran")


@pytest.mark.parametrize("location", ["top", "component"])
def test_exact_dict_custom_keys_rejected_without_hooks(location: str) -> None:
    document = manifest()
    if location == "top":
        value = document.pop("schema_version")
        document[HostileKey("schema_version")] = value
    else:
        item = component(document)
        value = item.pop("owner")
        item[HostileKey("owner")] = value
    assert_invalid(document, "keys must be plain strings")


def test_manifest_binds_known_operational_source_entrypoints() -> None:
    document = manifest()
    expected = {
        "consumer": (
            "prismatic.gateway.event_handlers.dispatch_consumer_v3",
            "prismatic/gateway/event_handlers/dispatch_consumer_v3.py",
        ),
        "watchdog": (
            "" + HOME + "/.prismatic/releases/{release_id}/scripts/watchdog.sh",
            "scripts/watchdog.sh",
        ),
        "merge-daemon": ("prismatic_merge.cli", "prismatic_merge/cli.py"),
    }
    for component_id, (module_path, source_suffix) in expected.items():
        item = component(document, component_id)
        assert item["module_path"] == module_path
        assert item["source_path"].endswith(source_suffix)


@pytest.mark.parametrize("component_id", ["gateway", "consumer", "merge-daemon"])
def test_dotted_module_must_match_source_path(component_id: str) -> None:
    document = manifest()
    component(document, component_id)["source_path"] = (
        component(document, component_id)["release_path_template"] + "/wrong.py"
    )
    assert_invalid(document, "dotted module_path")


def test_absolute_entrypoint_must_equal_source_path() -> None:
    document = manifest()
    component(document, "watchdog")["module_path"] = (
        "" + HOME + "/.prismatic/releases/{release_id}/scripts/other.sh"
    )
    assert_invalid(document, "absolute entrypoint")
