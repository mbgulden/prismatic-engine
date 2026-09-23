"""Tests for Portal Phase 1 P0 #3: deploy lifecycle events on the event bus.

Covers:
  - pe.deploy.deploy_events: event schema, socket-path precedence, the
    unix-socket push (round-trip against a fake bridge server), absent
    socket -> False without raising, unknown types refused.
  - pe.deploy.receiver: deploy.started/deploy.succeeded on the success
    path; dry-run emits nothing; refused triggers emit nothing.
  - pe.deploy.gateway_redeploy: the pilot's proven failure mode (broken
    release -> health check fails -> deploy.failed -> rollback ->
    deploy.rolled_back) emits the full ordered event sequence.
  - pe.deploy.receiver node path: node-routed deploys emit started/succeeded.
  - prismatic.gateway.ipc_bridge: the 4 deploy types validate; the /ws
    forwarder relays deploy.* and drops everything else, never raising.
"""

import asyncio
import json
import os
import socket
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest

os.environ["PRISMATIC_ALLOW_DEFAULT_HMAC"] = "1"

from pe.deploy.config import DeployRepoConfig, load_repo_registry  # noqa: E402
from pe.deploy.deploy_events import (  # noqa: E402
    DEPLOY_EVENT_TYPES,
    build_deploy_event,
    default_ipc_socket_path,
    emit_deploy_event,
)
from pe.deploy.gateway_redeploy import (  # noqa: E402
    GatewayDeployResult,
    GatewayRedeployer,
)
from pe.deploy.node_executor import NodeDeployResult  # noqa: E402
from pe.deploy.receiver import DeployReceiverPipeline  # noqa: E402
from prismatic.gateway.event_bus import SwarmEvent  # noqa: E402
from prismatic.gateway.ipc_bridge import (  # noqa: E402
    deploy_event_ws_forwarder,
    validate_event,
)


# ---------------------------------------------------------------- helpers


class FakeBridgeServer:
    """Minimal stand-in for the gateway's UnixSocketListener.

    Accepts connections, parses one NDJSON event per connection, records
    it, and replies with the {"status":"ok"} ACK the real bridge sends.
    """

    def __init__(self, sock_path):
        self.sock_path = Path(sock_path)
        self.events = []
        self._listen_sock = None
        self._thread = None

    def start(self):
        if self.sock_path.exists():
            self.sock_path.unlink()
        self._listen_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listen_sock.bind(str(self.sock_path))
        self._listen_sock.listen(10)
        self._listen_sock.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                line = data.decode("utf-8").strip()
                if line:
                    self.events.append(json.loads(line))
                conn.sendall(b'{"status":"ok"}\n')
            except Exception:
                pass
            finally:
                conn.close()

    def stop(self):
        if self._listen_sock is not None:
            self._listen_sock.close()
            self._listen_sock = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


@pytest.fixture
def bridge_server(tmp_path, monkeypatch):
    """Fake IPC bridge; the sender is pointed at it via env override."""
    server = FakeBridgeServer(tmp_path / "ipc_bridge.sock")
    monkeypatch.setenv("PRISMATIC_IPC_BRIDGE_SOCK", str(server.sock_path))
    server.start()
    yield server
    server.stop()


class StubRunner:
    def __init__(self, tmp_path):
        self.dry_run = False
        self.release_symlink = tmp_path / "releases" / "prismatic-engine"

    def deploy(self, source_repo, pr_sha, branch="main"):
        d = Path(source_repo) / "versions" / f"prismatic-engine-{pr_sha[:8]}"
        d.mkdir(parents=True, exist_ok=True)
        return True, d, ""


class StubHealth:
    def check(self, version_dir=None, release_symlink=None, dry_run=False):
        return {"passed": True, "checks": {}, "details": {}}


class StubTransitioner:
    def transition_issues_for_deploy(self, **kwargs):
        return []


class StubStore:
    def __init__(self):
        self.records = []

    def record_deploy(self, record):
        self.records.append(record)


class StubRedeployer:
    def __init__(self, result):
        self._result = result

    def redeploy(self, pr_sha, repo, dry_run=False, repo_config=None):
        return self._result


