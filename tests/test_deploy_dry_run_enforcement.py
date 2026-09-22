"""Regression tests: dry_run=true must produce ZERO deployment side effects.

Proves the fail-closed dry-run gate in pe.deploy.receiver.DeployReceiverPipeline.
Before the 2026-09-22 repair, a payload-level ``dry_run: true`` was evaluated
only AFTER ``AtomicDeployRunner.deploy()`` had already created the versioned
release dir, moved the release symlink, and fired Linear transitions -- a
"dry run" that deployed. These tests pin the fixed behavior: dry run means
no release dir, no symlink move, no gateway redeploy, no Linear transitions,
no mirror refresh.
"""

import subprocess

import pytest

from pe.deploy.gateway_redeploy import GatewayDeployResult
from pe.deploy.integrate import AtomicDeployRunner
from pe.deploy.linear_transition import (
    LinearDeployTransitioner,
    LinearTransitionsStore,
)
from pe.deploy.manifest import DeployManifestStore, DeployRecord
from pe.deploy.receiver import DeployReceiverPipeline


@pytest.fixture
def dry_run_env(tmp_path):
    versions_dir = tmp_path / "versions"
    releases_dir = tmp_path / "releases"
    source_repo = tmp_path / "repo"
    versions_dir.mkdir()
    releases_dir.mkdir()
    source_repo.mkdir()
    (source_repo / "prismatic").mkdir()
    (source_repo / "prismatic" / "__init__.py").write_text("# main", encoding="utf-8")
    return {
        "versions_dir": versions_dir,
        "symlink_path": releases_dir / "prismatic-engine",
        "db_file": tmp_path / "deploy_records.json",
        "transitions_db": tmp_path / "linear_transitions.json",
        "source_repo": source_repo,
    }


class _RecordingHealthChecker:
    """Health checker double that records whether it was invoked."""

    def __init__(self):
        self.calls = 0

    def check(self, **kwargs):
        self.calls += 1
        return {"passed": True, "checks": {}, "details": {"stub": True}}


class _RecordingGatewayRedeployer:
    """Gateway redeployer double that records whether it was invoked."""

    def __init__(self):
        self.calls = 0

    def redeploy(self, pr_sha="", repo=None, dry_run=False):
        self.calls += 1
        return GatewayDeployResult(
            success=True, skipped=True, reason="test-stub", pr_sha=pr_sha
        )


