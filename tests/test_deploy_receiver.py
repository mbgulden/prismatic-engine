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

    symlink_path = releases_dir / "prismatic-engine"

    return {
        "versions_dir": versions_dir,
        "releases_dir": releases_dir,
        "symlink_path": symlink_path,
        "db_file": db_file,
        "source_repo": source_repo,
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
            pr_sha="a" * 40,
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
            "pr_sha": "c" * 40,
            "pr_number": 42,
            "pr_title": "Fix GRO-5000 deploy hook",
            "deployer": "github-action",
        }

        record = pipeline.process_deploy(payload)
        assert record.success is True
        assert record.pr_sha == "c" * 40
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
            {"pr_sha": "d" * 40, "pr_number": 7, "pr_title": "mirror test"}
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
        record = pipeline.process_deploy({"pr_sha": "e" * 40})

        assert record.success is True
        assert record.mirror_refresh["refreshed"] is False
        assert record.mirror_refresh["reason"].startswith("git-rc-")

    def test_mirror_missing_dir_is_fail_closed(self, tmp_path, tmp_deploy_env):
        pipeline = _make_mirror_pipeline(
            tmp_deploy_env, mirror_repo=tmp_path / "no-such-dir"
        )
        record = pipeline.process_deploy({"pr_sha": "f" * 40})

        assert record.success is True
        assert record.mirror_refresh == {
            "refreshed": False,
            "reason": "mirror-not-present",
        }

    def test_mirror_refresh_git_missing_is_fail_closed(
        self, tmp_path, tmp_deploy_env, monkeypatch
    ):
        # No git on PATH: subprocess raises FileNotFoundError, caught.
        _, _, mirror = _make_git_mirror(tmp_path)
        monkeypatch.setenv("PATH", "")

        pipeline = _make_mirror_pipeline(tmp_deploy_env, mirror_repo=mirror)
        record = pipeline.process_deploy({"pr_sha": "g" * 40})

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
        record = pipeline.process_deploy({"pr_sha": "i" * 40})

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
