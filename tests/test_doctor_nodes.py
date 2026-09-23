"""Tests for the WS7 doctor ``nodes`` check (prismatic.doctor).

Hermetic: the mesh client and the ssh probe are monkeypatched; no real
tailnet, no real SSH.
"""

import pytest

import prismatic.doctor as doctor
from prismatic.doctor import (
    DeployNodeCheck,
    DeployReport,
    _deploy_section_ok,
    _probe_deploy_nodes,
)


class FakeMeshNode:
    def __init__(self, name, dns_name, ips):
        self.name = name
        self.dns_name = dns_name
        self.tailscale_ips = ips
        self.hostname = name
        self.online = True


class FakeMeshClient:
    def __init__(self, peers=(), ping_ok=True):
        self._peers = list(peers)
        self.ping_ok = ping_ok

    def list_nodes_sync(self):
        return list(self._peers)

    async def ping(self, target, count=1):
        if self.ping_ok:
            return {"target": target, "success": True, "rtt_ms": 2.0}
        return {"target": target, "success": False, "error": "no pong"}


@pytest.fixture
def nodes_file(tmp_path, monkeypatch):
    path = tmp_path / "nodes.yaml"
    path.write_text(
        "nodes:\n  - name: athens\n    address: 100.99.0.2\n    ssh_user: ubuntu\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRISMATIC_NODES_FILE", str(path))
    return path


@pytest.fixture
def mesh_ok(monkeypatch):
    import prismatic.mesh.tailscale as tailscale

    monkeypatch.setattr(
        tailscale,
        "get_tailscale_mesh_client",
        lambda: FakeMeshClient(),
    )


@pytest.fixture
def ssh_ok(monkeypatch):
    monkeypatch.setattr(
        doctor, "_probe_deploy_node_ssh", lambda node, address, timeout_s=10: True
    )


class TestProbeDeployNodes:
    def test_all_green(self, nodes_file, mesh_ok, ssh_ok):
        (checks,) = _probe_deploy_nodes()
        assert checks.name == "athens"
        assert checks.ok is True
        assert checks.resolvable and checks.mesh_reachable and checks.ssh_reachable
        assert checks.remediation == ""

    def test_ssh_failure_names_the_node(self, nodes_file, mesh_ok, monkeypatch):
        monkeypatch.setattr(
            doctor, "_probe_deploy_node_ssh", lambda node, address, timeout_s=10: False
        )
        (checks,) = _probe_deploy_nodes()
        assert checks.ok is False
        assert checks.ssh_reachable is False
        assert "athens" in checks.remediation
        assert "ubuntu@100.99.0.2" in checks.remediation

    def test_mesh_down_names_every_node(self, nodes_file, monkeypatch, ssh_ok):
        import prismatic.mesh.tailscale as tailscale

        def _boom():
            raise RuntimeError("tailscale not running")

        monkeypatch.setattr(tailscale, "get_tailscale_mesh_client", _boom)
        (checks,) = _probe_deploy_nodes()
        assert checks.ok is False
        assert "athens" in checks.remediation

    def test_unresolvable_address_names_the_node(
        self, tmp_path, monkeypatch, mesh_ok, ssh_ok
    ):
        path = tmp_path / "nodes.yaml"
        path.write_text(
            "nodes:\n  - name: ghost\n    address: ghost\n    ssh_user: u\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PRISMATIC_NODES_FILE", str(path))
        (checks,) = _probe_deploy_nodes()
        assert checks.ok is False
        assert checks.resolvable is False
        assert "ghost" in checks.remediation

    def test_no_nodes_no_checks(self, tmp_path, monkeypatch, mesh_ok, ssh_ok):
        path = tmp_path / "nodes.yaml"
        path.write_text("nodes: []\n", encoding="utf-8")
        monkeypatch.setenv("PRISMATIC_NODES_FILE", str(path))
        assert _probe_deploy_nodes() == []

    def test_unreadable_registry_is_one_erroring_check(self, tmp_path, monkeypatch):
        path = tmp_path / "nodes.yaml"
        path.write_text("nodes: [broken\n", encoding="utf-8")
        monkeypatch.setenv("PRISMATIC_NODES_FILE", str(path))
        (checks,) = _probe_deploy_nodes()
        assert checks.ok is False
        assert "unreadable" in checks.remediation


class TestSectionOk:
    def _ok_report(self):
        report = DeployReport(
            installed=True,
            hmac_secret_set=True,
            receiver_unit_installed=True,
            gateway_unit_installed=True,
        )
        report.nodes = [
            DeployNodeCheck(
                name="athens",
                address="100.99.0.2",
                resolvable=True,
                mesh_reachable=True,
                ssh_reachable=True,
            )
        ]
        return report

    def test_green_nodes_keep_section_ok(self):
        assert _deploy_section_ok(self._ok_report()) is True

    def test_failing_node_fails_section(self):
        report = self._ok_report()
        report.nodes[0].ssh_reachable = False
        assert _deploy_section_ok(report) is False

    def test_erroring_node_fails_section(self):
        report = self._ok_report()
        report.nodes = [DeployNodeCheck(name="(registry)", remediation="unreadable")]
        assert _deploy_section_ok(report) is False

    def test_no_nodes_is_neutral(self):
        report = self._ok_report()
        report.nodes = []
        assert _deploy_section_ok(report) is True
