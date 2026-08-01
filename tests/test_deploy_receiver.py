"""Unit and integration tests for Workstream B: Linear -> PR -> Prod Deploy Hook (WB-10).
"""

import hashlib
import hmac

import os
import pytest
os.environ["PRISMATIC_ALLOW_DEFAULT_HMAC"] = "1"

from pe.deploy.health import PostDeployHealthChecker
from pe.deploy.integrate import AtomicDeployRunner
from pe.deploy.linear_transition import LinearDeployTransitioner
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
