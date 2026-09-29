#!/usr/bin/env python3
"""Release-readiness checks for Prismatic Engine.

This script is intentionally local-only and credential-free. It validates the
release engineering layer needed before tagging or publishing a public release.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found]
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "CHANGELOG.md",
    "docs/release-process.md",
    "docs/migrations.md",
    "docs/upgrade-guide.md",
    "docs/release-checklist.md",
    "docs/stable-cli-entrypoints.md",
    "config/prismatic.sample.yaml",
    "scripts/bootstrap_env.sh",
    "scripts/release_smoke.py",
    "scripts/release_check.py",
    "Dockerfile",
    ".devcontainer/devcontainer.json",
    ".github/workflows/test.yml",
    ".github/workflows/publish.yml",
]

REQUIRED_MARKERS = {
    "CHANGELOG.md": [
        "## Unreleased",
        "## [0.2.0]",
        "Migration notes",
        "Release engineering",
    ],
    "docs/release-process.md": [
        "Versioning",
        "Tagged releases",
        "Release checklist",
        "Smoke test",
        "PyPI",
    ],
    "docs/migrations.md": [
        "0.1.x to 0.2.0",
        "PRISMATIC_STATE_DIR",
        "plugin_jobs.json",
        "plugin_artifacts.json",
    ],
    "docs/upgrade-guide.md": ["Upgrade path", "Back up state", "Run release smoke"],
    "docs/release-checklist.md": ["Pre-release", "Tag", "Post-release", "Rollback"],
    "docs/stable-cli-entrypoints.md": [
        "prismatic",
        "prismatic-gateway",
        "plugin-load-gate",
    ],
    "config/prismatic.sample.yaml": [
        "version: 1",
        "state_dir:",
        "gateway:",
        "plugins:",
    ],
    "scripts/bootstrap_env.sh": [
        "PRISMATIC_STATE_DIR",
        "python -m pip install",
        "public_launch_smoke.py",
    ],
    ".github/workflows/test.yml": [
        "smoke",
        "review factory gate",
        "signal",
        "nightly-full-suite",
    ],
    ".github/workflows/publish.yml": [
        "github.event_name == 'workflow_dispatch'",
        "twine check",
        "attest-build-provenance",
    ],
}

EXPECTED_SCRIPTS = {
    "prismatic",
    "plugin-load-gate",
    "prismatic-engine",
    "prismatic-engine-skills",
    "prismatic-lock",
    "prismatic-admin",
    "prismatic-journal",
    "prismatic-journal-snapshot",
    "prismatic-linear-import",
    "prismatic-second-witness",
    "prismatic-gateway",
    "prismatic-api",
}


def _run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout
    )


def _read_pyproject() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def check_files_and_markers() -> list[str]:
    failures: list[str] = []
    for rel in REQUIRED_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            failures.append(f"missing required release file: {rel}")
            continue
        if (
            path.is_file()
            and not path.read_text(encoding="utf-8", errors="replace").strip()
        ):
            failures.append(f"empty required release file: {rel}")
    for rel, markers in REQUIRED_MARKERS.items():
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in markers:
            if marker not in text:
                failures.append(f"{rel} missing marker {marker!r}")
    return failures


def check_versioning() -> list[str]:
    failures: list[str] = []
    project = _read_pyproject()["project"]
    version = project["version"]
    init_text = (REPO_ROOT / "prismatic/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    runtime_version = match.group(1) if match else None
    if runtime_version != version:
        failures.append(
            f"runtime version {runtime_version!r} != pyproject version {version!r}"
        )
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        failures.append(f"CHANGELOG.md missing release section for {version}")
    if not re.match(r"^\d+\.\d+\.\d+(?:[a-zA-Z0-9.-]+)?$", version):
        failures.append(f"project version is not semver-like: {version}")
    return failures


def check_entrypoints_and_extras() -> list[str]:
    failures: list[str] = []
    project = _read_pyproject()["project"]
    scripts = set(project.get("scripts", {}))
    missing_scripts = EXPECTED_SCRIPTS - scripts
    if missing_scripts:
        failures.append(f"pyproject missing stable scripts: {sorted(missing_scripts)}")
    optional = project.get("optional-dependencies", {})
    for extra in ["gateway", "all", "dev", "release"]:
        if extra not in optional:
            failures.append(f"pyproject missing optional extra {extra!r}")
    return failures


def check_release_commands() -> list[str]:
    failures: list[str] = []
    commands = [
        ([sys.executable, "scripts/public_launch_smoke.py"], "PUBLIC_LAUNCH_SMOKE_OK"),
        (
            [sys.executable, "scripts/public_security_readiness_audit.py"],
            "PUBLIC_SECURITY_READINESS_OK",
        ),
    ]
    for cmd, marker in commands:
        result = _run(cmd, timeout=240)
        output = result.stdout + result.stderr
        if result.returncode != 0 or marker not in output:
            failures.append(
                f"{' '.join(cmd)} failed or missed {marker}: {output[-2000:]}"
            )
    return failures


def check_build_metadata() -> list[str]:
    failures: list[str] = []
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for marker in [
        "setuptools>=64",
        "wheel",
        "build-backend",
        'requires-python = ">=3.10"',
    ]:
        if marker not in pyproject:
            failures.append(f"pyproject missing build metadata marker {marker!r}")
    return failures


def run_checks() -> dict[str, Any]:
    checks = {
        "files_and_markers": check_files_and_markers(),
        "versioning": check_versioning(),
        "entrypoints_and_extras": check_entrypoints_and_extras(),
        "release_commands": check_release_commands(),
        "build_metadata": check_build_metadata(),
    }
    failures: list[str] = []
    for name, result in checks.items():
        if result:
            failures.append(f"{name}: {result}")
    return {"ok": not failures, "failures": failures, "checks": checks}


def main() -> int:
    result = run_checks()
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["ok"]:
        print("RELEASE_READINESS_OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