class FakeRun:
    """Scripted subprocess double (same shape as test_deploy_alerts)."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "is-active" in argv:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


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
    """Redeployer with git/wheel/venv/health steps scripted."""

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


def _repo_config(tmp_path):
    return DeployRepoConfig(
        full_name="acme/widgets",
        mirror_dir=tmp_path / "mirror-missing",
        release_prefix="widgets",
        target_service="widgets.service",
    )


def _local_pipeline(tmp_path, gateway_redeployer, **kw):
    repo_cfg = _repo_config(tmp_path)
    pipeline = DeployReceiverPipeline(
        source_repo=tmp_path,
        deploy_runner=StubRunner(tmp_path),
        health_checker=StubHealth(),
        transitioner=StubTransitioner(),
        store=StubStore(),
        gateway_redeployer=gateway_redeployer,
        repo_config=repo_cfg,
        **kw,
    )
    return pipeline, repo_cfg


def _payload(**kw):
    base = {
        "pr_sha": "b" * 40,
        "pr_number": 7,
        "pr_title": "test deploy",
        "deployer": "pytest",
    }
    base.update(kw)
    return base


# ------------------------------------------------------------ event schema


def test_event_schema_keys_and_timestamp():
    e = build_deploy_event("deploy.started", {"deploy_id": "deploy-abc"})
    assert set(e) == {"type", "source", "timestamp", "payload"}
    assert e["type"] == "deploy.started"
    assert e["source"] == "deploy-receiver"
    datetime.fromisoformat(e["timestamp"])  # parses as ISO-8601
    assert e["payload"] == {"deploy_id": "deploy-abc"}


def test_deploy_event_types_constant():
    assert tuple(DEPLOY_EVENT_TYPES) == (
        "deploy.started",
        "deploy.succeeded",
        "deploy.failed",
        "deploy.rolled_back",
    )


def test_build_rejects_unknown_type():
    with pytest.raises(ValueError):
        build_deploy_event("deploy.bogus", {})


def test_emit_refuses_unknown_type(bridge_server):
    assert emit_deploy_event("deploy.bogus", {}) is False
    assert bridge_server.events == []


# ------------------------------------------------------- socket resolution


def test_socket_path_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISMATIC_IPC_BRIDGE_SOCK", raising=False)
    monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
    # 1. explicit override wins
    monkeypatch.setenv("PRISMATIC_IPC_BRIDGE_SOCK", "/x/sock")
    assert default_ipc_socket_path() == Path("/x/sock")
    # 2. state dir next (converges with the live gateway)
    monkeypatch.delenv("PRISMATIC_IPC_BRIDGE_SOCK")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    assert default_ipc_socket_path() == tmp_path / "state" / "ipc_bridge.sock"
    # 3. deploy-pipeline home fallback (never CWD-relative)
    monkeypatch.delenv("PRISMATIC_STATE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_ipc_socket_path() == (
        tmp_path / "home" / ".prismatic" / "db" / "ipc_bridge.sock"
    )


# ------------------------------------------------------------------ sender


def test_emit_roundtrip_against_fake_bridge(bridge_server):
    ok = emit_deploy_event("deploy.started", {"deploy_id": "deploy-abc123"})
    assert ok is True
    (event,) = bridge_server.events
    assert set(event) == {"type", "source", "timestamp", "payload"}
    assert event["type"] == "deploy.started"
    assert event["source"] == "deploy-receiver"
    datetime.fromisoformat(event["timestamp"])
    assert event["payload"]["deploy_id"] == "deploy-abc123"


def test_emit_absent_socket_returns_false_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_IPC_BRIDGE_SOCK", str(tmp_path / "nope.sock"))
    assert emit_deploy_event("deploy.started", {"deploy_id": "x"}) is False


# ------------------------------------------------- pipeline: success path


def test_pipeline_success_emits_started_then_succeeded(
    tmp_path, monkeypatch, bridge_server
):
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    gw_res = GatewayDeployResult(success=True, pr_sha="b" * 40, reason="ok")
    pipeline, repo_cfg = _local_pipeline(tmp_path, StubRedeployer(gw_res))

    record = pipeline.process_deploy(_payload(), repo_config=repo_cfg)

    assert record.success is True
    assert [e["type"] for e in bridge_server.events] == [
        "deploy.started",
        "deploy.succeeded",
    ]
    started, succeeded = bridge_server.events

    assert started["payload"]["deploy_id"] == record.deploy_id == "deploy-" + "b" * 8
    assert started["payload"]["repo"] == "acme/widgets"
    assert started["payload"]["step"] == "pipeline"
    assert started["payload"]["pr_sha"] == "b" * 40
    assert started["payload"]["pr_number"] == 7

    assert succeeded["payload"]["deploy_id"] == record.deploy_id
    assert succeeded["payload"]["repo"] == "acme/widgets"
    assert succeeded["payload"]["success"] is True
    assert succeeded["payload"]["duration_ms"] >= 0
    assert succeeded["payload"]["version_dir"] == "prismatic-engine-" + "b" * 8


def test_pipeline_dry_run_emits_nothing(tmp_path, monkeypatch, bridge_server):
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    gw_res = GatewayDeployResult(success=True, pr_sha="b" * 40)
    pipeline, repo_cfg = _local_pipeline(tmp_path, StubRedeployer(gw_res))

    record = pipeline.process_deploy(_payload(dry_run=True), repo_config=repo_cfg)

    assert record.success is True
    assert bridge_server.events == []


def test_pipeline_refusal_emits_nothing(tmp_path, monkeypatch, bridge_server):
    """A refused deploy never started a lifecycle: no bus events.

    The refusal is still alert-logged as PostMergeDeployFailed (existing
    behavior, unchanged).
    """
    repos_file = tmp_path / "repos.json"
    repos_file.write_text(
        json.dumps(
            {
                "acme/widgets": {
                    "mirror_dir": str(tmp_path / "m1"),
                    "release_prefix": "widgets",
                    "target_service": "widgets.service",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(repos_file))
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    repo_cfg = load_repo_registry().get("acme/widgets")
    pipeline = DeployReceiverPipeline(
        source_repo=tmp_path,
        deploy_runner=StubRunner(tmp_path),
        health_checker=StubHealth(),
        transitioner=StubTransitioner(),
        store=StubStore(),
        gateway_redeployer=StubRedeployer(GatewayDeployResult()),
        repo_config=repo_cfg,
    )

    record = pipeline.process_deploy(_payload(repository="unknown/repo"))

    assert record.success is False
    assert bridge_server.events == []
    assert "PostMergeDeployFailed" in (tmp_path / "alerts.log").read_text(
        encoding="utf-8"
    )


# ------------------------------------------------- pipeline: failure path


def test_gateway_failure_emits_full_event_sequence(
    tmp_path, monkeypatch, bridge_server, gw_home
):
    """The pilot's proven failure mode, as bus events.

    Broken release -> health check fails -> deploy.failed ->
    rollback -> deploy.rolled_back, in order, mirroring the alert-log
    sequence GatewayDeployHealthCheckFailed / GatewayDeployFailed /
    GatewayRollbackStarted / GatewayRollbackCompleted.
    """
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    redeployer = ScriptedRedeployer(
        home=gw_home, run=FakeRun(), lock_timeout_s=30, health_ok=False
    )
    pipeline, repo_cfg = _local_pipeline(tmp_path, redeployer)

    record = pipeline.process_deploy(_payload(), repo_config=repo_cfg)

    assert record.success is False
    seq = [
        (e["type"], e["payload"].get("step"), e["payload"].get("phase"))
        for e in bridge_server.events
    ]
    assert seq == [
        ("deploy.started", "pipeline", None),
        ("deploy.failed", "gateway-redeploy", None),
        ("deploy.rolled_back", "gateway-redeploy", "started"),
        ("deploy.rolled_back", "gateway-redeploy", "completed"),
        ("deploy.failed", "pipeline", None),
    ]

    gw_failed = bridge_server.events[1]["payload"]
    assert gw_failed["pr_sha"] == "b" * 40
    assert gw_failed["attempted_release"] == "widgets-" + "b" * 40
    assert "simulated failure" in gw_failed["failure_reason"]

    rb_started = bridge_server.events[2]["payload"]
    assert rb_started["failed_release"] == "widgets-" + "b" * 40
    assert rb_started["previous_release"].endswith("prismatic-engine-" + "a" * 40)

    rb_completed = bridge_server.events[3]["payload"]
    assert rb_completed["restored_release"].endswith("prismatic-engine-" + "a" * 40)

    terminal = bridge_server.events[4]["payload"]
    assert terminal["deploy_id"] == record.deploy_id
    assert terminal["success"] is False
    assert terminal["rolled_back"] is True
    assert "GATEWAY REDEPLOY FAILED" in terminal["failure_reason"]


# ------------------------------------------------------- node-routed path


class FakeNodeDeployer:
    def __init__(self, node, repo, deploy_ok=True):
        self.node = node
        self.repo = repo
        self.deploy_ok = deploy_ok

    def check_reachable(self):
        return {"ok": True, "address": "100.99.0.2"}

    def deploy(self, pr_sha, dry_run=False):
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


@pytest.fixture
def node_env(tmp_path, monkeypatch):
    """Hermetic registry with a node-targeted repo + node registry."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    monkeypatch.delenv("PRISMATIC_ALLOW_DEFAULT_HMAC", raising=False)
    monkeypatch.delenv("PRISMATIC_STRICT_SECRETS", raising=False)
    monkeypatch.delenv("DEPLOY_HMAC_SECRET", raising=False)

    repos = {
        "acme/widgets": {
            "mirror_dir": str(tmp_path / "m1"),
            "release_prefix": "widgets",
            "target_service": "widgets.service",
            "target_node": "athens",
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
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(tmp_path / "alerts.log"))
    monkeypatch.setenv("PRISMATIC_DEPLOY_DB", str(tmp_path / "deploy_records.json"))
    return tmp_path


def test_node_deploy_emits_started_and_succeeded(node_env, bridge_server):
    pipeline = DeployReceiverPipeline(
        source_repo=node_env,
        deploy_runner=StubRunner(node_env),
        health_checker=StubHealth(),
        transitioner=StubTransitioner(),
        store=StubStore(),
        gateway_redeployer=StubRedeployer(GatewayDeployResult(success=True)),
        node_deployer_factory=lambda node, repo: FakeNodeDeployer(node, repo),
    )
    record = pipeline.process_deploy(_payload(repository="acme/widgets"))

    assert record.success is True
    assert [e["type"] for e in bridge_server.events] == [
        "deploy.started",
        "deploy.succeeded",
    ]
    started, succeeded = bridge_server.events
    assert started["payload"]["node"] == "athens"
    assert started["payload"]["deploy_id"] == record.deploy_id
    assert "node-athens" in record.deploy_id
    assert succeeded["payload"]["node"] == "athens"
    assert succeeded["payload"]["success"] is True


# ------------------------------------------------- gateway: bridge + /ws


def test_validate_event_accepts_deploy_types():
    for event_type in DEPLOY_EVENT_TYPES:
        ok, reason = validate_event({"type": event_type, "source": "deploy-receiver"})
        assert ok, f"{event_type} rejected: {reason}"
    ok, _ = validate_event({"type": "deploy.bogus", "source": "x"})
    assert not ok


def test_forwarder_relays_only_deploy_events():
    sent = []

    async def fake_broadcast(message):
        sent.append(message)

    handler = deploy_event_ws_forwarder(fake_broadcast)

    async def run():
        await handler(SwarmEvent("deploy.started", "deploy-receiver", {"a": 1}))
        await handler(SwarmEvent("lock", "swarmlock", {}))
        await handler(SwarmEvent("deploy.failed", "deploy-receiver", {"b": 2}))

    asyncio.run(run())

    assert [m["type"] for m in sent] == ["deploy.started", "deploy.failed"]
    assert sent[0]["payload"] == {"a": 1}
    assert sent[0]["source"] == "deploy-receiver"
    assert "timestamp" in sent[0]


def test_forwarder_never_raises_on_broadcast_failure():
    async def bad_broadcast(message):
        raise RuntimeError("boom")

    handler = deploy_event_ws_forwarder(bad_broadcast)
    asyncio.run(handler(SwarmEvent("deploy.failed", "deploy-receiver", {})))
