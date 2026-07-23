from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from plugins.pwp.compiler import render_template, validate_tokens
from plugins.pwp.theme_validator import validate_theme_package
from plugins.pwp import resources


FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "pwp_theme" / "valid_theme"


def test_compiler_and_validator_load_bundled_resources_from_traversable_root(
    tmp_path: Path, monkeypatch
) -> None:
    package_root = tmp_path / "package-root"
    shutil.copytree(Path(__file__).resolve().parents[1] / "templates", package_root / "templates")
    shutil.copytree(Path(__file__).resolve().parents[1] / "schemas", package_root / "schemas")

    archive_path = tmp_path / "pwp-bundled-resources.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for file_path in package_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(package_root).as_posix())

    monkeypatch.setattr(resources, "_PACKAGE_ROOT", zipfile.Path(archive_path))

    tokens = resources.bundled_json("templates", "tokens.json")
    validate_tokens(tokens)

    html = render_template("corporate")
    assert "<style id=\"pwp-tokens\">" in html
    assert "--pwp-color-primary:" in html

    theme_root = tmp_path / "theme"
    shutil.copytree(FIXTURES_ROOT, theme_root)
    result = validate_theme_package(theme_root)

    assert result.ok, result.errors
    assert result.warnings == []