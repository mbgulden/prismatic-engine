"""Tests for the WS7 tailnet node registry (pe.deploy.nodes).

Hermetic: no real tailnet, no real SSH. Mesh reachability is exercised
through a fake mesh client.
"""

import json
import os
import stat

import pytest

from pe.deploy import nodes
from pe.deploy.nodes import (
    DeployNode,
    UnresolvableNodeError,
    UnknownNodeError,
    append_node,
    load_node_registry,
    nodes_file_path,
    ping_node,
    resolve_address,
)


class FakeTailscaleNode:
    def __init__(self, name, dns_name, ips, hostname=""):
        self.id = "node-" + name
        self.name = name
        self.dns_name = dns_name
        self.tailscale_ips = ips
        self.os = "linux"
        self.hostname = hostname or name
        self.online = True
        self.active = True
        self.is_self = False
        self.tags = []


class FakeMeshClient:
    def __init__(self, peers=(), ping_ok=True):
        self._peers = list(peers)
        self.ping_ok = ping_ok
        self.list_calls = 0

    def list_nodes_sync(self):
        self.list_calls += 1
        return list(self._peers)

    async def ping(self, target, count=1):
        if self.ping_ok:
            return {"target": target, "success": True, "rtt_ms": 3.0}
        return {"target": target, "success": False, "error": "no pong"}


