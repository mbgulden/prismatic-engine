from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.theme_installer import (  # noqa: E402
    install_theme_package,
    resolve_theme_reference,
)

VALID_THEME = _THIS_DIR / "fixtures" / "pwp_theme" / "valid_theme"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_theme_package_copies_contract_assets_into_astro_project(
    tmp_path: Path,
) -> None:
    target = tmp_path / "astro-site"

    result = install_theme_package(
        VALID_THEME,
        target,
        tenant="sentinelitad",
        engine_version="0.2.0",
    )

    assert result.ok, result.errors
    assert (target / "pwp" / "themes" / "sentinelitad" / "theme.json").exists()
    assert (
        target / "pwp" / "themes" / "sentinelitad" / "tokens" / "tokens.json"
    ).exists()
    assert (
        target / "pwp" / "themes" / "sentinelitad" / "emdash" / "fields.json"
    ).exists()
    assert (
        target / "pwp" / "themes" / "sentinelitad" / "modules" / "hero.json"
    ).exists()
    assert (
        target
        / "pwp"
        / "themes"
        / "sentinelitad"
        / "schemas"
        / "modules"
        / "hero.schema.json"
    ).exists()
    assert (target / "src" / "styles" / "pwp-theme.css").exists()
    assert (target / "src" / "layouts" / "PwpBaseLayout.astro").exists()
    assert (target / "src" / "components" / "pwp" / "Hero.astro").exists()
    assert (target / "src" / "content.config.ts").exists()

    install_manifest = _read_json(
        target / "pwp" / "themes" / "sentinelitad" / "install-manifest.json"
    )
    assert install_manifest["tenant"] == "sentinelitad"
    assert install_manifest["themeId"] == "pwp.theme.trust-light"
    assert install_manifest["themeVersion"] == "0.1.0"
    assert install_manifest["tokenHash"].startswith("sha256:")
    assert install_manifest["moduleHash"].startswith("sha256:")
    assert "src/styles/pwp-theme.css" in install_manifest["copiedFiles"]


def test_install_theme_package_refuses_overwrite_without_force(tmp_path: Path) -> None:
    target = tmp_path / "astro-site"
    first = install_theme_package(VALID_THEME, target, tenant="sentinelitad")
    assert first.ok, first.errors

    second = install_theme_package(VALID_THEME, target, tenant="sentinelitad")

    assert not second.ok
    assert any("Refusing to overwrite" in error for error in second.errors)


def test_resolve_theme_reference_reads_registry_index(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "themes": [
                    {
                        "id": "pwp.theme.trust-light",
                        "version": "0.1.0",
                        "path": "trust-light/theme.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    theme_dir = tmp_path / "trust-light"
    theme_dir.mkdir()
    (theme_dir / "theme.json").write_text("{}", encoding="utf-8")

    assert resolve_theme_reference("trust-light", registry) == theme_dir.resolve()
    assert (
        resolve_theme_reference("pwp.theme.trust-light@0.1.0", registry)
        == theme_dir.resolve()
    )


def test_repo_local_pwp_theme_install_command_emits_json(tmp_path: Path) -> None:
    target = tmp_path / "astro-site"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "theme",
            "install",
            str(VALID_THEME),
            "--target",
            str(target),
            "--tenant",
            "sentinelitad",
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
    assert payload["ok"] is True
    assert payload["tenant"] == "sentinelitad"
    assert payload["themeId"] == "pwp.theme.trust-light"
    assert (
        target / "pwp" / "themes" / "sentinelitad" / "install-manifest.json"
    ).exists()


def test_repo_local_pwp_theme_install_rejects_invalid_package(tmp_path: Path) -> None:
    bad_theme = tmp_path / "bad-theme"
    bad_theme.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "theme",
            "install",
            str(bad_theme),
            "--target",
            str(tmp_path / "astro-site"),
            "--tenant",
            "sentinelitad",
        ],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Missing theme.json manifest" in completed.stdout
