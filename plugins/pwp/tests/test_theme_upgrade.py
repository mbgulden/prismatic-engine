from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.theme_upgrade import plan_theme_upgrade  # noqa: E402

VALID_THEME = _THIS_DIR / "fixtures" / "pwp_theme" / "valid_theme"


def _copy_theme(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(VALID_THEME, target)
    return target


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_upgrade_preserves_real_tenant_token_overrides(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    tokens_path = after / "tokens" / "tokens.json"
    tokens = _read_json(tokens_path)
    tokens["color"]["background"]["page"]["$value"] = "#f8fafc"
    _write_json(tokens_path, tokens)
    overrides = tmp_path / "tenant-overrides.json"
    _write_json(
        overrides,
        {
            "tokens": {
                "color": {
                    "background": {"page": {"$value": "#101827", "$type": "color"}}
                },
                "space": {"md": {"$value": "1rem", "$type": "dimension"}},
            }
        },
    )

    plan = plan_theme_upgrade(before, after, overrides)

    assert not plan.errors
    assert (
        plan.preserved_overrides["tokens"]["color"]["background"]["page"]["$value"]
        == "#101827"
    )
    assert "space" not in plan.preserved_overrides["tokens"]
    assert any(
        conflict.path == "tokens.color.background.page"
        and conflict.reason == "target-default-changed-under-tenant-override"
        for conflict in plan.conflicts
    )


def test_upgrade_flags_overrides_for_removed_targets(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    tokens_path = after / "tokens" / "tokens.json"
    tokens = _read_json(tokens_path)
    del tokens["shadow"]["sm"]
    _write_json(tokens_path, tokens)
    overrides = tmp_path / "tenant-overrides.json"
    _write_json(
        overrides, {"tokens": {"shadow": {"sm": {"$value": "none", "$type": "shadow"}}}}
    )

    plan = plan_theme_upgrade(before, after, overrides)

    assert not plan.errors
    assert plan.preserved_overrides == {}
    assert [conflict.reason for conflict in plan.conflicts] == [
        "override-target-removed"
    ]
    assert plan.conflicts[0].path == "tokens.shadow.sm"


def test_repo_local_pwp_theme_upgrade_command_emits_json(tmp_path: Path) -> None:
    before = _copy_theme(tmp_path, "before")
    after = _copy_theme(tmp_path, "after")
    manifest = _read_json(after / "theme.json")
    manifest["version"] = "0.2.0"
    _write_json(after / "theme.json", manifest)
    overrides = tmp_path / "tenant-overrides.json"
    _write_json(
        overrides,
        {
            "tokens": {
                "color": {"text": {"primary": {"$value": "#222222", "$type": "color"}}}
            }
        },
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "theme",
            "upgrade",
            "--from",
            str(before),
            "--to",
            str(after),
            "--tenant-overrides",
            str(overrides),
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
    assert payload["themeDiff"]["toVersion"] == "0.2.0"
    assert (
        payload["preservedOverrides"]["tokens"]["color"]["text"]["primary"]["$value"]
        == "#222222"
    )
