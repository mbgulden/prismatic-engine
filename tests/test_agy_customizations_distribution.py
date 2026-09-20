from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _base_interpreter_has_dependencies() -> bool:
    """Check the child venv can see project dependencies via system site.

    The test installs the wheel with ``--no-deps`` into a fresh venv created
    with ``--system-site-packages``, so dependencies must be importable from
    the *base* interpreter's site-packages. When pytest itself runs inside a
    virtualenv, the child venv inherits the base interpreter's site-packages
    — not the virtualenv's — and the import fails. CI runs on a non-venv
    interpreter whose site-packages carry the dependencies.
    """
    if sys.prefix == sys.base_prefix:
        return True
    base_python = Path(sys.base_prefix) / "bin" / "python3"
    if not base_python.exists():
        return False
    proc = subprocess.run(
        [str(base_python), "-c", "import swarmlock"],
        capture_output=True,
    )
    return proc.returncode == 0


requires_base_site_dependencies = pytest.mark.skipif(
    not _base_interpreter_has_dependencies(),
    reason=(
        "needs project dependencies importable from the base interpreter's "
        "site-packages (true on CI's non-venv interpreter; this sandbox runs "
        "pytest inside a virtualenv whose base lacks them)"
    ),
)


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@requires_base_site_dependencies
def test_noneditable_wheel_manages_agy_bundle_from_empty_cwd(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=REPO_ROOT,
        env=dict(os.environ),
    )
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1

    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv / "bin" / "python"
    pip = venv / "bin" / "pip"
    subprocess.run(
        [str(pip), "install", "--no-deps", str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
    )

    empty = tmp_path / "empty"
    target = tmp_path / "target"
    empty.mkdir()
    target.mkdir()
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PATH"] = f"{venv / 'bin'}:{env.get('PATH', '')}"

    imported = _run(
        [str(python), "-c", "import prismatic; print(prismatic.__file__)"],
        cwd=empty,
        env=env,
    )
    assert str(venv) in imported.stdout
    assert str(REPO_ROOT) not in imported.stdout

    executable = venv / "bin" / "prismatic"
    validated = _run(
        [str(executable), "agy", "customizations", "validate"],
        cwd=empty,
        env=env,
    )
    assert json.loads(validated.stdout)["ok"] is True

    preview = _run(
        [
            str(executable),
            "agy",
            "customizations",
            "install",
            "--workspace",
            str(target),
            "--dry-run",
        ],
        cwd=empty,
        env=env,
    )
    assert json.loads(preview.stdout)["would_change"] is True
    assert not (target / ".agents").exists()

    installed = _run(
        [
            str(executable),
            "agy",
            "customizations",
            "install",
            "--workspace",
            str(target),
        ],
        cwd=empty,
        env=env,
    )
    assert json.loads(installed.stdout)["changed"] is True

    status = _run(
        [
            str(executable),
            "agy",
            "customizations",
            "status",
            "--workspace",
            str(target),
        ],
        cwd=empty,
        env=env,
    )
    assert json.loads(status.stdout)["ok"] is True

    repeated = _run(
        [
            str(executable),
            "agy",
            "customizations",
            "install",
            "--workspace",
            str(target),
        ],
        cwd=empty,
        env=env,
    )
    assert json.loads(repeated.stdout)["changed"] is False

    removed = _run(
        [
            str(executable),
            "agy",
            "customizations",
            "uninstall",
            "--workspace",
            str(target),
        ],
        cwd=empty,
        env=env,
    )
    assert json.loads(removed.stdout)["ok"] is True
    assert not (target / ".agents" / "prismatic-managed.json").exists()
