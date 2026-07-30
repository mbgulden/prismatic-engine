from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = REPO_ROOT / "prismatic/gateway/templates/dashboard.html"
MANIFEST = REPO_ROOT / "prismatic/gateway/dashboard_src/manifest.json"
BUILDER = REPO_ROOT / "scripts/build_dashboard.py"


def _builder_module():
    spec = importlib.util.spec_from_file_location("build_dashboard", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _html() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_dashboard_fragments_rebuild_exact_generated_bytes_and_sha() -> None:
    builder = _builder_module()
    generated = DASHBOARD.read_bytes()
    rebuilt = builder.build_bytes(MANIFEST)
    assert rebuilt == generated
    assert hashlib.sha256(rebuilt).hexdigest() == hashlib.sha256(generated).hexdigest()


def test_build_dashboard_check_and_repeated_builds_are_deterministic() -> None:
    first = subprocess.run(
        [sys.executable, "scripts/build_dashboard.py"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    sha_one = hashlib.sha256(DASHBOARD.read_bytes()).hexdigest()

    second = subprocess.run(
        [sys.executable, "scripts/build_dashboard.py"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    sha_two = hashlib.sha256(DASHBOARD.read_bytes()).hexdigest()
    assert sha_one == sha_two

    check = subprocess.run(
        [sys.executable, "scripts/build_dashboard.py", "--check"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "fresh" in check.stdout


def test_all_tab_buttons_and_sections_are_unique_one_to_one() -> None:
    html = _html()
    button_tabs = re.findall(
        r'<button id="tab-btn-([^"]+)"[^>]*onclick="switchTab\(\'([^\']+)\'\)"', html
    )
    section_tabs = re.findall(r'<div id="section-([^"]+)"', html)

    assert len(button_tabs) == 11
    assert len(section_tabs) == 11
    assert len({button_id for button_id, _ in button_tabs}) == 11
    assert len({target for _, target in button_tabs}) == 11
    assert len(set(section_tabs)) == 11
    assert {button_id for button_id, _ in button_tabs} == {
        target for _, target in button_tabs
    }
    assert {target for _, target in button_tabs} == set(section_tabs)


def test_deep_link_and_canonical_dashboard_markers_are_preserved() -> None:
    html = _html()
    required_markers = [
        "Prismatic Hub Dashboard",
        'id="tab-btn-dashboard"',
        'data-proof-marker="ingestion"',
        'id="tab-btn-merge"',
        "governance",
        "native-cron",
        "workspace-tree-mobile-responsive",
        "file viewer · powered by /api/workspaces",
        "selectedWorkspaceId",
        "selectedRelativePath",
        "data-workspace-id=",
        "data-relative-path=",
        "workspace_id: workspaceId",
        "workspace-legacy-link",
        "Resources · Model usage and budget caps",
        "Jules Daily Capacity",
        'data-proof-marker="jules-daily-capacity-resources"',
        "/jules/capacity",
        "AGY_OVERNIGHT_READINESS_GUARD_OK",
        "RAW_AGENT_OUTPUT_REPAIR_QUEUE_OK",
        "pwp-lifecycle-history",
    ]
    missing = [marker for marker in required_markers if marker not in html]
    assert not missing
    assert html.count("Prismatic Hub Dashboard") == 1
    assert html.count('id="tab-btn-quota"') == 1
    assert html.count('id="section-quota"') == 1
    assert html.count('id="section-pwp"') == 1
    assert html.count('id="section-crons"') == 1
    assert html.count('id="section-workspaces"') == 1
    assert "/api/workspace-tree/node?file=" not in html
    assert "/api/workspace-tree/preview?file=" not in html
    assert "/workspace-tree?file=" not in html
    assert "data-path=" not in html
    assert "canonical-merge-winner-map-2026-07-06.md" not in html
    assert 'href="/dashboard#workspaces"' in html


def test_manifest_missing_duplicate_and_traversal_entries_fail_closed(
    tmp_path: Path,
) -> None:
    builder = _builder_module()
    src = tmp_path / "dashboard_src"
    src.mkdir()
    (src / "a.html").write_text("a", encoding="utf-8")

    def write_manifest(fragments: list[dict[str, str]]) -> Path:
        manifest = src / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "generated": "prismatic/gateway/templates/dashboard.html",
                    "fragments": fragments,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    missing = write_manifest([{"path": "missing.html"}])
    try:
        builder.build_bytes(missing)
    except builder.DashboardBuildError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("missing fragment did not fail closed")

    duplicate = write_manifest([{"path": "a.html"}, {"path": "a.html"}])
    try:
        builder.build_bytes(duplicate)
    except builder.DashboardBuildError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate fragment did not fail closed")

    traversal = write_manifest([{"path": "../outside.html"}])
    try:
        builder.build_bytes(traversal)
    except builder.DashboardBuildError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("traversal fragment did not fail closed")


def test_wheel_contains_generated_dashboard_template_and_sources(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("prismatic_engine-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert "prismatic/gateway/templates/dashboard.html" in names
    assert "prismatic/gateway/dashboard_src/manifest.json" in names
    assert "prismatic/gateway/dashboard_src/scripts/dashboard.js" in names
