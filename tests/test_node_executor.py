"""Tests for the WS7 node executor (pe.deploy.node_executor).

Never real SSH, never real tailnet: the transport's subprocess call is a
scripted double, and the mesh client is faked. These tests assert:

* the ssh argv is fail-closed (BatchMode, strict host-key checking, pinned
  known-hosts, per-call timeout, no key spray for tailscale-ssh);
* the executor checks reachability BEFORE any remote action (fail-closed);
* the full deploy cycle runs through the reused GatewayRedeployer steps
  (build -> flip -> restart -> health poll -> rollback on failure) with
  alerts tagged with the node name;
* the 90s per-endpoint health budget is untouched.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from pe.deploy import config as deploy_config
from pe.deploy.gateway_redeploy import GatewayDeployResult, GatewayRedeployer
from pe.deploy.node_executor import (
    NodeDeployer,
    NodeUnreachableError,
    SSHError,
    SubprocessSSHTransport,
    probe_ssh,
)
from pe.deploy.nodes import DeployNode
from pe.deploy.process_manager_systemd import SystemdProcessManager


ATHENS_IP = "100.99.0.2"


def _node(**kw):
    base = dict(name="athens", address=ATHENS_IP, ssh_user="ubuntu")
    base.update(kw)
    return DeployNode(**base)


class FakeMeshClient:
    """Resolves athens; ping scripted."""

    def __init__(self, ping_ok=True):
        self.ping_ok = ping_ok

    def list_nodes_sync(self):
        return []

    async def ping(self, target, count=1):
        if self.ping_ok:
            return {"target": target, "success": True, "rtt_ms": 2.0}
        return {"target": target, "success": False, "error": "no pong"}


class ScriptedSSHRun:
    """Double for the transport's subprocess call. Records full ssh argv."""

    def __init__(self, probe_ok=True):
        self.probe_ok = probe_ok
        self.calls: list[list[str]] = []
        self.timeouts: list[int | None] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.timeouts.append(kwargs.get("timeout"))
        remote = argv[-1]
        if remote == "true":
            rc = 0 if self.probe_ok else 1
            return SimpleNamespace(returncode=rc, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def remote_commands(self):
        return [c[-1] for c in self.calls]


class FakeRedeployer:
    """Stand-in for GatewayRedeployer: records construction, scripts results."""

    instances: list["FakeRedeployer"] = []

    def __init__(self, fail=False, rolled_back=False, **kwargs):
        self.kwargs = kwargs
        self.fail = fail
        self.rolled_back = rolled_back
        self.redeploy_calls: list[tuple[str, Path]] = []
        FakeRedeployer.instances.append(self)

    def redeploy(self, pr_sha, repo, repo_config=None):
        self.redeploy_calls.append((pr_sha, Path(repo)))
        res = GatewayDeployResult(pr_sha=pr_sha)
        if self.fail:
            res.success = False
            res.reason = "simulated remote failure"
            res.rolled_back = self.rolled_back
            res.health = {"passed": False, "checks": {}, "details": {}}
        else:
            res.success = True
            res.reason = "gateway redeployed to deadbeef"
            res.version_dir = f"/home/ubuntu/.prismatic/releases/x-{pr_sha}"
            res.health = {"passed": True, "checks": {}, "details": {}}
        return res


@pytest.fixture(autouse=True)
def _clear_fake_instances():
    FakeRedeployer.instances.clear()
    yield
    FakeRedeployer.instances.clear()


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PRISMATIC_NODES_FILE", raising=False)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / ".prismatic"))
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    monkeypatch.setenv("PRISMATIC_SSH_KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    (tmp_path / "known_hosts").write_text("# pinned\n", encoding="utf-8")
    return tmp_path


def _repo_config():
    return deploy_config.DeployRepoConfig(
        full_name="acme/widgets",
        mirror_dir=Path("/tmp/mirror"),
        release_prefix="widgets",
        target_service="widgets.service",
        target_node="athens",
    )


class TestTransportArgv:
    def test_fail_closed_flags(self, hermetic):
        t = SubprocessSSHTransport(_node())
        argv = t.ssh_argv(["systemctl", "restart", "x.service"])
        assert argv[0] == "ssh"
        assert "-o" in argv
        joined = " ".join(argv)
        assert "BatchMode=yes" in joined  # never prompts
        assert "StrictHostKeyChecking=yes" in joined  # unknown key refuses
        assert f"UserKnownHostsFile={hermetic / 'known_hosts'}" in joined
        assert "ConnectTimeout=" in joined
        assert argv[-3] == "ubuntu@100.99.0.2"
        # No -i for tailscale-ssh: auth rides the tailnet identity.
        assert "-i" not in argv

    def test_key_method_argv(self, tmp_path):
        key = tmp_path / "id_deploy"
        key.write_text("x", encoding="utf-8")
        node = _node(ssh_method="key", ssh_key=str(key))
        t = SubprocessSSHTransport(node)
        joined = " ".join(t.ssh_argv(["true"]))
        assert "-i" in joined and str(key) in joined
        assert "IdentitiesOnly=yes" in joined  # no agent key spray

    def test_key_method_requires_key_material(self):
        # DeployNode itself refuses key-method nodes without a key file.
        with pytest.raises(ValueError):
            DeployNode(name="k", address=ATHENS_IP, ssh_user="u", ssh_method="key")

    def test_refuses_local_node(self):
        with pytest.raises(SSHError):
            SubprocessSSHTransport(DeployNode.local())

    def test_timeouts_on_every_call(self, hermetic):
        calls: list[dict] = []

        def _run(argv, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        t = SubprocessSSHTransport(_node(), run=_run)
        t.run(["true"], timeout=30)
        assert calls[0]["timeout"] == 30
        t.run(["true"])  # no timeout -> bounded default, never unbounded
        assert calls[0 + 1]["timeout"] == 900

    def test_probe_ssh_uses_fail_closed_transport(self, hermetic):
        run = ScriptedSSHRun(probe_ok=True)
        assert probe_ssh(_node(), ATHENS_IP, run=run) is True
        # The probe ran through the real ssh argv builder.
        assert run.calls and run.calls[0][0] == "ssh"
        assert "StrictHostKeyChecking=yes" in " ".join(run.calls[0])

    def test_cwd_supported(self, hermetic):
        t = SubprocessSSHTransport(_node())
        argv = t.ssh_argv(["make"], cwd="/home/ubuntu/src")
        assert (
            "cd '/home/ubuntu/src'" in argv[-1]
            or 'cd "/home/ubuntu/src"' in argv[-1]
            or "cd /home/ubuntu/src" in argv[-1]
        )


class TestReachabilityFirst:
    def test_unresolvable_node_refused_before_any_remote_action(self, hermetic):
        run = ScriptedSSHRun()
        node = DeployNode(name="ghost", address="ghost", ssh_user="u")
        deployer = NodeDeployer(
            node,
            _repo_config(),
            transport=SubprocessSSHTransport(_node(), run=run),
            mesh_client=FakeMeshClient(),
        )
        with pytest.raises(NodeUnreachableError):
            deployer.check_reachable()
        assert run.calls == []  # zero remote actions

    def test_unreachable_ping_refused_before_ssh(self, hermetic):
        run = ScriptedSSHRun()
        transport = SubprocessSSHTransport(_node(), run=run)
        deployer = NodeDeployer(
            _node(),
            _repo_config(),
            transport=transport,
            mesh_client=FakeMeshClient(ping_ok=False),
        )
        with pytest.raises(NodeUnreachableError) as excinfo:
            deployer.check_reachable()
        assert "unreachable" in str(excinfo.value)
        assert run.calls == []  # ping failed -> ssh probe never ran

    def test_failed_ssh_probe_refused(self, hermetic):
        run = ScriptedSSHRun(probe_ok=False)
        deployer = NodeDeployer(
            _node(),
            _repo_config(),
            transport=SubprocessSSHTransport(_node(), run=run),
            mesh_client=FakeMeshClient(),
        )
        with pytest.raises(NodeUnreachableError):
            deployer.check_reachable()
        # The ssh probe DID run (it is the failing check), exactly once.
        assert len(run.calls) == 1
        assert run.calls[0][-1] == "true"

    def test_deploy_refuses_unreachable_without_remote_action(self, hermetic, tmp_path):
        run = ScriptedSSHRun(probe_ok=False)
        deployer = NodeDeployer(
            _node(),
            _repo_config(),
            transport=SubprocessSSHTransport(_node(), run=run),
            mesh_client=FakeMeshClient(ping_ok=False),
            alert_log=tmp_path / "alerts.log",
        )
        result = deployer.deploy("a" * 40)
        assert result.success is False
        assert "refused" in result.reason
        assert run.calls == []
        alerts = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "NodeDeployFailed" in alerts
        assert "node=athens" in alerts


class TestExecutorCycle:
    def _deployer(self, tmp_path, fail=False, rolled_back=False, probe_ok=True):
        run = ScriptedSSHRun(probe_ok=probe_ok)
        transport = SubprocessSSHTransport(_node(), run=run)

        def factory(**kwargs):
            return FakeRedeployer(fail=fail, rolled_back=rolled_back, **kwargs)

        deployer = NodeDeployer(
            _node(),
            _repo_config(),
            transport=transport,
            mesh_client=FakeMeshClient(),
            alert_log=tmp_path / "alerts.log",
            redeployer_factory=factory,
        )
        return deployer, run

    def test_success_cycle_reuses_redeployer_and_tags_alerts(self, tmp_path):
        deployer, run = self._deployer(tmp_path)
        result = deployer.deploy("d" * 40)
        assert result.success is True
        assert result.node == "athens"
        fake = FakeRedeployer.instances[0]
        # The reused GatewayRedeployer-equivalent got the REMOTE layout:
        assert fake.kwargs["home"] == "/home/ubuntu"
        assert str(fake.kwargs["service"]) == "widgets.service"
        assert isinstance(fake.kwargs["manager"], SystemdProcessManager)
        assert fake.redeploy_calls[0][0] == "d" * 40
        assert str(fake.redeploy_calls[0][1]).endswith(".prismatic/repos/acme/widgets")
        # Reachability probe ran before the deploy (fail-closed ordering).
        assert run.remote_commands()[0] == "true"
        # Alerts tagged with the node name.
        alerts = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "NodeDeployStarted" in alerts
        assert "NodeDeploySucceeded" in alerts
        assert alerts.count("node=athens") >= 2

    def test_rollback_on_failure_tagged(self, tmp_path):
        deployer, _ = self._deployer(tmp_path, fail=True, rolled_back=True)
        result = deployer.deploy("e" * 40)
        assert result.success is False
        assert result.rolled_back is True
        assert "rolled back" in result.reason
        alerts = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "NodeDeployFailed" in alerts
        assert "node=athens" in alerts
        assert "rolled_back=True" in alerts

    def test_rollback_failed_is_loud(self, tmp_path):
        deployer, _ = self._deployer(tmp_path, fail=True, rolled_back=False)
        result = deployer.deploy("f" * 40)
        assert result.success is False
        assert "ROLLBACK FAILED" in result.reason

    def test_deployer_dry_run_short_circuits_before_probe(self, tmp_path):
        run = ScriptedSSHRun()
        transport = SubprocessSSHTransport(_node(), run=run)
        deployer = NodeDeployer(
            _node(),
            _repo_config(),
            transport=transport,
            mesh_client=FakeMeshClient(),
            alert_log=tmp_path / "alerts.log",
        )
        result = deployer.deploy("a" * 40, dry_run=True)
        assert result.success is True
        assert result.dry_run is True
        assert result.reason == "dry-run: no remote actions taken"
        assert run.calls == []  # not even the reachability probe
        alerts = (tmp_path / "alerts.log").read_text(encoding="utf-8")
        assert "NodeDeployDryRun" in alerts
        assert "NodeDeployStarted" not in alerts


class TestHealthBudget:
    def test_poll_budget_untouched_and_target_is_node(self, tmp_path, monkeypatch):
        """The real GatewayRedeployer polls each endpoint with timeout_s=90,
        and the executor's http_get rewrites localhost -> the node address."""
        seen_urls: list[str] = []

        class _Resp:
            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=5):
            seen_urls.append(req.full_url)
            return _Resp()

        import urllib.request as urlrequest

        monkeypatch.setattr(urlrequest, "urlopen", _fake_urlopen)

        run = ScriptedSSHRun()
        transport = SubprocessSSHTransport(_node(), run=run)
        node = _node()
        deployer = NodeDeployer(
            node, _repo_config(), transport=transport, mesh_client=FakeMeshClient()
        )
        # The executor's http_get rewrites the redeployer's localhost URL.
        ok, detail = deployer._tailnet_http_get("http://localhost:9000/health", 5)
        assert ok is True
        assert seen_urls == [f"http://{ATHENS_IP}:9000/health"]

        # The 90s per-endpoint poll budget survives the executor wiring.
        poll_budgets: list[int] = []
        real_poll = GatewayRedeployer._poll_http

        def _recording_poll(self, path, timeout_s=90):
            poll_budgets.append(timeout_s)
            return real_poll(self, path, timeout_s=timeout_s)

        monkeypatch.setattr(GatewayRedeployer, "_poll_http", _recording_poll)

        def _scripted(argv, **kwargs):
            joined = " ".join(argv)
            if "is-active" in joined:
                return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        redeployer = GatewayRedeployer(
            home=str(tmp_path / "remote-home"),
            run=_scripted,
            http_get=deployer._tailnet_http_get,
            service="widgets.service",
            port=9000,
            health_endpoints=(("/health", "gateway_health"),),
            manager=SystemdProcessManager(run=_scripted),
        )
        health = redeployer._health_check(tmp_path / "venv")
        assert health["passed"] is True
        assert poll_budgets == [90]  # budget untouched
        assert seen_urls[-1] == f"http://{ATHENS_IP}:9000/health"  # over the tailnet
