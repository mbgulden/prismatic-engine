"""Committed regression proof for clean-room non-editable installed-wheel shipped-plugin contract.

TASK: DISTRIBUTION-PORTABILITY-5
Verifies that:
1. Wheel and sdist contain required shipped-plugin manifests, modules, and assets under prismatic/shipped_plugins/
2. Shipped plugin catalog includes and validates the reference plugin from empty CWD
3. Plugin load gate passes from empty CWD
4. Authorized policy preview for example-plugin returns allow from empty CWD
5. Unknown plugin still blocks
6. Public launch smoke and release readiness succeed from installed distribution context
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wheel_and_sdist_content_inspection(tmp_path: Path) -> None:
    """Requirement 9: Assert wheel and sdist contain shipped plugin manifests/modules/assets."""
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(dist_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))
    assert len(wheels) == 1, f"Expected 1 wheel, found: {wheels}"
    assert len(sdists) == 1, f"Expected 1 sdist, found: {sdists}"

    # Inspect wheel contents
    with zipfile.ZipFile(wheels[0]) as zf:
        members = zf.namelist()
        required_wheel_files = [
            "prismatic/shipped_plugins/example_plugin/plugin-manifest.yaml",
            "prismatic/shipped_plugins/example_plugin/plugin.py",
            "prismatic/shipped_plugins/pwp/plugin-manifest.yaml",
            "prismatic/shipped_plugins/pwp/plugin.py",
            "prismatic/shipped_plugins/prismatic_hello_world/plugin-manifest.yaml",
            "prismatic/shipped_plugins/prismatic_hello_world/plugin.py",
            "prismatic/resources/antigravity/workspace/agents/skills.json",
            "prismatic/resources/antigravity/workspace/agents/rules/prismatic-engine.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-agy-execution/SKILL.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-agy-execution/templates/task.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-agy-execution/templates/implementation-plan.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-agy-execution/templates/result.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-context-discipline/SKILL.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-engine-operations/SKILL.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-evidence-and-review/SKILL.md",
            "prismatic/resources/antigravity/workspace/agents/skills/prismatic-worktree-safety/SKILL.md",
        ]
        for item in required_wheel_files:
            assert item in members, f"Wheel missing required file {item}"

    # Inspect sdist contents
    with tarfile.open(sdists[0], "r:gz") as tf:
        sdist_members = tf.getnames()
        for item in required_wheel_files:
            assert any(m.endswith(item) for m in sdist_members), (
                f"Sdist missing required file {item}"
            )


def test_clean_room_installed_wheel_plugin_contract(tmp_path: Path) -> None:
    """Requirement 8: Build exact wheel, install non-editably in fresh venv, verify from empty CWD."""
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel_path = wheels[0]

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_py = venv_dir / "bin" / "python"
    venv_pip = venv_dir / "bin" / "pip"

    subprocess.run(
        [str(venv_pip), "install", "--force-reinstall", f"{wheel_path}[all]"],
        check=True,
    )

    empty_cwd = tmp_path / "empty_cwd"
    empty_cwd.mkdir()

    # Clean environment without PYTHONPATH or PRISMATIC_PLUGINS_DIR
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "PRISMATIC_PLUGINS_DIR"}
    }
    clean_env["PATH"] = f"{venv_dir / 'bin'}:{clean_env.get('PATH', '')}"

    # 1. Shipped plugin catalog includes and validates example-plugin
    cmd_cat = [
        str(venv_py),
        "-c",
        "from prismatic.plugin_architecture import plugin_catalog; "
        "cat = plugin_catalog(); "
        "assert cat['ready_count'] >= 1; "
        "names = [p['name'] for p in cat['plugins']]; "
        "assert 'example-plugin' in names, f'example-plugin missing: {names}'; "
        "assert 'pwp-design-token-plugin' in names, f'pwp-design-token-plugin missing: {names}'",
    ]
    res_cat = subprocess.run(
        cmd_cat, cwd=empty_cwd, env=clean_env, capture_output=True, text=True
    )
    assert res_cat.returncode == 0, (
        f"Catalog check failed: {res_cat.stderr}\n{res_cat.stdout}"
    )

    # 2. Load gate passes
    cmd_load = [
        str(venv_py),
        "-c",
        "from prismatic.quality.plugin_load import verify_shipped_plugins_load; "
        "res = verify_shipped_plugins_load(); "
        "assert res.passed, res.reason",
    ]
    res_load = subprocess.run(
        cmd_load, cwd=empty_cwd, env=clean_env, capture_output=True, text=True
    )
    assert res_load.returncode == 0, (
        f"Load gate failed: {res_load.stderr}\n{res_load.stdout}"
    )

    # 3. Authorized policy preview for reference plugin returns allow
    cmd_policy_allow = [
        str(venv_py),
        "-c",
        "from prismatic.plugin_policy import preview_policy; "
        "p = preview_policy('job_request', plugin_name='example-plugin', action='smoke_validate'); "
        "assert p['decision'] == 'allow', p",
    ]
    res_policy_allow = subprocess.run(
        cmd_policy_allow, cwd=empty_cwd, env=clean_env, capture_output=True, text=True
    )
    assert res_policy_allow.returncode == 0, (
        f"Policy allow preview failed: {res_policy_allow.stderr}\n{res_policy_allow.stdout}"
    )

    # 4. Unknown plugin still blocks
    cmd_policy_block = [
        str(venv_py),
        "-c",
        "from prismatic.plugin_policy import preview_policy; "
        "p = preview_policy('job_request', plugin_name='missing-plugin', action='run'); "
        "assert p['decision'] == 'block'; "
        "assert any('unknown plugin' in b for b in p['blockers']), p",
    ]
    res_policy_block = subprocess.run(
        cmd_policy_block, cwd=empty_cwd, env=clean_env, capture_output=True, text=True
    )
    assert res_policy_block.returncode == 0, (
        f"Policy block preview failed: {res_policy_block.stderr}\n{res_policy_block.stdout}"
    )

    agy_contract = subprocess.run(
        [str(venv_dir / "bin" / "prismatic"), "agy", "contract"],
        cwd=empty_cwd,
        env=clean_env,
        capture_output=True,
        text=True,
    )
    assert agy_contract.returncode == 0, agy_contract.stderr
    contract = json.loads(agy_contract.stdout)
    assert contract["transport"] == "tmux-durable-anchor"
    assert contract["prompt_prefix"] == "/goal "
    assert contract["runtime_deadline"] is None
    assert contract["runtime_policy"] == "no-wall-clock-cap-progress-supervised"
    registry_check = subprocess.run(
        [
            str(venv_py),
            "-c",
            "from importlib.resources import files; "
            "import importlib; "
            "p=files('prismatic.harnesses').joinpath('registry.json'); "
            "assert p.is_file(); "
            "assert importlib.import_module('prismatic.harnesses.agy_cli').AGYCLIHarness().name == 'agy-cli'; "
            "assert importlib.import_module('prismatic.agy_activity').list_agy_activity_runs()['status'] == 'unavailable'",
        ],
        cwd=empty_cwd,
        env=clean_env,
        capture_output=True,
        text=True,
    )
    assert registry_check.returncode == 0, registry_check.stderr

    smoke_env = {
        **clean_env,
        "PRISMATIC_EXPECT_INSTALLED_PREFIX": str(venv_dir),
        "HOME": str(tmp_path / "home"),
    }
    public_smoke = subprocess.run(
        [str(venv_py), str(REPO_ROOT / "scripts/public_launch_smoke.py")],
        cwd=empty_cwd,
        env=smoke_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert public_smoke.returncode == 0, (
        f"Installed-wheel public launch smoke failed: "
        f"{public_smoke.stderr}\n{public_smoke.stdout}"
    )
    assert "PUBLIC_LAUNCH_SMOKE_OK" in public_smoke.stdout

    release_smoke = subprocess.run(
        [str(venv_py), str(REPO_ROOT / "scripts/release_smoke.py")],
        cwd=empty_cwd,
        env=smoke_env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert release_smoke.returncode == 0, (
        f"Installed-wheel release smoke failed: "
        f"{release_smoke.stderr}\n{release_smoke.stdout}"
    )
    assert "RELEASE_SMOKE_OK" in release_smoke.stdout
