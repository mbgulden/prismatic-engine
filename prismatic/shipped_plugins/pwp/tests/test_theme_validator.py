from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[4]

from prismatic.shipped_plugins.pwp.theme_validator import validate_theme_package

VALID_THEME = _THIS_DIR / "fixtures" / "pwp_theme" / "valid_theme"


def test_valid_theme_package_passes_contract_validation() -> None:
    result = validate_theme_package(VALID_THEME)
    assert result.ok, result.errors
    assert result.warnings == []


def test_validator_rejects_missing_manifest_entrypoint(tmp_path: Path) -> None:
    theme = tmp_path / "theme"
    shutil.copytree(VALID_THEME, theme)
    manifest_path = theme / "theme.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entrypoints"].pop("emdashMap")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_theme_package(theme)

    assert not result.ok
    assert "theme.json missing required entrypoint: emdashMap" in result.errors


def test_validator_rejects_missing_token_group(tmp_path: Path) -> None:
    theme = tmp_path / "theme"
    shutil.copytree(VALID_THEME, theme)
    token_path = theme / "tokens" / "tokens.json"
    tokens = json.loads(token_path.read_text(encoding="utf-8"))
    tokens.pop("zIndex")
    token_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

    result = validate_theme_package(theme)

    assert not result.ok
    assert "Token file missing required group: zIndex" in result.errors


def test_validator_rejects_emdash_unknown_module_reference(tmp_path: Path) -> None:
    theme = tmp_path / "theme"
    shutil.copytree(VALID_THEME, theme)
    emdash_path = theme / "emdash" / "fields.json"
    emdash = json.loads(emdash_path.read_text(encoding="utf-8"))
    emdash["blocks"].append(
        {
            "blockId": "ghost-module",
            "component": "Ghost",
            "fields": {"title": {"type": "string", "editable": True}},
        }
    )
    emdash_path.write_text(json.dumps(emdash, indent=2), encoding="utf-8")

    result = validate_theme_package(theme)

    assert not result.ok
    assert "EmDash map references unknown module blockId: ghost-module" in result.errors


def test_repo_local_pwp_theme_validate_command_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/pwp", "theme", "validate", str(VALID_THEME)],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "OK" in completed.stdout


def test_repo_local_pwp_theme_validate_command_fails_for_bad_package(
    tmp_path: Path,
) -> None:
    bad_theme = tmp_path / "bad-theme"
    bad_theme.mkdir()
    completed = subprocess.run(
        [sys.executable, "scripts/pwp", "theme", "validate", str(bad_theme)],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Missing theme.json manifest" in completed.stdout
