"""Unit and integration tests for Workstream B: Linear -> PR -> Prod Deploy Hook (WB-10).
"""

import hashlib
import hmac
import subprocess

import os
import pytest
os.environ["PRISMATIC_ALLOW_DEFAULT_HMAC"] = "1"

from pe.deploy.health import PostDeployHealthChecker
from pe.deploy.integrate import AtomicDeployRunner
from pe.deploy.linear_transition import LinearDeployTransitioner
from pe.deploy.gateway_redeploy import GatewayDeployResult
from pe.deploy.manifest import DeployManifestStore, DeployRecord
from pe.deploy.receiver import DeployReceiverPipeline, verify_hmac_signature


@pytest.fixture
def tmp_deploy_env(tmp_path):
    versions_dir = tmp_path / "versions"
    releases_dir = tmp_path / "releases"
    db_file = tmp_path / "deploy_records.json"
    source_repo = tmp_path / "repo"

    versions_dir.mkdir()
    releases_dir.mkdir()
    source_repo.mkdir()

    # Create dummy source files
    (source_repo / "prismatic").mkdir()
    (source_repo / "prismatic" / "__init__.py").write_text("# main", encoding="utf-8")
    (source_repo / "docs").mkdir()
    (source_repo / "docs" / "readme.md").write_text("# Readme", encoding="utf-8")

    # Real git repo: deploys build a pristine worktree of an exact commit, so
    # the source must be a git checkout containing prismatic/ (fail-fast
    # validation, Sep 22 2026).
    _git("init", "-q", "-b", "main", str(source_repo), cwd=str(tmp_path))
    _git("config", "user.email", "test@example.com", cwd=str(source_repo))
    _git("config", "user.name", "test", cwd=str(source_repo))
    _git("add", ".", cwd=str(source_repo))
    _git("commit", "-qm", "seed", cwd=str(source_repo))
    head_sha = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    symlink_path = releases_dir / "prismatic-engine"

    return {
        "versions_dir": versions_dir,
        "releases_dir": releases_dir,
        "symlink_path": symlink_path,
        "db_file": db_file,
        "source_repo": source_repo,
        "head_sha": head_sha,
    }


class TestHMACVerification:
    def test_valid_signature(self):
        secret = "test-secret"
        body = b'{"pr_sha":"12345"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert verify_hmac_signature(body, f"sha256={sig}", secret=secret) is True

    def test_invalid_signature(self):
        secret = "test-secret"
        body = b'{"pr_sha":"12345"}'

        assert verify_hmac_signature(body, "sha256=invalid", secret=secret) is False
        assert verify_hmac_signature(body, None, secret=secret) is False


class TestLinearDeployTransitioner:
    def test_extract_issue_ids(self):
        text = "Fixes GRO-4188 and GRO-4189. Closes gro-4190."
        issues = LinearDeployTransitioner.extract_issue_ids(text)
        assert len(issues) == 3
        assert "GRO-4188" in issues
        assert "GRO-4189" in issues
        assert "GRO-4190" in issues

    def test_transition_issues_dry_run(self):
        trans = LinearDeployTransitioner(dry_run=True)
        receipts = trans.transition_issues_for_deploy(
            deploy_id="dep-1",
            pr_sha="a" * 40,
            pr_title="Merge GRO-4188 fix",
        )
        assert len(receipts) == 1
        assert receipts[0].issue_id == "GRO-4188"
        assert receipts[0].success is True

    def test_durable_linear_transitions_store(self, tmp_path):
        from pe.deploy.linear_transition import LinearTransitionsStore
        db_file = tmp_path / "linear_transitions.json"
        store1 = LinearTransitionsStore(db_path=db_file)
        trans1 = LinearDeployTransitioner(dry_run=True, store=store1)

        receipts1 = trans1.transition_issues_for_deploy(
            deploy_id="dep-1",
            pr_sha="a" * 40,
            pr_title="Merge GRO-4188 fix",
        )
        assert receipts1[0].linear_response.get("status") == "dry_run"

        # Re-instantiate store from same file (simulating server restart)
        store2 = LinearTransitionsStore(db_path=db_file)
        trans2 = LinearDeployTransitioner(dry_run=True, store=store2)

        receipts2 = trans2.transition_issues_for_deploy(
            deploy_id="dep-2",
            pr_sha="a" * 40,
            pr_title="Merge GRO-4188 fix",
        )
        # Should be deduplicated via durable store!
        assert receipts2[0].linear_response.get("status") == "idempotent_dedupe"


