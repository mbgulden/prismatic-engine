from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from prismatic.shipped_plugins.pwp.theme_diff import (
    check_theme_compatibility,
    diff_theme_packages,
    engine_version_satisfies,
)

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[3]

VALID_THEME = _THIS_DIR / "fixtures" / "pwp_theme" / "valid_theme"


def _copy_theme(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(VALID_THEME, target)
    return target


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_engine_version_satisfies_common_semver_ranges() -> None:
    assert engine_version_satisfies(">=0.2.0", "0.3.0")
    assert engine_version_satisfies(">=0.2.0 <1.0.0", "0.9.1")
    assert not engine_version_satisfies(">=0.2.0 <1.0.0", "1.0.0")
    assert engine_version_satisfies("^1.2.0", "1.5.0")
    assert not engine_version_satisfies("^1.2.0", "2.0.0")
    assert engine_version_satisfies("~1.2.0", "1.2.9")
    assert not engine_version_satisfies("~1.2.0", "1.3.0")


def test_check_theme_compatibility_accepts_matching_engine_version() -> None:
    result = check_theme_compatibility(VALID_THEME, "0.2.0")

    assert result.ok, result.errors
    assert result.range == ">=0.2.0"


def test_check_theme_compatibility_rejects_out_of_range_engine_version(
    tmp_path: Path,
) -> None:
    theme = _copy_theme(tmp_path, "theme")
    manifest_path = theme / "theme.json"
    manifest = _read_json(manifest_path)
    manifest["engineCompatibility"] = ">=0.4.0 <1.0.0"
    _write_json(manifest_path, manifest)

    result = check_theme_compatibility(theme, "0.3.9")

    assert not result.ok
    assert not result.compatible
    assert result.errors == []


def test_diff_reports_added_module_as_safe_change(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    manifest_path = after / "theme.json"
    manifest = _read_json(manifest_path)
    manifest["version"] = "0.2.0"
    manifest["modules"].append("testimonial")
    _write_json(manifest_path, manifest)
    module_contract = _read_json(after / "modules" / "hero.json")
    module_contract["id"] = "testimonial"
    module_contract["component"] = "Testimonial.astro"
    _write_json(after / "modules" / "testimonial.json", module_contract)
    (after / "src" / "components" / "Testimonial.astro").write_text(
        "---\n---\n<section />\n", encoding="utf-8"
    )
    emdash_path = after / "emdash" / "fields.json"
    emdash = _read_json(emdash_path)
    emdash["blocks"].append(
        {
            "blockId": "testimonial",
            "component": "Testimonial",
            "fields": {"quote": {"type": "string", "editable": True}},
        }
    )
    _write_json(emdash_path, emdash)

    diff = diff_theme_packages(before, after)

    assert not diff.errors
    module_changes = [
        change for change in diff.changes if change.path == "modules.testimonial"
    ]
    assert module_changes
    assert module_changes[0].breaking is False


def test_diff_marks_removed_module_as_breaking(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    manifest_path = after / "theme.json"
    manifest = _read_json(manifest_path)
    manifest["version"] = "0.2.0"
    manifest["modules"].remove("lead-capture")
    _write_json(manifest_path, manifest)
    (after / "modules" / "lead-capture.json").unlink()
    emdash_path = after / "emdash" / "fields.json"
    emdash = _read_json(emdash_path)
    emdash["blocks"] = [
        block for block in emdash["blocks"] if block["blockId"] != "lead-capture"
    ]
    _write_json(emdash_path, emdash)

    diff = diff_theme_packages(before, after)

    assert not diff.errors
    assert any(
        change.path == "modules.lead-capture" and change.breaking
        for change in diff.breaking_changes
    )
    assert not diff.ok


def test_diff_marks_target_engine_incompatibility_as_breaking(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    manifest_path = after / "theme.json"
    manifest = _read_json(manifest_path)
    manifest["version"] = "0.2.0"
    manifest["engineCompatibility"] = ">=0.4.0"
    _write_json(manifest_path, manifest)

    diff = diff_theme_packages(before, after, engine_version="0.3.0")

    assert not diff.errors
    assert any(
        change.kind == "compatibility" and change.breaking
        for change in diff.breaking_changes
    )


def test_repo_local_pwp_theme_diff_command_emits_json(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    manifest_path = after / "theme.json"
    manifest = _read_json(manifest_path)
    manifest["version"] = "0.2.0"
    _write_json(manifest_path, manifest)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "theme",
            "diff",
            "--from",
            str(before),
            "--to",
            str(after),
            "--engine-version",
            "0.2.0",
            "--json",
        ],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["fromVersion"] == "0.1.0"
    assert payload["toVersion"] == "0.2.0"
    assert any(change["path"] == "version" for change in payload["changes"])


def test_repo_local_pwp_theme_check_compat_command_fails_for_bad_range() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "theme",
            "check-compat",
            str(VALID_THEME),
            "--engine-version",
            "0.1.0",
        ],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "FAILED" in completed.stdout
