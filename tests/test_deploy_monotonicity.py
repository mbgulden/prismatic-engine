"""Tests for the deploy monotonicity guard (fail-closed on out-of-order hooks).

Observed 2026-09-28: a late-firing post-merge hook for #568 redeployed an
older SHA over the live release, moving the release symlink backward. The
guard in Receiver.process_deploy refuses such downgrades loudly instead of
silently redeploying stale code.
"""

import os
import subprocess

import pytest

os.environ["PRISMATIC_ALLOW_DEFAULT_HMAC"] = "1"

from pe.deploy.gateway_redeploy import GatewayDeployResult
from pe.deploy.health import PostDeployHealthChecker
from pe.deploy.integrate import AtomicDeployRunner
from pe.deploy.linear_transition import LinearDeployTransitioner
from pe.deploy.manifest import DeployManifestStore
from pe.deploy.receiver import DeployReceiverPipeline


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _sha(repo, rev="HEAD"):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def two_commit_env(tmp_path):
    """Source repo with two commits: old_sha (ancestor) -> new_sha (head)."""
    versions_dir = tmp_path / "versions"
    releases_dir = tmp_path / "releases"
    versions_dir.mkdir()
    releases_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", str(repo), cwd=str(tmp_path))
    _git("config", "user.email", "test@example.com", cwd=str(repo))
    _git("config", "user.name", "test", cwd=str(repo))
    (repo / "prismatic").mkdir()
    (repo / "prismatic" / "__init__.py").write_text("# v1", encoding="utf-8")
    _git("add", ".", cwd=str(repo))
    _git("commit", "-qm", "first", cwd=str(repo))
    old_sha = _sha(repo)
    (repo / "prismatic" / "__init__.py").write_text("# v2", encoding="utf-8")
    _git("commit", "-qam", "second", cwd=str(repo))
    new_sha = _sha(repo)
    assert old_sha != new_sha
    return {
        "versions_dir": versions_dir,
        "releases_dir": releases_dir,
        "symlink_path": releases_dir / "prismatic-engine",
        "db_file": tmp_path / "deploy_records.json",
        "source_repo": repo,
        "old_sha": old_sha,
        "new_sha": new_sha,
    }


class _StubGatewayRedeployer:
    def redeploy(self, pr_sha="", repo=None, dry_run=False, repo_config=None):
        return GatewayDeployResult(
            success=True, skipped=True, reason="test-stub", pr_sha=pr_sha
        )


def _make_pipeline(env):
    runner = AtomicDeployRunner(
        versions_dir=env["versions_dir"],
        release_symlink=env["symlink_path"],
    )
    return DeployReceiverPipeline(
        source_repo=env["source_repo"],
        deploy_runner=runner,
        health_checker=PostDeployHealthChecker(),
        transitioner=LinearDeployTransitioner(dry_run=True),
        store=DeployManifestStore(db_path=env["db_file"]),
        gateway_redeployer=_StubGatewayRedeployer(),
    )


def _payload(sha, **extra):
    p = {
        "pr_sha": sha,
        "pr_number": 1,
        "pr_title": "test",
        "deployer": "github-action",
    }
    p.update(extra)
    return p


class TestCurrentDeployedSha:
    def test_parses_sha_from_release_symlink(self, two_commit_env):
        env = two_commit_env
        target = env["versions_dir"] / f"prismatic-engine-{env['new_sha']}"
        target.mkdir()
        env["symlink_path"].symlink_to(target)
        runner = AtomicDeployRunner(
            versions_dir=env["versions_dir"],
            release_symlink=env["symlink_path"],
        )
        assert (
            DeployReceiverPipeline._current_deployed_sha(runner) == env["new_sha"]
        )

    def test_missing_symlink_returns_empty(self, two_commit_env):
        env = two_commit_env
        runner = AtomicDeployRunner(
            versions_dir=env["versions_dir"],
            release_symlink=env["symlink_path"],
        )
        assert DeployReceiverPipeline._current_deployed_sha(runner) == ""


class TestIsStrictAncestor:
    def test_older_is_strict_ancestor_of_newer(self, two_commit_env):
        env = two_commit_env
        assert (
            DeployReceiverPipeline._is_strict_ancestor(
                env["source_repo"], env["old_sha"], env["new_sha"]
            )
            is True
        )

    def test_newer_is_not_ancestor_of_older(self, two_commit_env):
        env = two_commit_env
        assert (
            DeployReceiverPipeline._is_strict_ancestor(
                env["source_repo"], env["new_sha"], env["old_sha"]
            )
            is False
        )

    def test_same_sha_is_not_strict_ancestor(self, two_commit_env):
        env = two_commit_env
        assert (
            DeployReceiverPipeline._is_strict_ancestor(
                env["source_repo"], env["new_sha"], env["new_sha"]
            )
            is False
        )

    def test_unknown_sha_returns_none(self, two_commit_env):
        env = two_commit_env
        assert (
            DeployReceiverPipeline._is_strict_ancestor(
                env["source_repo"], "0" * 40, env["new_sha"]
            )
            is None
        )


class TestMonotonicityGuard:
    def _deploy_live(self, env, live_sha):
        """Simulate a live release at live_sha (symlink only, no full deploy)."""
        target = env["versions_dir"] / f"prismatic-engine-{live_sha}"
        target.mkdir(exist_ok=True)
        if env["symlink_path"].is_symlink():
            env["symlink_path"].unlink()
        env["symlink_path"].symlink_to(target)

    def test_downgrade_refused(self, two_commit_env):
        env = two_commit_env
        self._deploy_live(env, env["new_sha"])
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload(env["old_sha"]))
        assert record.success is False
        assert "out-of-order" in record.failure_reason
        assert env["new_sha"][:8] in record.failure_reason
        assert record.deploy_id.endswith("-stale")
        # Release symlink untouched: still the newer release.
        assert env["new_sha"][:8] in str(env["symlink_path"].resolve())

    def test_same_sha_allowed(self, two_commit_env):
        env = two_commit_env
        self._deploy_live(env, env["new_sha"])
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload(env["new_sha"]))
        assert record.success is True

    def test_newer_sha_allowed(self, two_commit_env):
        env = two_commit_env
        self._deploy_live(env, env["old_sha"])
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload(env["new_sha"]))
        assert record.success is True
        assert env["new_sha"][:8] in str(env["symlink_path"].resolve())

    def test_first_deploy_allowed(self, two_commit_env):
        env = two_commit_env
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload(env["new_sha"]))
        assert record.success is True

    def test_force_overrides_downgrade(self, two_commit_env):
        env = two_commit_env
        self._deploy_live(env, env["new_sha"])
        pipeline = _make_pipeline(env)
        record = pipeline.process_deploy(_payload(env["old_sha"], force=True))
        assert record.success is True
        assert env["old_sha"][:8] in str(env["symlink_path"].resolve())

    def test_unknown_order_fails_open(self, two_commit_env, monkeypatch):
        env = two_commit_env
        self._deploy_live(env, env["new_sha"])
        pipeline = _make_pipeline(env)
        monkeypatch.setattr(
            DeployReceiverPipeline,
            "_is_strict_ancestor",
            staticmethod(lambda *a: None),
        )
        record = pipeline.process_deploy(_payload(env["old_sha"]))
        assert record.success is True