def _make_pipeline(env, runner_dry_run=False, mirror_repo=None):
    health = _RecordingHealthChecker()
    gateway = _RecordingGatewayRedeployer()
    transitioner = LinearDeployTransitioner(
        dry_run=True,
        store=LinearTransitionsStore(db_path=env["transitions_db"]),
    )
    store = DeployManifestStore(db_path=env["db_file"])
    pipeline = DeployReceiverPipeline(
        source_repo=env["source_repo"],
        deploy_runner=AtomicDeployRunner(
            versions_dir=env["versions_dir"],
            release_symlink=env["symlink_path"],
            dry_run=runner_dry_run,
        ),
        health_checker=health,
        transitioner=transitioner,
        store=store,
        gateway_redeployer=gateway,
        mirror_repo=mirror_repo,
    )
    return pipeline, health, gateway, store


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_git_mirror(tmp_path):
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
    # Advance origin AFTER the clone: the mirror is stale and would change on fetch.
    (seed / "file.txt").write_text("v2", encoding="utf-8")
    _git("commit", "-qam", "v2", cwd=str(seed))
    _git("push", "-q", "origin", "testbranch", cwd=str(seed))
    before = subprocess.run(
        ["git", "-C", str(mirror), "rev-parse", "origin/testbranch"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return mirror, before


def _base_payload(**overrides):
    payload = {
        "pr_sha": "d" * 40,
        "pr_number": 99,
        "pr_title": "dry-run enforcement probe",
        "deployer": "pytest",
        "merged_at": "2026-09-22T00:00:00Z",
    }
    payload.update(overrides)
    return payload


class TestPayloadDryRunEnforcement:
    def test_payload_dry_run_produces_zero_deployment_side_effects(
        self, tmp_path, dry_run_env
    ):
        """The core regression test: payload dry_run=true deploys NOTHING."""
        mirror, mirror_before = _make_git_mirror(tmp_path)
        pipeline, health, gateway, store = _make_pipeline(
            dry_run_env, mirror_repo=mirror
        )

        record = pipeline.process_deploy(_base_payload(dry_run=True))

        # No release dir was created ...
        assert list(dry_run_env["versions_dir"].iterdir()) == []
        # ... the release symlink was not created or moved ...
        assert not dry_run_env["symlink_path"].is_symlink()
        assert not dry_run_env["symlink_path"].exists()
        # ... the gateway redeployer was never invoked ...
        assert gateway.calls == 0
        # ... no Linear transitions fired ...
        assert record.linear_transitions == []
        # ... the health checker was never invoked ...
        assert health.calls == 0
        # ... the repo mirror was not refreshed ...
        assert record.mirror_refresh == {
            "refreshed": False,
            "reason": "skipped: dry-run",
        }
        after = subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", "origin/testbranch"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert after == mirror_before
        # ... and the run is recorded as an explicitly-marked dry run, never
        # mistaken for a real deployment.
        assert record.success is True
        assert record.dry_run is True
        assert record.deploy_id.endswith("-dryrun")
        assert record.version_dir == ""
        stored = store.get_latest()
        assert stored is not None
        assert stored.dry_run is True

    def test_payload_without_dry_run_still_deploys(self, dry_run_env):
        """Control: the harness CAN detect a real deploy (test is not vacuous)."""
        pipeline, health, gateway, store = _make_pipeline(dry_run_env)

        record = pipeline.process_deploy(_base_payload())

        assert record.success is True
        assert record.dry_run is False
        version_dirs = list(dry_run_env["versions_dir"].iterdir())
        assert len(version_dirs) == 1
        assert (version_dirs[0] / "prismatic" / "__init__.py").exists()
        assert dry_run_env["symlink_path"].is_symlink()
        assert dry_run_env["symlink_path"].resolve() == version_dirs[0].resolve()
        assert gateway.calls == 1
        assert health.calls == 1

    def test_runner_flag_dry_run_no_side_effects(self, dry_run_env):
        """The constructor-level dry_run flag also short-circuits everything."""
        pipeline, health, gateway, _ = _make_pipeline(
            dry_run_env, runner_dry_run=True
        )

        record = pipeline.process_deploy(_base_payload())

        assert record.success is True
        assert record.dry_run is True
        assert list(dry_run_env["versions_dir"].iterdir()) == []
        assert not dry_run_env["symlink_path"].exists()
        assert gateway.calls == 0
        assert health.calls == 0
        assert record.linear_transitions == []

    @pytest.mark.parametrize("dry_run_value", ["false", "no", "0", "yes", 1, True])
    def test_truthy_dry_run_values_fail_closed(self, dry_run_env, dry_run_value):
        """Ambiguity resolves to no-deploy: any truthy dry_run value is a dry run.

        Note ``"false"`` (string) is truthy -- fail-closed means it must NOT
        deploy. Callers that mean "really deploy" send a real boolean false.
        """
        pipeline, _, _, _ = _make_pipeline(dry_run_env)

        record = pipeline.process_deploy(
            _base_payload(dry_run=dry_run_value)
        )

        assert record.dry_run is True
        assert list(dry_run_env["versions_dir"].iterdir()) == []
        assert not dry_run_env["symlink_path"].exists()

    @pytest.mark.parametrize("dry_run_value", [False, 0, None])
    def test_falsy_dry_run_values_deploy(self, dry_run_env, dry_run_value):
        """Explicitly falsy dry_run values (and a missing key) take the real path."""
        pipeline, _, _, _ = _make_pipeline(dry_run_env)

        record = pipeline.process_deploy(
            _base_payload(dry_run=dry_run_value)
        )

        assert record.dry_run is False
        assert len(list(dry_run_env["versions_dir"].iterdir())) == 1

    def test_missing_dry_run_key_deploys(self, dry_run_env):
        # _base_payload() never sets "dry_run": the key is simply absent.
        pipeline, _, _, _ = _make_pipeline(dry_run_env)

        record = pipeline.process_deploy(_base_payload())

        assert record.dry_run is False
        assert len(list(dry_run_env["versions_dir"].iterdir())) == 1


class TestDeployRecordDryRunField:
    def test_from_dict_backward_compat(self):
        """Records written before the dry_run field existed load as non-dry-run."""
        rec = DeployRecord.from_dict({"pr_sha": "a" * 40})
        assert rec.dry_run is False
        assert rec.to_dict()["dry_run"] is False

    def test_dry_run_round_trip(self, tmp_path):
        store = DeployManifestStore(db_path=tmp_path / "db.json")
        store.record_deploy(DeployRecord(pr_sha="b" * 40, dry_run=True))
        assert store.get_latest().dry_run is True