def _write_nodes_file(path, entries):
    path.write_text(
        "\n".join(
            ["nodes:"]
            + [
                f"  - name: {e['name']}\n"
                f"    address: {e['address']}\n"
                f"    ssh_user: {e['ssh_user']}\n"
                + "".join(
                    f"    {k}: {v}\n"
                    for k, v in e.items()
                    if k not in ("name", "address", "ssh_user")
                )
                for e in entries
            ]
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def hermetic_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PRISMATIC_NODES_FILE", raising=False)
    return tmp_path


@pytest.fixture
def registry_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_NODES_FILE", str(tmp_path / "nodes.yaml"))
    return tmp_path / "nodes.yaml"


class TestLoadValidate:
    def test_load_yaml_registry(self, registry_file):
        _write_nodes_file(
            registry_file,
            [
                {
                    "name": "athens",
                    "address": "100.99.0.2",
                    "ssh_user": "ubuntu",
                },
                {
                    "name": "sparta",
                    "address": "sparta.tailnet.ts.net",
                    "ssh_user": "ops",
                    "ssh_method": "key",
                    "ssh_key": "~/.ssh/sparta",
                    "roles": ["gateway", "runner"],
                },
            ],
        )
        reg = load_node_registry()
        assert len(reg) == 2
        athens = reg.get("athens")
        assert athens.address == "100.99.0.2"
        assert athens.ssh_user == "ubuntu"
        assert athens.ssh_method == "tailscale-ssh"
        assert athens.platform == "linux-systemd"
        sparta = reg.get("sparta")
        assert sparta.ssh_method == "key"
        assert str(sparta.ssh_key).endswith(".ssh/sparta")
        assert sparta.roles == ("gateway", "runner")

    def test_json_content_parses(self, registry_file):
        registry_file.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "name": "athens",
                            "address": "100.99.0.2",
                            "ssh_user": "ubuntu",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        reg = load_node_registry()
        assert reg.get("athens").ssh_user == "ubuntu"

    def test_missing_file_means_no_remote_nodes(self, registry_file):
        assert not registry_file.exists()
        reg = load_node_registry()
        assert len(reg) == 0
        # "local" still resolves.
        assert reg.get("local").is_local

    def test_default_path_under_prismatic(self, hermetic_home):
        assert nodes_file_path() == hermetic_home / ".prismatic" / "nodes.yaml"

    def test_invalid_entries_rejected(self, registry_file):
        for bad in [
            [{"name": "Bad_Name", "address": "100.99.0.2", "ssh_user": "u"}],
            [{"name": "athens", "address": "100.99.0.2"}],  # missing ssh_user
            [
                {
                    "name": "athens",
                    "address": "100.99.0.2",
                    "ssh_user": "u",
                    "ssh_method": "password",
                }
            ],
            [
                {
                    "name": "athens",
                    "address": "100.99.0.2",
                    "ssh_user": "u",
                    "ssh_method": "key",
                }
            ],  # key method without ssh_key
            [
                {"name": "a", "address": "1.2.3.4", "ssh_user": "u"},
                {"name": "a", "address": "5.6.7.8", "ssh_user": "u"},
            ],  # duplicate
            [{"name": "local", "address": "1.2.3.4", "ssh_user": "u"}],  # reserved
        ]:
            registry_file.write_text(
                "nodes:\n"
                + "".join(
                    f"  - name: {e.get('name')}\n"
                    + "".join(f"    {k}: {v}\n" for k, v in e.items() if k != "name")
                    for e in bad
                ),
                encoding="utf-8",
            )
            with pytest.raises(Exception):
                load_node_registry()

    def test_unknown_node_raises_naming_registry(self, registry_file):
        _write_nodes_file(
            registry_file,
            [{"name": "athens", "address": "100.99.0.2", "ssh_user": "ubuntu"}],
        )
        reg = load_node_registry()
        with pytest.raises(UnknownNodeError) as excinfo:
            reg.get("nowhere")
        assert "nowhere" in str(excinfo.value)
        assert "athens" in str(excinfo.value)


class TestResolve:
    def test_tailscale_ip_passes_through_without_mesh(self, registry_file):
        mesh = FakeMeshClient()
        node = DeployNode(name="athens", address="100.99.0.2", ssh_user="ubuntu")
        assert resolve_address(node, mesh) == "100.99.0.2"
        assert mesh.list_calls == 0  # no mesh call for a Tailscale IP

    def test_magicdns_resolved_via_mesh(self):
        mesh = FakeMeshClient(
            [FakeTailscaleNode("athens", "athens.tailnet.ts.net", ["100.99.0.2"])]
        )
        node = DeployNode(name="athens", address="athens", ssh_user="ubuntu")
        assert resolve_address(node, mesh) == "100.99.0.2"

    def test_unresolvable_address_refused(self):
        mesh = FakeMeshClient([])
        node = DeployNode(name="athens", address="ghost", ssh_user="ubuntu")
        with pytest.raises(UnresolvableNodeError) as excinfo:
            resolve_address(node, mesh)
        assert "ghost" in str(excinfo.value)

    def test_non_tailscale_ip_refused(self):
        mesh = FakeMeshClient([])
        node = DeployNode(name="athens", address="203.0.113.9", ssh_user="ubuntu")
        with pytest.raises(UnresolvableNodeError):
            resolve_address(node, mesh)

    def test_local_resolves_without_mesh(self):
        mesh = FakeMeshClient()
        assert resolve_address(DeployNode.local(), mesh) == "localhost"
        assert mesh.list_calls == 0

    def test_ping_node_ok_and_unreachable(self):
        ok_mesh = FakeMeshClient()
        node = DeployNode(name="athens", address="100.99.0.2", ssh_user="ubuntu")
        assert ping_node(node, ok_mesh)["success"] is True
        bad_mesh = FakeMeshClient(ping_ok=False)
        assert ping_node(node, bad_mesh)["success"] is False
        # Unresolvable address: ping reports failure, never raises.
        ghost = DeployNode(name="ghost", address="ghost", ssh_user="u")
        res = ping_node(ghost, FakeMeshClient([]))
        assert res["success"] is False


class TestAppend:
    def test_append_creates_file_0600(self, registry_file):
        node = DeployNode(name="athens", address="100.99.0.2", ssh_user="ubuntu")
        written = append_node(registry_file, node)
        assert written == registry_file
        assert load_node_registry(registry_file).get("athens").address == "100.99.0.2"
        mode = stat.S_IMODE(os.stat(registry_file).st_mode)
        assert mode == 0o600

    def test_append_preserves_existing(self, registry_file):
        _write_nodes_file(
            registry_file,
            [{"name": "athens", "address": "100.99.0.2", "ssh_user": "ubuntu"}],
        )
        append_node(
            registry_file,
            DeployNode(name="sparta", address="100.99.0.3", ssh_user="ops"),
        )
        reg = load_node_registry(registry_file)
        assert reg.get("athens").address == "100.99.0.2"
        assert reg.get("sparta").address == "100.99.0.3"

    def test_append_refuses_duplicates(self, registry_file):
        _write_nodes_file(
            registry_file,
            [{"name": "athens", "address": "100.99.0.2", "ssh_user": "ubuntu"}],
        )
        with pytest.raises(nodes.NodeRegistryError):
            append_node(
                registry_file,
                DeployNode(name="athens", address="100.99.0.9", ssh_user="ops"),
            )
        # The original entry is untouched.
        assert load_node_registry(registry_file).get("athens").address == "100.99.0.2"


class TestEnrollNode:
    def _options(self, **kw):
        from pe.deploy.install import EnrollNodeOptions

        base = dict(
            name="athens",
            address="100.99.0.2",
            ssh_user="ubuntu",
            ssh_method="tailscale-ssh",
            platform="linux-systemd",
            roles=("gateway",),
            confirm=True,
        )
        base.update(kw)
        return EnrollNodeOptions(**base)

    def _ok_mesh(self):
        return FakeMeshClient()

    def test_dry_run_reports_plan_without_writing(self, registry_file):
        from pe.deploy.install import enroll_node

        result = enroll_node(
            self._options(dry_run=True, confirm=False),
            mesh_client=self._ok_mesh(),
            probe=lambda node, address, timeout_s=10: True,
            run_remote=lambda node, address, argv, timeout=None: _Completed(
                0, "Linux\n"
            ),
        )
        assert result.ok is True
        assert any(s.status == "would-do" for s in result.steps)
        assert not registry_file.exists()  # nothing written

    def test_confirm_required_never_auto_enrolls(self, registry_file):
        from pe.deploy.install import InstallRefused, enroll_node

        with pytest.raises(InstallRefused):
            enroll_node(
                self._options(confirm=False),
                mesh_client=self._ok_mesh(),
                probe=lambda node, address, timeout_s=10: True,
                run_remote=lambda node, address, argv, timeout=None: _Completed(
                    0, "Linux\n" if argv[-1] == "-s" else "/usr/bin/systemctl\n"
                ),
            )
        assert not registry_file.exists()

    def test_unreachable_node_refused_before_write(self, registry_file):
        from pe.deploy.install import InstallError, enroll_node

        with pytest.raises(InstallError) as excinfo:
            enroll_node(
                self._options(),
                mesh_client=self._ok_mesh(),
                probe=lambda node, address, timeout_s=10: False,  # ssh dead
                run_remote=lambda node, address, argv, timeout=None: _Completed(0, ""),
            )
        assert "ssh probe failed" in str(excinfo.value)
        assert not registry_file.exists()  # nothing written

    def test_platform_mismatch_refused(self, registry_file):
        from pe.deploy.install import InstallError, enroll_node

        with pytest.raises(InstallError) as excinfo:
            enroll_node(
                self._options(),
                mesh_client=self._ok_mesh(),
                probe=lambda node, address, timeout_s=10: True,
                run_remote=lambda node, address, argv, timeout=None: _Completed(
                    0, "Darwin\n"
                ),
            )
        assert "Darwin" in str(excinfo.value)
        assert not registry_file.exists()

    def test_successful_enroll_writes_registry(self, registry_file):
        from pe.deploy.install import enroll_node

        result = enroll_node(
            self._options(),
            mesh_client=self._ok_mesh(),
            probe=lambda node, address, timeout_s=10: True,
            run_remote=lambda node, address, argv, timeout=None: _Completed(
                0, "Linux\n" if argv[-1] == "-s" else "/usr/bin/systemctl\n"
            ),
        )
        assert result.ok is True
        reg = load_node_registry(registry_file)
        assert reg.get("athens").ssh_user == "ubuntu"

    def test_reserved_local_name_rejected(self, registry_file):
        from pe.deploy.install import InstallError, enroll_node

        with pytest.raises(InstallError):
            enroll_node(self._options(name="local"), mesh_client=self._ok_mesh())


class _Completed:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""
