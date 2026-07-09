from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.visual_fixture_harness import (  # noqa: E402
    DEFAULT_VIEWPORTS,
    build_harness_plan,
    write_harness_files,
)

VALID_THEME = _THIS_DIR / "fixtures" / "pwp_theme" / "valid_theme"


def test_harness_plan_covers_pages_modules_variants_and_viewports(
    tmp_path: Path,
) -> None:
    plan = build_harness_plan(VALID_THEME, tmp_path / "fixtures")

    # 1 full-page fixture per viewport + 2 modules x 2 variants x 3 viewports.
    assert len(plan.cases) == 15
    assert {case.viewport.name for case in plan.cases} == {
        viewport["name"] for viewport in DEFAULT_VIEWPORTS
    }
    page_cases = [case for case in plan.cases if case.kind == "page"]
    assert len(page_cases) == 3
    module_matrix = {
        (case.module_id, case.variant, case.viewport.name)
        for case in plan.cases
        if case.kind == "module"
    }
    assert ("hero", "default", "desktop") in module_matrix
    assert ("hero", "split-media", "mobile") in module_matrix
    assert ("lead-capture", "form", "tablet") in module_matrix
    assert ("lead-capture", "mailto", "desktop") in module_matrix


def test_write_harness_files_emits_manifest_html_and_playwright_spec(
    tmp_path: Path,
) -> None:
    out = tmp_path / "pwp-fixtures"
    plan = build_harness_plan(VALID_THEME, out)

    write_harness_files(plan)

    manifest_path = Path(plan.manifest_file)
    spec_path = Path(plan.spec_file)
    assert manifest_path.exists()
    assert spec_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["themeId"] == "pwp.theme.trust-light"
    assert len(payload["cases"]) == len(plan.cases)
    assert "require('playwright')" in spec_path.read_text(encoding="utf-8")
    first_fixture = out / payload["cases"][0]["html_file"]
    assert first_fixture.exists()
    assert (
        'data-fixture-id="pwp-theme-trust-light__page__default__mobile"'
        in first_fixture.read_text(encoding="utf-8")
    )


def test_repo_local_pwp_theme_fixtures_command_emits_json(tmp_path: Path) -> None:
    out = tmp_path / "generated"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "theme",
            "fixtures",
            str(VALID_THEME),
            "--out",
            str(out),
            "--json",
        ],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["themeId"] == "pwp.theme.trust-light"
    assert len(payload["cases"]) == 15
    assert (out / "pwp-theme-fixtures.spec.js").exists()
