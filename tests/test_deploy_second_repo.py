"""Tests for second-repo (pilot) deployment isolation.

Covers the per-repo deployment parameterization added for the second-repo
pilot: default behavior for prismatic-engine is byte-for-byte unchanged,
while a routed repo gets isolated live links, its own release prefix, port,
extras, and smoke import — so its deploys can never flip the production
gateway's symlinks.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pe.deploy import onboard
from pe.deploy.config import (
    DEFAULT_GATEWAY_EXTRAS,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_RELEASE_PREFIX,
    _config_for_name,
)
from pe.deploy.gateway_redeploy import DEFAULT_EXTRAS, GatewayRedeployer
from pe.deploy.health import PostDeployHealthChecker
from pe.deploy.integrate import AtomicDeployRunner


def test_default_repo_config_unchanged():
    cfg = _config_for_name("mbgulden/prismatic-engine")
    assert cfg.release_prefix == DEFAULT_RELEASE_PREFIX == "prismatic-engine"
    assert cfg.port == DEFAULT_GATEWAY_PORT == 9000
    assert cfg.extras == DEFAULT_GATEWAY_EXTRAS
    assert cfg.smoke_import == "prismatic.gateway.server"


def test_pilot_repo_config_overrides():
    cfg = _config_for_name(
        "mbgulden/prismatic-deploy-pilot",
        {
            "release_prefix": "prismatic-deploy-pilot",
            "target_service": "prismatic-deploy-pilot.service",
            "port": 9461,
            "extras": "",
            "smoke_import": "pilot_service.server",
        },
    )
    assert cfg.release_prefix == "prismatic-deploy-pilot"
    assert cfg.target_service == "prismatic-deploy-pilot.service"
    assert cfg.port == 9461
    assert cfg.extras == ""
    assert cfg.smoke_import == "pilot_service.server"


def test_port_validation():
    with pytest.raises(ValueError, match="invalid port"):
        _config_for_name("octo/repo", {"port": 0})
    with pytest.raises(ValueError, match="invalid port"):
        _config_for_name("octo/repo", {"port": 99999})
    with pytest.raises(ValueError, match="invalid port"):
        _config_for_name("octo/repo", {"port": "not-a-number"})
    assert _config_for_name("octo/repo", {"port": "9461"}).port == 9461


def test_redeployer_default_links_unchanged(tmp_path):
    rd = GatewayRedeployer(home=tmp_path)
    assert rd.release_prefix == DEFAULT_RELEASE_PREFIX
    assert rd.current_link == rd.prismatic / "current"
    assert rd.venv_link == rd.prismatic / "venv_current"
    assert rd.state_name == "last-gateway-deploy.json"
    assert rd.port == DEFAULT_GATEWAY_PORT
    assert rd.extras == DEFAULT_EXTRAS
    assert rd.smoke_import == "prismatic.gateway.server"


def test_redeployer_pilot_links_isolated(tmp_path):
    rd = GatewayRedeployer(
        home=tmp_path,
        release_prefix="prismatic-deploy-pilot",
        service="prismatic-deploy-pilot.service",
        port=9461,
        extras="",
        smoke_import="pilot_service.server",
    )
    assert rd.current_link == rd.prismatic / "current-prismatic-deploy-pilot"
    assert rd.venv_link == rd.prismatic / "venv_current-prismatic-deploy-pilot"
    assert rd.state_name == "last-deploy-prismatic-deploy-pilot.json"
    assert rd.port == 9461
    assert rd.extras == ""
    assert rd.smoke_import == "pilot_service.server"
    # The production links are untouched by construction.
    assert not (rd.prismatic / "current").exists()
    assert not (rd.prismatic / "venv_current").exists()


def test_live_sha_is_prefix_aware(tmp_path):
    rd = GatewayRedeployer(home=tmp_path, release_prefix="prismatic-deploy-pilot")
    rd.prismatic.mkdir(parents=True, exist_ok=True)
    target = rd.prismatic / "releases" / "prismatic-deploy-pilot-abc1234"
    target.mkdir(parents=True, exist_ok=True)
    os.symlink(target, rd.current_link)
    assert rd._live_sha() == "abc1234"

    # A default-prefix redeployer does not claim the pilot's link.
    default_rd = GatewayRedeployer(home=tmp_path)
    assert default_rd._live_sha() is None


def _make_bare_repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.t"], cwd=work, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=work, check=True
    )
    (work / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(work), str(bare)], check=True
    )
    return bare


def test_integrate_accepts_bare_mirror_source(tmp_path):
    bare = _make_bare_repo(tmp_path)
    runner = AtomicDeployRunner()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=bare, capture_output=True, text=True
    ).stdout.strip()
    src, full_sha = runner._validate_source_repo(bare, sha)
    assert src == bare
    assert full_sha == sha


def test_integrate_still_rejects_nonprismatic_worktree(tmp_path):
    bare = _make_bare_repo(tmp_path)
    work = tmp_path / "worktree"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    runner = AtomicDeployRunner()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True
    ).stdout.strip()
    with pytest.raises(ValueError, match="no prismatic/ package directory"):
        runner._validate_source_repo(work, sha)


def test_health_checker_accepts_pyproject_staged_dir(tmp_path):
    version_dir = tmp_path / "version"
    version_dir.mkdir()
    (version_dir / "pyproject.toml").write_text("[project]\nname='x'\n")
    symlink = tmp_path / "link"
    try:
        os.symlink(version_dir, symlink)
    except OSError:
        symlink = version_dir  # pragma: no cover
    res = PostDeployHealthChecker().check(
        version_dir=version_dir, release_symlink=symlink
    )
    assert res["checks"]["version_dir_valid"] is True


def test_default_release_prefix_derivation():
    assert (
        onboard._default_release_prefix("mbgulden/prismatic-deploy-pilot")
        == "mbgulden-prismatic-deploy-pilot"
    )
    assert onboard._default_release_prefix("octo/repo") == "octo-repo"


def test_add_repo_always_writes_release_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_FILE", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    reg = tmp_path / ".prismatic" / "deploy-repos.json"

    class CP:
        returncode = 0
        stdout = ""
        stderr = ""

    def ok_run(cmd, timeout=60):
        return CP()

    result = onboard.add_repo(
        onboard.AddRepoOptions(
            "octo/repo",
            no_mirror=True,
            registry_file=str(reg),
            port=9461,
            extras="",
            smoke_import="octo_server",
        ),
        run=ok_run,
    )
    assert result.ok
    import json

    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["octo/repo"]
    assert entry["release_prefix"] == "octo-repo"
    assert entry["port"] == 9461
    assert entry["extras"] == ""
    assert entry["smoke_import"] == "octo_server"
    cfg = _config_for_name("octo/repo", entry)
    assert cfg.port == 9461 and cfg.extras == "" and cfg.smoke_import == "octo_server"


def test_receiver_redeployer_uses_repo_params(tmp_path):
    # GatewayRedeployer built the way receiver._redeployer_for builds it.
    cfg = _config_for_name(
        "mbgulden/prismatic-deploy-pilot",
        {
            "release_prefix": "prismatic-deploy-pilot",
            "target_service": "prismatic-deploy-pilot.service",
            "port": 9461,
            "extras": "",
            "smoke_import": "pilot_service.server",
        },
    )
    rd = GatewayRedeployer(
        service=cfg.target_service,
        health_endpoints=cfg.health_endpoints,
        release_prefix=cfg.release_prefix,
        port=cfg.port,
        extras=cfg.extras,
        smoke_import=cfg.smoke_import,
    )
    assert rd.service == "prismatic-deploy-pilot.service"
    assert rd.port == 9461
    assert rd.extras == ""
    assert rd.smoke_import == "pilot_service.server"
    assert rd.current_link.name == "current-prismatic-deploy-pilot"
