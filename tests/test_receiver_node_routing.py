"""Tests for WS7 receiver routing: repo -> target_node -> node executor.

Hermetic: no real SSH, no real tailnet. The node executor is an injected
fake; the local path uses the pipeline's normal doubles.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from pe.deploy.config import load_repo_registry
from pe.deploy.gateway_redeploy import GatewayDeployResult
from pe.deploy.node_executor import NodeDeployResult, NodeUnreachableError
from pe.deploy.receiver import DeployReceiverPipeline


WIDGETS = "acme/widgets"
GADGETS = "acme/gadgets"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def node_env(tmp_path, monkeypatch):
    """Hermetic env: registry with a node-targeted repo + a node registry."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    monkeypatch.delenv("PRISMATIC_ALLOW_DEFAULT_HMAC", raising=False)
    monkeypatch.delenv("PRISMATIC_STRICT_SECRETS", raising=False)
    monkeypatch.delenv("DEPLOY_HMAC_SECRET", raising=False)

    repos = {
        WIDGETS: {
            "mirror_dir": str(tmp_path / "m1"),
            "release_prefix": "widgets",
            "target_service": "widgets.service",
            "target_node": "athens",
        },
        GADGETS: {
            "mirror_dir": str(tmp_path / "m2"),
            "release_prefix": "gadgets",
            "target_service": "gadgets.service",
            # target_node omitted -> "local"
        },
    }
    repos_file = tmp_path / "repos.json"
    repos_file.write_text(json.dumps(repos), encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(repos_file))

    nodes_file = tmp_path / "nodes.yaml"
    nodes_file.write_text(
        "nodes:\n  - name: athens\n    address: 100.99.0.2\n    ssh_user: ubuntu\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRISMATIC_NODES_FILE", str(nodes_file))

    src = tmp_path / "src"
    src.mkdir()
    (src / "prismatic").mkdir()
    (src / "prismatic" / "__init__.py").write_text("# main", encoding="utf-8")
    _git("init", "-q", "-b", "main", str(src), cwd=str(tmp_path))
    _git("config", "user.email", "test@example.com", cwd=str(src))
    _git("config", "user.name", "test", cwd=str(src))
    _git("add", ".", cwd=str(src))
    _git("commit", "-qm", "seed", cwd=str(src))
    monkeypatch.setenv("PRISMATIC_DEPLOY_SOURCE_REPO", str(src))

    monkeypatch.setenv("PRISMATIC_DEPLOY_DB", str(tmp_path / "deploy_records.json"))
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    return tmp_path


class FakeNodeDeployer:
    """Injected node executor double: records call order."""

    instances: list["FakeNodeDeployer"] = []

    def __init__(self, node, repo, reachable=True, deploy_ok=True):
        self.node = node
        self.repo = repo
        self.reachable = reachable
        self.deploy_ok = deploy_ok
        self.calls: list[str] = []
        FakeNodeDeployer.instances.append(self)

    def check_reachable(self):
        self.calls.append("check_reachable")
        if not self.reachable:
            raise NodeUnreachableError("simulated unreachable")
        return {"ok": True, "address": "100.99.0.2"}

    def deploy(self, pr_sha, dry_run=False):
        self.calls.append("deploy")
        gw = GatewayDeployResult(pr_sha=pr_sha)
        gw.success = self.deploy_ok
        gw.reason = "fake remote deploy"
        gw.rolled_back = not self.deploy_ok
        gw.health = {"passed": self.deploy_ok, "checks": {}, "details": {}}
        return NodeDeployResult(
            node=self.node.name,
            success=self.deploy_ok,
            pr_sha=pr_sha,
            gateway=gw,
            reason="fake remote deploy",
            rolled_back=not self.deploy_ok,
        )


@pytest.fixture(autouse=True)
def _clear_instances():
    FakeNodeDeployer.instances.clear()
    yield
    FakeNodeDeployer.instances.clear()


def _pipeline(tmp_path, **kw):
    return DeployReceiverPipeline(
        source_repo=tmp_path / "src",
        transitioner=SimpleNamespace(transition_issues_for_deploy=lambda **k: []),
        **kw,
    )


def _payload(repo):
    return {
        "pr_sha": "a" * 40,
        "pr_number": 7,
        "pr_title": "test",
        "deployer": "test",
        "repository": repo,
    }


