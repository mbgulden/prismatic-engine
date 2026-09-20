"""Unit tests for Dispatcher Node Affinity & Mesh Routing (GRO-4853)."""

import socket
from unittest.mock import patch, MagicMock

import pytest

from prismatic.dispatcher import (
    extract_node_affinity,
    check_node_affinity,
    preflight_assigned_agent,
    AssignedAgentResolution,
)
from prismatic.mesh.tailscale import TailscaleNode


def test_extract_node_affinity_formats():
    assert extract_node_affinity({"node_affinity": "lightbringer-windows"}) == "lightbringer-windows"
    assert extract_node_affinity({"metadata": {"node": "webtop-hermes"}}) == "webtop-hermes"
    assert extract_node_affinity({"data": {"target_node": "custom-node"}}) == "custom-node"
    assert extract_node_affinity({"labels": [{"name": "node:LightBringer"}]}) == "lightbringer"
    assert extract_node_affinity({"labels": ["node-webtop-hermes"]}) == "webtop-hermes"
    assert extract_node_affinity({"labels": ["affinity:my-worker"]}) == "my-worker"
    assert extract_node_affinity({}) is None


def test_check_node_affinity_wildcards():
    for wild in (None, "", "any", "all", "none", "*"):
        ok, reason = check_node_affinity(wild)
        assert ok is True
        assert "any node accepted" in reason


def test_check_node_affinity_mesh_matching():
    online_node = TailscaleNode(
        id="1",
        name="webtop-hermes",
        dns_name="webtop-hermes.tailnet.ts.net",
        tailscale_ips=["100.83.32.92"],
        os="linux",
        hostname="webtop-hermes",
        online=True,
        active=True,
        is_self=True,
    )
    offline_node = TailscaleNode(
        id="2",
        name="backup-worker",
        dns_name="backup-worker.tailnet.ts.net",
        tailscale_ips=["100.99.99.99"],
        os="linux",
        hostname="backup-worker",
        online=False,
        active=False,
        is_self=False,
    )

    with patch("prismatic.mesh.tailscale.get_tailscale_mesh_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.list_nodes_sync.return_value = [online_node, offline_node]
        mock_get_client.return_value = mock_client

        # Online node
        ok, reason = check_node_affinity("webtop-hermes")
        assert ok is True
        assert "is online in mesh" in reason

        # Offline node
        ok, reason = check_node_affinity("backup-worker")
        assert ok is False
        assert "is offline in mesh" in reason

        # Unknown node
        ok, reason = check_node_affinity("non-existent-node-12345")
        assert ok is False
        assert "not found in mesh" in reason


def test_preflight_assigned_agent_blocks_on_offline_node():
    row = {
        "event_id": "evt-test",
        "dispatch_status": "pending",
        "node_affinity": "offline-server",
        "raw_json": '{"node_affinity": "offline-server"}',
    }

    resolution = AssignedAgentResolution(
        status="resolved",
        target_agent="agy",
        routing_source="metadata",
    )

    offline_node = TailscaleNode(
        id="3",
        name="offline-server",
        dns_name="offline-server.tailnet.ts.net",
        tailscale_ips=["100.77.77.77"],
        os="linux",
        hostname="offline-server",
        online=False,
        active=False,
        is_self=False,
    )

    with patch("prismatic.mesh.tailscale.get_tailscale_mesh_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.list_nodes_sync.return_value = [offline_node]
        mock_get_client.return_value = mock_client

        preflight = preflight_assigned_agent(row, resolution)
        assert preflight.allowed is False
        assert preflight.status == "blocked_preflight"
        assert "node affinity check failed" in preflight.reason
