"""Unit tests for the real atomic gateway redeploy (pe.deploy.gateway_redeploy).

Covers: success path flips + restarts, rollback on health failure (proves the
gateway is restored, never left half-flipped), SHA refusal, supersede skip,
lock contention, and dry-run. Real git is used for the SHA-verification
tests; everything else is faked (no systemd, no network, no pip).
"""

import fcntl
import os
import subprocess
import time
from pathlib import Path

import pytest

from pe.deploy.gateway_redeploy import (
    DeployRefused,
    GatewayDeployResult,
    GatewayRedeployer,
)


class FakeRun:
    """Scripted subprocess double. Records every call."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "is-active" in argv:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def restarts(self):
        return [c for c in self.calls if c[-2:] == ["restart", "prismatic-gateway.service"]]


@pytest.fixture
def gw_home(tmp_path):
    """Fake ~/.prismatic with a live release + venv wired via symlinks."""
    home = tmp_path / "home"
    pris = home / ".prismatic"
    for d in ("releases", "venvs", "run", "wheel_cache"):
        (pris / d).mkdir(parents=True)
    live_rel = pris / "releases" / ("prismatic-engine-" + "a" * 40)
    live_rel.mkdir()
    (live_rel / "prismatic").mkdir()
    live_venv = pris / "venvs" / ("prismatic-engine-" + "a" * 40)
    (live_venv / "bin").mkdir(parents=True)
    (pris / "current").symlink_to(live_rel)
    (pris / "venv_current").symlink_to(live_venv)
    return home


class ScriptedRedeployer(GatewayRedeployer):
    """Redeployer with git/wheel/venv/health steps scripted for orchestration tests."""

    def __init__(self, *args, health_ok=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.health_ok = health_ok

    def _verify_sha(self, repo, pr_sha):
        return "b" * 40, "c" * 40

    def _live_sha(self):
        return None  # never superseded in these tests

    def _build_wheel(self, worktree, dist_dir):
        w = dist_dir / "prismatic_engine-0.2.0-py3-none-any.whl"
        w.write_text("fake-wheel")
        return w

    def _create_venv(self, venv_dir, wheel):
        venv_dir.mkdir(parents=True, exist_ok=True)
        (venv_dir / "bin").mkdir(exist_ok=True)
        (venv_dir / "bin" / "python").write_text("#!/bin/sh\n")

    def _stage_release(self, worktree, version_dir):
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "prismatic").mkdir(exist_ok=True)

    def _health_check(self, venv_dir):
        if self.health_ok:
            return {"passed": True, "checks": {"service_active": True}, "details": {}}
        return {
            "passed": False,
            "checks": {"service_active": False},
            "details": {"service_active_error": "simulated failure"},
        }


def make_deployer(home, **kw):
    run = FakeRun()
    kw.setdefault("lock_timeout_s", 30)
    dep = ScriptedRedeployer(home=home, run=run, **kw)
    return dep, run


# ---------------------------------------------------------------- success

def test_success_path_flips_both_symlinks_and_restarts(gw_home):
    dep, run = make_deployer(gw_home)
    res = dep.redeploy("b" * 40, repo=gw_home)

    assert res.success and not res.skipped, res.reason
    pris = gw_home / ".prismatic"
    assert os.readlink(pris / "current").endswith("b" * 40)
    assert os.readlink(pris / "venv_current").endswith("b" * 40)
    assert len(run.restarts()) == 1
    assert res.previous_version_dir.endswith("a" * 40)
    assert res.previous_venv_dir.endswith("a" * 40)
    assert not res.rolled_back
    state = (pris / "run" / "last-gateway-deploy.json").read_text()
    assert ("b" * 40) in state


# ---------------------------------------------------------------- rollback

def test_rollback_restores_previous_release_on_health_failure(gw_home):
    dep, run = make_deployer(gw_home, health_ok=False)
    res = dep.redeploy("b" * 40, repo=gw_home)

    assert not res.success, "deploy must fail when health fails"
    assert res.rolled_back, "rollback must be reported"
    pris = gw_home / ".prismatic"
    # both symlinks point back at the ORIGINAL release -- never half-flipped
    assert os.readlink(pris / "current").endswith("a" * 40)
    assert os.readlink(pris / "venv_current").endswith("a" * 40)
    # one restart for the deploy attempt, one for the rollback
    assert len(run.restarts()) == 2
    assert "health check failed" in res.reason


def test_failed_build_leaves_live_release_untouched(gw_home):
    class BuildFails(ScriptedRedeployer):
        def _build_wheel(self, worktree, dist_dir):
            raise Exception("simulated wheel build failure")

    run = FakeRun()
    dep = BuildFails(home=gw_home, run=run, lock_timeout_s=30)
    res = dep.redeploy("b" * 40, repo=gw_home)

    assert not res.success
    assert res.rolled_back  # nothing flipped -> trivially true, no restart needed
    pris = gw_home / ".prismatic"
    assert os.readlink(pris / "current").endswith("a" * 40)
    assert run.restarts() == [], "service must not restart when nothing flipped"


# ---------------------------------------------------------------- SHA safety

def _git_repo(path: Path) -> dict[str, str]:
    def g(*a):
        return subprocess.run(
            ["git", *a], cwd=path, capture_output=True, text=True, check=True
        )
    (path).mkdir(parents=True, exist_ok=True)
    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (path / "f.txt").write_text("one")
    g("add", ".")
    g("commit", "-qm", "first")
    sha1 = g("rev-parse", "HEAD").stdout.strip()
    (path / "f.txt").write_text("two")
    g("commit", "-qam", "second")
    sha2 = g("rev-parse", "HEAD").stdout.strip()
    g("branch", "-M", "main")
    g("remote", "add", "origin", str(path))
    g("fetch", "-q", "origin", "main")
    g("checkout", "-qb", "other", sha1)
    (path / "g.txt").write_text("other-branch")
    g("add", ".")
    g("commit", "-qm", "other")
    sha_other = g("rev-parse", "HEAD").stdout.strip()
    g("checkout", "-q", "main")
    return {"sha1": sha1, "sha2": sha2, "sha_other": sha_other}


def test_verify_sha_accepts_ancestor_of_main(tmp_path):
    repo = tmp_path / "repo"
    shas = _git_repo(repo)
    dep = GatewayRedeployer(home=tmp_path / "home", lock_timeout_s=5)
    full, main = dep._verify_sha(repo, shas["sha1"])
    assert full == shas["sha1"]
    assert main == shas["sha2"]


def test_verify_sha_refuses_non_commit_and_non_ancestor(tmp_path):
    repo = tmp_path / "repo"
    shas = _git_repo(repo)
    dep = GatewayRedeployer(home=tmp_path / "home", lock_timeout_s=5)
    with pytest.raises(DeployRefused):
        dep._verify_sha(repo, "deadbee")
    with pytest.raises(DeployRefused):
        dep._verify_sha(repo, "not-a-sha!!!")
    with pytest.raises(DeployRefused):
        dep._verify_sha(repo, shas["sha_other"])  # real commit, not on main
    with pytest.raises(DeployRefused):
        dep._verify_sha(repo, "")


# ---------------------------------------------------------------- supersede / lock / dry-run

def test_superseded_merge_is_skipped_not_redeployed(gw_home):
    """Live release already contains the requested sha -> skip quietly."""
    run = FakeRun()

    class Superseded(ScriptedRedeployer):
        def _live_sha(self):
            return "c" * 40

        def _is_ancestor(self, repo, sha, descendant):
            return True  # requested sha is contained in live

    dep = Superseded(home=gw_home, run=run, lock_timeout_s=30)
    res = dep.redeploy("b" * 40, repo=gw_home)
    assert res.success and res.skipped
    assert "superseded" in res.reason
    assert run.restarts() == []


def test_lock_contention_times_out_loudly(gw_home):
    pris = gw_home / ".prismatic"
    (pris / "run").mkdir(parents=True, exist_ok=True)
    lock_path = pris / "run" / "gateway-deploy.lock"
    fh = open(lock_path, "w")
    fcntl.flock(fh, fcntl.LOCK_EX)  # hold the lock like a stuck deploy
    try:
        dep, run = make_deployer(gw_home, lock_timeout_s=1)
        res = dep.redeploy("b" * 40, repo=gw_home)
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
    assert res.skipped and not res.success
    assert "deploy lock" in res.reason
    assert run.restarts() == []


def test_dry_run_does_nothing(gw_home):
    dep, run = make_deployer(gw_home)
    res = dep.redeploy("b" * 40, repo=gw_home, dry_run=True)
    assert res.success and res.skipped
    assert run.calls == []
    pris = gw_home / ".prismatic"
    assert os.readlink(pris / "current").endswith("a" * 40)


def test_result_serializes():
    res = GatewayDeployResult(success=True, pr_sha="ab12", reason="ok",
                              health={"passed": True})
    d = res.to_dict()
    assert d["success"] is True and d["health"]["passed"] is True

# auto-deploy e2e verification (2026-09-20): harmless marker comment; exercises the full merge-to-redeploy loop.
