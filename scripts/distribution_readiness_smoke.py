#!/usr/bin/env python3
"""Prismatic Engine first-user distribution readiness smoke gate.

This is a repo-local, stdlib-only gate for deciding whether the current checkout
is publishable/private-demo-ready for a first user. It intentionally checks docs,
package metadata, console entrypoints, package data, Docker metadata, and
Michael-only path leaks before PyPI/GHCR publishing.

Usage:
    python3 scripts/distribution_readiness_smoke.py
    python3 scripts/distribution_readiness_smoke.py --fresh-install
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 CI
    import tomli as tomllib  # type: ignore[import-not-found]


REPO = Path(__file__).resolve().parents[1]
P0 = "P0"
P1 = "P1"
PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


@dataclass
class Check:
    name: str
    status: str
    severity: str
    detail: str


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def add(
    checks: list[Check], name: str, ok: bool, detail: str, severity: str = P0
) -> None:
    checks.append(
        Check(name=name, status=PASS if ok else FAIL, severity=severity, detail=detail)
    )


def warn(checks: list[Check], name: str, detail: str, severity: str = P1) -> None:
    checks.append(Check(name=name, status=WARN, severity=severity, detail=detail))


def load_pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def normalize_project_license(value: object) -> str:
    """Return a license expression from supported ``project.license`` forms."""
    if type(value) is str:
        return value.strip()
    if type(value) is dict and len(value) == 1:
        ((key, text),) = value.items()
        if type(key) is str and key == "text" and type(text) is str:
            return text.strip()
    return ""


def check_metadata(checks: list[Check], pyproject: dict) -> None:
    project = pyproject.get("project", {})
    license_text = normalize_project_license(project.get("license"))
    readme = project.get("readme")
    add(
        checks,
        "pyproject declares package name",
        project.get("name") == "prismatic-engine",
        f"name={project.get('name')!r}",
    )
    add(
        checks,
        "pyproject declares AGPL-3.0-only",
        license_text == "AGPL-3.0-only",
        f"license={license_text!r}",
    )
    add(
        checks,
        "pyproject readme exists",
        bool(readme and (REPO / readme).is_file()),
        f"readme={readme!r}",
    )
    license_file = REPO / "LICENSE"
    license_body = (
        license_file.read_text(encoding="utf-8", errors="replace")
        if license_file.exists()
        else ""
    )
    add(
        checks,
        "LICENSE file exists and is AGPL",
        "GNU AFFERO GENERAL PUBLIC LICENSE" in license_body,
        f"{rel(license_file)} present={license_file.exists()}",
    )


def check_readme(checks: list[Check]) -> None:
    readme_path = REPO / "README.md"
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    add(
        checks,
        "README has first-user quick start",
        "## Quick start" in text or "## First-User Quick Start" in text,
        "README must include quick start path",
    )
    forbidden = [
        "Internal to GrowthWebDev",
        "LICENSE` (TBD)",
        "curl -fsSL https://prismaticengine.com/install.sh | bash",
    ]
    missing_forbidden = [needle for needle in forbidden if needle in text]
    add(
        checks,
        "README has no false/TBD distribution claims",
        not missing_forbidden,
        f"forbidden={missing_forbidden}",
    )
    home_lines = []
    for i, line in enumerate(text.splitlines(), 1):
        if "/home/ubuntu" in line:
            home_lines.append(f"L{i}: {line.strip()}")
    add(
        checks,
        "README first-user docs avoid Michael-only /home/ubuntu paths",
        not home_lines,
        "Michael-only lines: " + ("; ".join(home_lines[:5]) if home_lines else "none"),
    )
    add(
        checks,
        "README documents operator/systemd as optional",
        "## Quick start" in text or "systemd" in text,
        "systemd must not be presented as first-user requirement",
    )


def check_package_data(checks: list[Check], pyproject: dict) -> None:
    package_data = (
        pyproject.get("tool", {}).get("setuptools", {}).get("package-data", {})
    )
    prismatic_data = package_data.get("prismatic", [])
    existing_dirs = ["skills", "templates", "config", "shipped_plugins"]
    for dirname in existing_dirs:
        d = REPO / "prismatic" / dirname
        if d.exists():
            has_pattern = any(
                item.startswith(f"{dirname}/") or item == f"{dirname}/**/*"
                for item in prismatic_data
            )
            add(
                checks,
                f"package-data includes prismatic/{dirname}",
                has_pattern,
                f"patterns={prismatic_data}",
            )
    default_config = REPO / "prismatic" / "config" / "default_config.yaml"
    add(
        checks,
        "default config template exists",
        default_config.is_file(),
        rel(default_config),
    )


def check_entrypoints(checks: list[Check], pyproject: dict) -> list[str]:
    scripts = pyproject.get("project", {}).get("scripts", {})
    add(
        checks,
        "pyproject declares console scripts",
        bool(scripts),
        f"count={len(scripts)}",
    )
    entrypoints = []
    for command, target in scripts.items():
        entrypoints.append(command)
        if ":" not in target:
            add(checks, f"entrypoint {command} has module:function", False, target)
            continue
        module_name, func_name = target.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
            add(checks, f"entrypoint {command} imports", callable(func), target)
        except Exception as exc:
            add(
                checks,
                f"entrypoint {command} imports",
                False,
                f"{target}: {type(exc).__name__}: {exc}",
            )
    return entrypoints


def check_docker(checks: list[Check], pyproject: dict) -> None:
    dockerfile = REPO / "Dockerfile"
    add(checks, "Dockerfile exists", dockerfile.is_file(), rel(dockerfile))
    if not dockerfile.exists():
        return
    text = dockerfile.read_text(encoding="utf-8", errors="replace")
    project_license = normalize_project_license(
        pyproject.get("project", {}).get("license")
    )
    add(
        checks,
        "Docker license label matches pyproject",
        project_license == "AGPL-3.0-only",
        f"license={project_license!r}",
    )
    missing_copy_sources = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY "):
            continue
        parts = stripped.split()
        # Handles simple COPY src dest forms used in this Dockerfile.
        for src in parts[1:-1]:
            if src.startswith("--"):
                continue
            if not (REPO / src).exists():
                missing_copy_sources.append(src)
    add(
        checks,
        "Dockerfile COPY sources exist",
        not missing_copy_sources,
        f"missing={missing_copy_sources}",
    )
    add(
        checks,
        "Dockerfile entrypoint is package script",
        'ENTRYPOINT ["prismatic-engine"]' in text or 'CMD ["prismatic-gateway"' in text,
        "ENTRYPOINT or CMD should use installed console script",
    )


def check_systemd_docs(checks: list[Check]) -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8", errors="replace")
    has_systemctl = "systemctl" in readme
    gated = "Optional operator/systemd deployment" in readme
    if has_systemctl:
        add(
            checks,
            "systemd claims are gated as operator-only",
            gated,
            "README mentions systemctl",
        )
    else:
        warn(
            checks,
            "systemd docs",
            "No systemd docs found; acceptable for first-user path if not promised",
        )


def run(
    cmd: list[str], cwd: Path, timeout: int = 60, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout, env=env
    )


def check_fresh_install(checks: list[Check], entrypoints: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="prismatic-fresh-install-") as tmpdir:
        tmp = Path(tmpdir)
        checkout = tmp / "checkout"
        ignore = shutil.ignore_patterns(
            ".git",
            "__pycache__",
            ".pytest_cache",
            "*.egg-info",
            "dist",
            "build",
            "prismatic_state",
        )
        shutil.copytree(REPO, checkout, ignore=ignore)
        venv = tmp / "venv"
        proc = run([sys.executable, "-m", "venv", str(venv)], cwd=tmp, timeout=120)
        add(
            checks,
            "fresh venv create",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-500:],
        )
        if proc.returncode != 0:
            return
        py = venv / "bin" / "python"
        pip = venv / "bin" / "pip"
        bin_dir = venv / "bin"

        # Build wheel non-editably
        wheel_dir = tmp / "dist"
        wheel_dir.mkdir()
        proc = run(
            [str(py), "-m", "pip", "install", "build", "wheel"],
            cwd=checkout,
            timeout=120,
        )
        proc = run(
            [str(py), "-m", "build", "--wheel", "--outdir", str(wheel_dir)],
            cwd=checkout,
            timeout=240,
        )
        wheels = list(wheel_dir.glob("*.whl"))
        add(
            checks,
            "fresh wheel build",
            proc.returncode == 0 and len(wheels) == 1,
            (proc.stderr or proc.stdout)[-1000:],
        )
        if proc.returncode != 0 or not wheels:
            return
        wheel_path = wheels[0]

        proc = run(
            [str(pip), "install", f"{wheel_path}[all]"], cwd=checkout, timeout=240
        )
        add(
            checks,
            "fresh pip install wheel non-editable",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-1000:],
        )
        if proc.returncode != 0:
            return

        empty_cwd = tmp / "empty_cwd"
        empty_cwd.mkdir()

        # Smoke console scripts from empty CWD
        for command in ["prismatic"] + [ep for ep in entrypoints if ep != "prismatic"]:
            exe = bin_dir / command
            if not exe.exists():
                add(checks, f"fresh console script exists: {command}", False, str(exe))
                continue
            proc = run(
                [str(exe), "--help"],
                cwd=empty_cwd,
                timeout=30,
                env={**os.environ, "HOME": str(tmp / "home")},
            )
            add(
                checks,
                f"fresh console script --help: {command}",
                proc.returncode == 0,
                (proc.stderr or proc.stdout)[-800:],
            )

        # Clean-room installed-wheel verification from empty CWD
        code_catalog = (
            "from prismatic.plugin_architecture import plugin_catalog; "
            "cat = plugin_catalog(); "
            "assert cat['ready_count'] >= 1; "
            "names = [p['name'] for p in cat['plugins']]; "
            "assert 'example-plugin' in names, f'example-plugin missing from {names}'; "
            "assert 'pwp-design-token-plugin' in names, f'pwp-design-token-plugin missing from {names}'"
        )
        proc = run([str(py), "-c", code_catalog], cwd=empty_cwd, timeout=30)
        add(
            checks,
            "clean-room installed-wheel catalog discovery",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-500:],
        )

        code_load_gate = (
            "from prismatic.quality.plugin_load import verify_shipped_plugins_load; "
            "res = verify_shipped_plugins_load(); "
            "assert res.passed, res.reason"
        )
        proc = run([str(py), "-c", code_load_gate], cwd=empty_cwd, timeout=30)
        add(
            checks,
            "clean-room installed-wheel load gate",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-500:],
        )

        code_policy_allow = (
            "from prismatic.plugin_policy import preview_policy; "
            "p = preview_policy('job_request', plugin_name='example-plugin', action='smoke_validate'); "
            "assert p['decision'] == 'allow', p"
        )
        proc = run([str(py), "-c", code_policy_allow], cwd=empty_cwd, timeout=30)
        add(
            checks,
            "clean-room installed-wheel policy preview allow",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-500:],
        )

        code_policy_block = (
            "from prismatic.plugin_policy import preview_policy; "
            "p = preview_policy('job_request', plugin_name='missing-plugin', action='run'); "
            "assert p['decision'] == 'block'; "
            "assert any('unknown plugin' in b for b in p['blockers']), p"
        )
        proc = run([str(py), "-c", code_policy_block], cwd=empty_cwd, timeout=30)
        add(
            checks,
            "clean-room installed-wheel unknown plugin blocks",
            proc.returncode == 0,
            (proc.stderr or proc.stdout)[-500:],
        )

        smoke_env = {
            **os.environ,
            "HOME": str(tmp / "home"),
            "PRISMATIC_EXPECT_INSTALLED_PREFIX": str(venv),
        }
        proc = run(
            [str(py), str(checkout / "scripts/public_launch_smoke.py")],
            cwd=empty_cwd,
            timeout=120,
            env=smoke_env,
        )
        add(
            checks,
            "clean-room installed-wheel public launch smoke",
            proc.returncode == 0 and "PUBLIC_LAUNCH_SMOKE_OK" in proc.stdout,
            (proc.stderr or proc.stdout)[-1000:],
        )

        proc = run(
            [str(py), str(checkout / "scripts/release_smoke.py")],
            cwd=empty_cwd,
            timeout=240,
            env=smoke_env,
        )
        add(
            checks,
            "clean-room installed-wheel release smoke",
            proc.returncode == 0 and "RELEASE_SMOKE_OK" in proc.stdout,
            (proc.stderr or proc.stdout)[-1000:],
        )


def render(checks: list[Check], fresh_install: bool) -> int:
    failed_p0 = [c for c in checks if c.status == FAIL and c.severity == P0]
    failed = [c for c in checks if c.status == FAIL]
    warnings = [c for c in checks if c.status == WARN]
    verdict = "PUBLISHABLE" if not failed_p0 else "NOT_PUBLISHABLE"
    print("=" * 72)
    print("Prismatic Engine Distribution Readiness Smoke")
    print("=" * 72)
    print(f"Repo: {REPO}")
    print(f"Fresh install check: {'enabled' if fresh_install else 'skipped'}")
    print(f"Verdict: {verdict}")
    print(
        f"Checks: {len(checks)} total, {len(failed)} failed, {len(warnings)} warnings, {len(failed_p0)} P0 failures"
    )
    print()
    for c in checks:
        marker = {PASS: "✅", FAIL: "❌", WARN: "⚠️"}[c.status]
        print(f"{marker} [{c.severity}] {c.name}: {c.status}")
        if c.detail:
            print(textwrap.indent(c.detail.strip(), "    "))
    print()
    print(
        "JSON_SUMMARY="
        + json.dumps(
            {
                "verdict": verdict,
                "fresh_install": fresh_install,
                "total": len(checks),
                "failed": len(failed),
                "warnings": len(warnings),
                "failed_p0": len(failed_p0),
                "failed_checks": [c.name for c in failed],
            },
            sort_keys=True,
        )
    )
    return 0 if verdict == "PUBLISHABLE" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="First-user distribution readiness gate"
    )
    parser.add_argument(
        "--fresh-install",
        action="store_true",
        help="copy repo to temp dir, install into venv, and run console-script --help checks",
    )
    args = parser.parse_args()
    checks: list[Check] = []
    pyproject = load_pyproject()
    check_metadata(checks, pyproject)
    check_readme(checks)
    check_package_data(checks, pyproject)
    entrypoints = check_entrypoints(checks, pyproject)
    check_docker(checks, pyproject)
    check_systemd_docs(checks)
    if args.fresh_install:
        check_fresh_install(checks, entrypoints)
    else:
        warn(
            checks,
            "fresh install path",
            "Run with --fresh-install before release/PyPI/GHCR publication.",
            P1,
        )
    return render(checks, args.fresh_install)


if __name__ == "__main__":
    raise SystemExit(main())
