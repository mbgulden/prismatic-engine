from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "distribution_readiness_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "distribution_readiness_smoke", SCRIPT_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def test_normalize_project_license_accepts_string() -> None:
    assert smoke.normalize_project_license("  AGPL-3.0-only\n") == "AGPL-3.0-only"


def test_normalize_project_license_accepts_legacy_text_table() -> None:
    assert smoke.normalize_project_license({"text": " AGPL-3.0-only "}) == (
        "AGPL-3.0-only"
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        ["AGPL-3.0-only"],
        {},
        {"file": "LICENSE"},
        {"text": None},
        {"text": True},
        {"text": ["AGPL-3.0-only"]},
        {"text": "AGPL-3.0-only", "file": "LICENSE"},
        {"text": "AGPL-3.0-only", "unexpected": "value"},
    ],
)
def test_normalize_project_license_rejects_malformed_forms(value: object) -> None:
    assert smoke.normalize_project_license(value) == ""


def test_normalize_project_license_rejects_subclasses() -> None:
    class StringSubclass(str):
        pass

    class DictSubclass(dict):
        pass

    assert smoke.normalize_project_license(StringSubclass("AGPL-3.0-only")) == ""
    assert smoke.normalize_project_license(DictSubclass(text="AGPL-3.0-only")) == ""
    assert smoke.normalize_project_license({"text": StringSubclass("AGPL")}) == ""


def test_current_repository_license_metadata_is_supported() -> None:
    pyproject = smoke.load_pyproject()
    assert type(pyproject["project"]["license"]) is str
    assert (
        smoke.normalize_project_license(pyproject["project"]["license"])
        == "AGPL-3.0-only"
    )


def test_metadata_check_accepts_string_license() -> None:
    checks = []
    smoke.check_metadata(checks, smoke.load_pyproject())

    license_check = next(
        check for check in checks if check.name == "pyproject declares AGPL-3.0-only"
    )
    assert license_check.status == smoke.PASS
    assert license_check.detail == "license='AGPL-3.0-only'"


def test_docker_check_matches_string_license(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        'LABEL org.opencontainers.image.licenses="AGPL-3.0-only"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke, "REPO", tmp_path)
    checks = []

    smoke.check_docker(checks, {"project": {"license": " AGPL-3.0-only "}})

    license_check = next(
        check
        for check in checks
        if check.name == "Docker license label matches pyproject"
    )
    assert license_check.status == smoke.PASS
    assert license_check.detail == "license='AGPL-3.0-only'"
