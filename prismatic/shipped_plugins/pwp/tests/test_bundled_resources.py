"""Bundled-asset loading proof: PWP read-only assets resolve through
importlib.resources even when the package root is a zip-backed traversable.

Ported from stale PR #375 (never landed) as a fresh port on current main;
imports use the canonical package path (Slice 1 rewrote the dead
``prismatic.shipped_plugins.pwp`` aliases to ``prismatic.shipped_plugins.pwp``).
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = next(
    (p for p in _THIS_DIR.parents if (p / "pyproject.toml").exists()),
    _THIS_DIR.parents[2],
)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from prismatic.shipped_plugins.pwp import resources  # noqa: E402
from prismatic.shipped_plugins.pwp.compiler import (  # noqa: E402
    render_template,
    validate_tokens,
)
from prismatic.shipped_plugins.pwp.theme_validator import (  # noqa: E402
    validate_theme_package,
)


FIXTURES_ROOT = _THIS_DIR / "fixtures" / "pwp_theme" / "valid_theme"


def test_compiler_and_validator_load_bundled_resources_from_traversable_root(
    tmp_path: Path, monkeypatch
) -> None:
    package_root = tmp_path / "package-root"
    shutil.copytree(_THIS_DIR.parent / "templates", package_root / "templates")
    shutil.copytree(_THIS_DIR.parent / "schemas", package_root / "schemas")

    archive_path = tmp_path / "pwp-bundled-resources.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for file_path in package_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(package_root).as_posix())

    # Simulate an installed (zipped) package: no repo layout is visible.
    monkeypatch.setattr(resources, "_PACKAGE_ROOT", zipfile.Path(archive_path))

    tokens = resources.bundled_json("templates", "tokens.json")
    validate_tokens(tokens)

    html = render_template("corporate")
    assert '<style id="pwp-tokens">' in html
    assert "--pwp-color-primary:" in html

    theme_root = tmp_path / "theme"
    shutil.copytree(FIXTURES_ROOT, theme_root)
    result = validate_theme_package(theme_root)

    assert result.ok, result.errors
    assert result.warnings == []