class TestAtomicDeployRunner:
    def test_atomic_deploy(self, tmp_deploy_env):
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
            dry_run=False,
        )

        success, ver_dir, err = runner.deploy(
            source_repo=tmp_deploy_env["source_repo"],
            pr_sha=tmp_deploy_env["head_sha"],
        )

        assert success is True
        assert ver_dir.exists()
        assert (ver_dir / "prismatic" / "__init__.py").exists()
        assert tmp_deploy_env["symlink_path"].is_symlink() or tmp_deploy_env["symlink_path"].exists()


class TestDeployManifestStore:
    def test_record_and_list_deploys(self, tmp_deploy_env):
        store = DeployManifestStore(db_path=tmp_deploy_env["db_file"])
        record = DeployRecord(
            pr_sha="b" * 40,
            pr_title="Deploy Test",
            success=True,
        )
        store.record_deploy(record)

        deploys = store.list_deploys()
        assert len(deploys) == 1
        assert deploys[0].pr_sha == "b" * 40

        latest = store.get_latest()
        assert latest is not None
        assert latest.pr_sha == "b" * 40


class TestDeployReceiverPipeline:
    def test_process_deploy_pipeline(self, tmp_deploy_env):
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        store = DeployManifestStore(db_path=tmp_deploy_env["db_file"])
        transitioner = LinearDeployTransitioner(dry_run=True)
        health = PostDeployHealthChecker()

        pipeline = DeployReceiverPipeline(
            source_repo=tmp_deploy_env["source_repo"],
            deploy_runner=runner,
            health_checker=health,
            transitioner=transitioner,
            store=store,
            # The gateway redeploy has its own coverage; a real redeploy
            # refuses in a tmp non-repo, which would fail this pipeline test
            # for an environmental reason. (Red on main, Sep 22, 2026.)
            gateway_redeployer=_StubGatewayRedeployer(),
        )

        payload = {
            "pr_sha": tmp_deploy_env["head_sha"],
            "pr_number": 42,
            "pr_title": "Fix GRO-5000 deploy hook",
            "deployer": "github-action",
        }

        record = pipeline.process_deploy(payload)
        assert record.success is True
        assert record.pr_sha == tmp_deploy_env["head_sha"]
        assert len(record.linear_transitions) == 1
        assert record.linear_transitions[0]["issue_id"] == "GRO-5000"


class _StubGatewayRedeployer:
    """Test double: gateway redeploy that never touches git or the network."""

    def __init__(self, success=True, skipped=True, reason="test-stub"):
        self._res = GatewayDeployResult(
            success=success, skipped=skipped, reason=reason, pr_sha="t" * 40
        )

    def redeploy(self, pr_sha="", repo=None, dry_run=False):
        return self._res


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_git_mirror(tmp_path):
    """Bare 'remote' repo + a clone acting as the mirror, both on origin/testbranch."""
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(remote), cwd=str(tmp_path))
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "testbranch", str(seed), cwd=str(tmp_path))
    _git("config", "user.email", "test@example.com", cwd=str(seed))
    _git("config", "user.name", "test", cwd=str(seed))
    (seed / "file.txt").write_text("v1", encoding="utf-8")
    _git("add", ".", cwd=str(seed))
    _git("commit", "-qm", "seed", cwd=str(seed))
    _git("remote", "add", "origin", str(remote), cwd=str(seed))
    _git("push", "-q", "origin", "testbranch", cwd=str(seed))
    mirror = tmp_path / "mirror"
    _git("clone", "-q", str(remote), str(mirror), cwd=str(tmp_path))
    return remote, seed, mirror


