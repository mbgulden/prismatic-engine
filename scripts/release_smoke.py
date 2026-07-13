#!/usr/bin/env python3
"""Public release smoke test for Prismatic Engine.

Run this after installing from a wheel/sdist or from an editable checkout. It is
local-only and credential-free.
"""

from __future__ import annotations

import json
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found]
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ENTRYPOINTS = [
    "prismatic",
    "plugin-load-gate",
    "prismatic-gateway",
    "prismatic-engine",
]


def run(cmd: list[str], marker: str | None = None) -> str:
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, text=True, capture_output=True, timeout=240
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{output[-3000:]}")
    if marker and marker not in output:
        raise RuntimeError(
            f"command missed marker {marker}: {' '.join(cmd)}\n{output[-3000:]}"
        )
    return output


def project_metadata() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return {
        "version": project["version"],
        "console_scripts": sorted(project.get("scripts", {})),
    }


def main() -> int:
    import prismatic
    from prismatic.cli import run as cli_run
    from prismatic.quality.plugin_load import verify_shipped_plugins_load

    cli_rc = cli_run([])
    if cli_rc != 0:
        raise RuntimeError(f"prismatic CLI returned {cli_rc}")

    load_result = verify_shipped_plugins_load(plugins_dir=REPO_ROOT / "plugins")
    if not load_result.passed:
        raise RuntimeError("plugin load gate failed: " + load_result.reason)

    launch = run(
        [sys.executable, "scripts/public_launch_smoke.py"], "PUBLIC_LAUNCH_SMOKE_OK"
    )
    security = run(
        [sys.executable, "scripts/public_security_readiness_audit.py"],
        "PUBLIC_SECURITY_READINESS_OK",
    )
    dashboard = run(
        [sys.executable, "scripts/dashboard_visual_qa.py"], "DASHBOARD_VISUAL_QA_OK"
    )
    metadata = project_metadata()
    missing = sorted(set(EXPECTED_ENTRYPOINTS) - set(metadata["console_scripts"]))
    if missing:
        raise RuntimeError(f"project metadata missing console scripts: {missing}")

    payload = {
        "ok": True,
        "runtime_version": prismatic.__version__,
        "package_metadata": metadata,
        "plugin_load_reason": load_result.reason,
        "launch_marker": "PUBLIC_LAUNCH_SMOKE_OK" in launch,
        "security_marker": "PUBLIC_SECURITY_READINESS_OK" in security,
        "dashboard_visual_marker": "DASHBOARD_VISUAL_QA_OK" in dashboard,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    print("RELEASE_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
