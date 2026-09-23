"""Regression tests: importing the deploy receiver must have no side effects.

2026-09-22: PR #528 made DeployReceiverPipeline.__init__ fail-fast when
PRISMATIC_DEPLOY_SOURCE_REPO is unset. pe/deploy/receiver.py built the FastAPI
app at module level, so `import pe.deploy.receiver` raised RuntimeError -- and
the gateway imports that module at startup (via prismatic.deploy.routes), which
crash-looped the gateway on every deploy of #528 and forced a rollback.

The fail-fast must still fire when the receiver actually STARTS (get_app() /
pe.deploy.receiver:app), just not at import. These tests run in subprocesses
with the env var removed so they are hermetic regardless of test ordering.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_VAR = "PRISMATIC_DEPLOY_SOURCE_REPO"


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(ENV_VAR, None)
    return env


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_import_receiver_has_no_side_effects():
    """Importing pe.deploy.receiver without the env var must not raise."""
    proc = _run("import pe.deploy.receiver; print('import ok')")
    assert proc.returncode == 0, f"import crashed:\n{proc.stderr}"
    assert "import ok" in proc.stdout
    assert "RuntimeError" not in proc.stderr


def test_import_pipeline_class_has_no_side_effects():
    """The gateway's import (from-import of the pipeline class) must not raise."""
    proc = _run(
        "from pe.deploy.receiver import DeployReceiverPipeline;"
        " print('pipeline import ok')"
    )
    assert proc.returncode == 0, f"pipeline import crashed:\n{proc.stderr}"
    assert "pipeline import ok" in proc.stdout


def test_gateway_server_import_chain_without_env():
    """Exact production crash chain: importing the gateway server sans env var."""
    proc = _run("import prismatic.gateway.server; print('gateway import ok')")
    if proc.returncode != 0 and "ModuleNotFoundError" in proc.stderr:
        pytest.skip("gateway dependencies not installed in this environment")
    assert proc.returncode == 0, f"gateway import crashed:\n{proc.stderr}"
    assert "gateway import ok" in proc.stdout
    assert ENV_VAR not in proc.stderr


def test_get_app_fail_fast_without_env():
    """The fail-fast must still fire when the receiver actually starts."""
    proc = _run(
        "import pe.deploy.receiver as r\n"
        "try:\n"
        "    r.get_app()\n"
        "except RuntimeError as e:\n"
        f"    assert '{ENV_VAR}' in str(e), str(e)\n"
        "    print('fail-fast ok')\n"
        "else:\n"
        "    raise SystemExit('get_app() did not fail-fast')\n"
    )
    assert proc.returncode == 0, f"fail-fast broken:\n{proc.stderr}"
    assert "fail-fast ok" in proc.stdout


def test_app_attribute_fail_fast_without_env():
    """Uvicorn resolves 'pe.deploy.receiver:app' via attribute access: same gate."""
    proc = _run(
        "import pe.deploy.receiver as r\n"
        "try:\n"
        "    r.app\n"
        "except RuntimeError as e:\n"
        f"    assert '{ENV_VAR}' in str(e), str(e)\n"
        "    print('attr fail-fast ok')\n"
        "else:\n"
        "    raise SystemExit('r.app did not fail-fast')\n"
    )
    assert proc.returncode == 0, f"attr fail-fast broken:\n{proc.stderr}"
    assert "attr fail-fast ok" in proc.stdout


def test_deploy_router_builds_without_env():
    """The gateway builds the deploy router at startup; only /trigger needs env."""
    proc = _run(
        "from prismatic.deploy.routes import create_deploy_router, deploy_router\n"
        "assert create_deploy_router() is not None\n"
        "assert deploy_router is not None\n"
        "print('router ok')\n"
    )
    if proc.returncode != 0 and "ModuleNotFoundError" in proc.stderr:
        pytest.skip("fastapi not installed in this environment")
    assert proc.returncode == 0, f"router build crashed:\n{proc.stderr}"
    assert "router ok" in proc.stdout