def _mirror_origin_sha(mirror):
    out = subprocess.run(
        ["git", "-C", str(mirror), "rev-parse", "origin/testbranch"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _make_mirror_pipeline(mirror_env, mirror_repo, gateway_redeployer=None):
    return DeployReceiverPipeline(
        source_repo=mirror_env["source_repo"],
        deploy_runner=AtomicDeployRunner(
            versions_dir=mirror_env["versions_dir"],
            release_symlink=mirror_env["symlink_path"],
        ),
        health_checker=PostDeployHealthChecker(),
        transitioner=LinearDeployTransitioner(dry_run=True),
        store=DeployManifestStore(db_path=mirror_env["db_file"]),
        gateway_redeployer=gateway_redeployer or _StubGatewayRedeployer(),
        mirror_repo=mirror_repo,
    )


class TestMirrorFetchPiggyback:
    def test_successful_deploy_refreshes_mirror(self, tmp_path, tmp_deploy_env):
        remote, seed, mirror = _make_git_mirror(tmp_path)
        # Advance origin/testbranch AFTER the mirror was cloned: the mirror is stale.
        (seed / "file.txt").write_text("v2", encoding="utf-8")
        _git("commit", "-qam", "v2", cwd=str(seed))
        _git("push", "-q", "origin", "testbranch", cwd=str(seed))
        before = _mirror_origin_sha(mirror)

        pipeline = _make_mirror_pipeline(tmp_deploy_env, mirror_repo=mirror)
        record = pipeline.process_deploy(
            {"pr_sha": tmp_deploy_env["head_sha"], "pr_number": 7, "pr_title": "mirror test"}
        )

        assert record.success is True
        assert record.mirror_refresh == {"refreshed": True, "reason": "fetch-ok"}
        assert _mirror_origin_sha(mirror) != before

    def test_mirror_refresh_failure_is_fail_closed(self, tmp_path, tmp_deploy_env):
        # Git repo whose origin points at a nonexistent local path: fetch
        # exits non-zero. The deploy must still succeed.
        broken = tmp_path / "broken"
        _git("init", "-q", "-b", "testbranch", str(broken), cwd=str(tmp_path))
        _git(
            "remote",
            "add",
            "origin",
            str(tmp_path / "does-not-exist.git"),
            cwd=str(broken),
        )

        pipeline = _make_mirror_pipeline(tmp_deploy_env, mirror_repo=broken)
        record = pipeline.process_deploy({"pr_sha": tmp_deploy_env["head_sha"]})

        assert record.success is True
        assert record.mirror_refresh["refreshed"] is False
        assert record.mirror_refresh["reason"].startswith("git-rc-")

    def test_mirror_missing_dir_is_fail_closed(self, tmp_path, tmp_deploy_env):
        pipeline = _make_mirror_pipeline(
            tmp_deploy_env, mirror_repo=tmp_path / "no-such-dir"
        )
        record = pipeline.process_deploy({"pr_sha": tmp_deploy_env["head_sha"]})

        assert record.success is True
        assert record.mirror_refresh == {
            "refreshed": False,
            "reason": "mirror-not-present",
        }

    def test_mirror_refresh_git_missing_is_fail_closed(
        self, tmp_path, tmp_deploy_env, monkeypatch
    ):
        # git fetch blows up inside the mirror refresh only: stub
        # subprocess in the receiver module's namespace so the deploy's own
        # git validation still runs. The refresh must stay fail-closed.
        _, _, mirror = _make_git_mirror(tmp_path)

        # git fetch blows up inside the mirror refresh only: raise for
        # the fetch argv, delegate everything else (the deploy's own git
        # validation must still run). The refresh must stay fail-closed.
        real_run = subprocess.run

        def selective_boom(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args", [])
            if "fetch" in argv:
                raise FileNotFoundError("git: command not found")
            return real_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", selective_boom)
        pipeline = _make_mirror_pipeline(tmp_deploy_env, mirror_repo=mirror)
        record = pipeline.process_deploy(
            {"pr_sha": tmp_deploy_env["head_sha"]}
        )

        assert record.success is True

        assert record.success is True
        assert record.mirror_refresh["refreshed"] is False
        assert record.mirror_refresh["reason"].startswith("fetch-error:")

    def test_dry_run_skips_mirror_refresh(self, tmp_path, tmp_deploy_env):
        _, _, mirror = _make_git_mirror(tmp_path)
        before = _mirror_origin_sha(mirror)

        pipeline = _make_mirror_pipeline(tmp_deploy_env, mirror_repo=mirror)
        record = pipeline.process_deploy({"pr_sha": "h" * 40, "dry_run": True})

        assert record.success is True
        assert record.mirror_refresh == {
            "refreshed": False,
            "reason": "skipped: dry-run",
        }
        assert _mirror_origin_sha(mirror) == before

    def test_failed_deploy_skips_mirror_refresh(self, tmp_path, tmp_deploy_env):
        _, _, mirror = _make_git_mirror(tmp_path)
        before = _mirror_origin_sha(mirror)

        pipeline = _make_mirror_pipeline(
            tmp_deploy_env,
            mirror_repo=mirror,
            gateway_redeployer=_StubGatewayRedeployer(
                success=False, skipped=False, reason="boom"
            ),
        )
        record = pipeline.process_deploy({"pr_sha": tmp_deploy_env["head_sha"]})

        assert record.success is False
        assert record.mirror_refresh == {
            "refreshed": False,
            "reason": "skipped: deploy failed",
        }
        assert _mirror_origin_sha(mirror) == before

    def test_from_dict_backward_compat(self):
        rec = DeployRecord.from_dict({"pr_sha": "j" * 40})
        assert rec.mirror_refresh == {}
        assert rec.to_dict()["mirror_refresh"] == {}
class _FailingDeployRunner(AtomicDeployRunner):
    """Deploy runner stub whose deploy() always fails with a fixed error."""

    def __init__(self, err, versions_dir, symlink_path):
        super().__init__(versions_dir=versions_dir, release_symlink=symlink_path)
        self._err = err

    def deploy(self, source_repo=None, pr_sha="", **kwargs):
        return False, self.versions_dir / "prismatic-engine-deadbeef", self._err


class _FailingHealthChecker:
    """Health checker stub that always fails the version-dir check."""

    def check(self, version_dir=None, release_symlink=None, dry_run=False):
        return {
            "passed": False,
            "checks": {"version_dir_valid": False},
            "details": {"version_dir_error": "missing"},
        }


class TestDeploySourceDecoupling:
    """The deploy source is never derived from CWD (Sep 22, 2026)."""

    def test_unset_source_repo_fails_fast_at_startup(self, monkeypatch):
        monkeypatch.delenv("PRISMATIC_DEPLOY_SOURCE_REPO", raising=False)
        with pytest.raises(RuntimeError, match="PRISMATIC_DEPLOY_SOURCE_REPO"):
            DeployReceiverPipeline()

    def test_env_source_repo_is_used(self, tmp_path, monkeypatch, tmp_deploy_env):
        monkeypatch.setenv(
            "PRISMATIC_DEPLOY_SOURCE_REPO", str(tmp_deploy_env["source_repo"])
        )
        pipeline = DeployReceiverPipeline()
        assert pipeline.source_repo == tmp_deploy_env["source_repo"]

    def test_none_source_repo_fails_before_copy(self, tmp_deploy_env):
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        success, version_dir, err = runner.deploy(
            source_repo=None, pr_sha="a" * 40
        )
        assert success is False
        assert "source_repo is required" in err
        assert not version_dir.exists()

    def test_non_git_source_fails_before_copy(self, tmp_deploy_env):
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        plain = tmp_deploy_env["versions_dir"] / "not-a-repo"
        plain.mkdir()
        (plain / "prismatic").mkdir()
        success, version_dir, err = runner.deploy(
            source_repo=plain, pr_sha="a" * 40
        )
        assert success is False
        assert "not a git repository" in err
        assert not version_dir.exists()

    def test_source_missing_prismatic_dir_fails(self, tmp_path, tmp_deploy_env):
        repo = tmp_path / "empty-repo"
        _git("init", "-q", "-b", "main", str(repo), cwd=str(tmp_path))
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        success, version_dir, err = runner.deploy(
            source_repo=repo, pr_sha="a" * 40
        )
        assert success is False
        assert "no prismatic/" in err
        assert not version_dir.exists()

    def test_unresolvable_sha_fails(self, tmp_deploy_env):
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        success, version_dir, err = runner.deploy(
            source_repo=tmp_deploy_env["source_repo"], pr_sha="f" * 40
        )
        assert success is False
        assert "not a commit" in err
        assert not version_dir.exists()

    def test_version_dir_inside_source_is_refused(self, tmp_deploy_env):
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        nested = tmp_deploy_env["source_repo"] / "versions"
        with pytest.raises(ValueError, match="inside source"):
            runner._copy_release_files(
                tmp_deploy_env["source_repo"],
                nested / "prismatic-engine-abc",
                tmp_deploy_env["head_sha"],
            )

    def test_rsync_fallback_has_timeout_and_exclusions(
        self, tmp_deploy_env, monkeypatch
    ):
        import pe.deploy.integrate as integrate_mod

        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["timeout"] = kwargs.get("timeout")
            # pretend rsync does the copy
            (tmp_deploy_env["versions_dir"] / "x").mkdir(exist_ok=True)
            class R:
                returncode = 0
            return R()

        monkeypatch.setattr(integrate_mod.subprocess, "run", fake_run)
        runner = AtomicDeployRunner(
            versions_dir=tmp_deploy_env["versions_dir"],
            release_symlink=tmp_deploy_env["symlink_path"],
        )
        dest = tmp_deploy_env["versions_dir"] / "prismatic-engine-rsynctest"
        runner._copy_release_files(
            tmp_deploy_env["source_repo"],
            dest,
            tmp_deploy_env["head_sha"],
            copy_method="rsync",
        )
        assert seen["timeout"] == 900
        assert "--exclude=versions/" in seen["argv"]
        assert "--exclude=releases/" in seen["argv"]
        assert "--exclude=.git" in seen["argv"]

    def test_health_check_preserves_deploy_step_error(self, tmp_path):
        versions = tmp_path / "versions"
        releases = tmp_path / "releases"
        versions.mkdir()
        releases.mkdir()
        pipeline = DeployReceiverPipeline(
            source_repo=tmp_path / "repo",
            deploy_runner=_FailingDeployRunner(
                "rsync died: No space left on device",
                versions_dir=versions,
                symlink_path=releases / "prismatic-engine",
            ),
            health_checker=_FailingHealthChecker(),
            transitioner=LinearDeployTransitioner(dry_run=True),
            store=DeployManifestStore(db_path=tmp_path / "deploy_records.json"),
            gateway_redeployer=_StubGatewayRedeployer(),
            mirror_repo=tmp_path / "no-such-dir",
        )
        record = pipeline.process_deploy({"pr_sha": "a" * 40, "pr_number": 9})
        assert record.success is False
        # The underlying deploy error must survive; the health failure is
        # appended, not substituted (Sep 22, 2026 incident).
        assert "rsync died: No space left on device" in record.failure_reason
        assert "Post-deploy health check failed" in record.failure_reason