class TestNodeRouting:
    def test_repo_with_target_node_uses_node_executor(self, node_env):
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
        )
        record = pipeline.process_deploy(_payload(WIDGETS))
        assert record.success is True
        assert len(FakeNodeDeployer.instances) == 1
        fake = FakeNodeDeployer.instances[0]
        assert fake.node.name == "athens"
        assert fake.repo.full_name == WIDGETS
        # Reachability was verified before the deploy ran.
        assert fake.calls == ["check_reachable", "deploy"]
        assert record.gateway_deploy["node"] == "athens"
        assert "node-athens" in record.deploy_id
        alerts = (node_env / "alerts.log").read_text(encoding="utf-8")
        assert "PostMergeDeploySucceeded" in alerts
        assert "node=athens" in alerts

    def test_failed_node_deploy_records_rollback(self, node_env):
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(
                node, repo, deploy_ok=False
            ),
        )
        record = pipeline.process_deploy(_payload(WIDGETS))
        assert record.success is False
        assert record.gateway_deploy["rolled_back"] is True
        alerts = (node_env / "alerts.log").read_text(encoding="utf-8")
        assert "PostMergeDeployFailed" in alerts
        assert "node=athens" in alerts

    def test_default_repo_stays_local(self, node_env):
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
        )
        # A dry run keeps the local path hermetic and proves routing did
        # NOT hand the default repo to the node executor.
        payload = _payload(GADGETS)
        payload["dry_run"] = True
        record = pipeline.process_deploy(payload)
        assert record.success is True
        assert record.dry_run is True
        assert FakeNodeDeployer.instances == []


class TestNodeDryRun:
    def test_node_targeted_dry_run_is_zero_remote_action(self, node_env):
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
        )
        payload = _payload(WIDGETS)
        payload["dry_run"] = True
        record = pipeline.process_deploy(payload)
        assert record.success is True
        assert record.dry_run is True
        # The executor was never built: no factory call, no probe, no ssh.
        assert FakeNodeDeployer.instances == []
        assert record.gateway_deploy["node"] == "athens"
        assert record.gateway_deploy["skipped"] is True
        assert record.linear_transitions == []
        # Same contract as the local dry-run path: the record is the audit
        # trail, but no alert is emitted -- the alert log is never created.
        assert not (node_env / "alerts.log").exists()

    def test_node_dry_run_still_refuses_unknown_node(self, node_env):
        (node_env / "nodes.yaml").write_text("nodes: []\n", encoding="utf-8")
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
        )
        payload = _payload(WIDGETS)
        payload["dry_run"] = True
        record = pipeline.process_deploy(payload)
        assert record.success is False
        assert "unknown target node 'athens'" in (record.failure_reason or "")


class TestNodeFailClosed:
    def test_unknown_node_refused_before_any_remote_action(self, node_env, monkeypatch):
        # Registry without athens: the repo names an unknown node.
        (node_env / "nodes.yaml").write_text("nodes: []\n", encoding="utf-8")
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
        )
        record = pipeline.process_deploy(_payload(WIDGETS))
        assert record.success is False
        assert "unknown target node 'athens'" in (record.failure_reason or "")
        assert FakeNodeDeployer.instances == []  # factory never called
        alerts = (node_env / "alerts.log").read_text(encoding="utf-8")
        assert "PostMergeDeployFailed" in alerts
        assert "athens" in alerts

    def test_unreachable_node_refused_before_remote_action(self, node_env):
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(
                node, repo, reachable=False
            ),
        )
        record = pipeline.process_deploy(_payload(WIDGETS))
        assert record.success is False
        assert "unreachable" in (record.failure_reason or "")
        fake = FakeNodeDeployer.instances[0]
        # check_reachable refused; deploy() never ran -> no remote action.
        assert fake.calls == ["check_reachable"]
        alerts = (node_env / "alerts.log").read_text(encoding="utf-8")
        assert "PostMergeDeployFailed" in alerts

    def test_unreadable_registry_refused(self, node_env, monkeypatch):
        bad = node_env / "nodes.yaml"
        bad.write_text("nodes: [oops\n", encoding="utf-8")
        pipeline = _pipeline(
            node_env,
            node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
        )
        record = pipeline.process_deploy(_payload(WIDGETS))
        assert record.success is False
        assert "node registry unreadable" in (record.failure_reason or "")
        assert FakeNodeDeployer.instances == []

    def test_target_node_local_explicit_stays_local(self, node_env):
        reg = load_repo_registry()
        widgets = reg.get(WIDGETS)
        assert widgets.target_node == "athens"
        gadgets = reg.get(GADGETS)
        assert gadgets.target_node == "local"
