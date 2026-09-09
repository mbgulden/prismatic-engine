"""Phase 2 Verification Suite: Distributed Tailscale Mesh, Node Registry & Whois Middleware.

Covers all 4 Phase 2 engineering invariants:
1. Cross-platform compatibility (Linux socket & CLI fallback, graceful degradation when offline).
2. Async non-blocking /whois with TTL caching and fail-closed timeout.
3. Anti-spoofing reverse proxy exemption (direct socket host validation, header spoof deflection).
4. Live mesh node discovery and inter-node RTT probing (webtop-hermes <-> lightbringer-windows).
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from prismatic.mesh.tailscale import (
    TailscaleMeshClient,
    TailscaleNode,
    TailscalePeerIdentity,
    TailscaleAuthMiddleware,
    is_tailscale_ip,
)
from prismatic.gateway.server import app


# -----------------------------------------------------------------------------
# 1. IP Parsing and Utility Tests
# -----------------------------------------------------------------------------

def test_tailscale_ip_detection():
    # CGNAT IPv4 range (100.64.0.0/10)
    assert is_tailscale_ip("100.83.32.92") is True       # webtop-hermes
    assert is_tailscale_ip("100.93.104.46") is True      # lightbringer-windows
    assert is_tailscale_ip("100.64.0.1") is True
    assert is_tailscale_ip("100.127.255.254") is True

    # Tailscale ULA IPv6 range (fd7a:115c:a1e0::/48)
    assert is_tailscale_ip("fd7a:115c:a1e0::a834:205c") is True

    # Non-Tailscale IPs
    assert is_tailscale_ip("127.0.0.1") is False
    assert is_tailscale_ip("::1") is False
    assert is_tailscale_ip("192.168.1.1") is False
    assert is_tailscale_ip("8.8.8.8") is False
    assert is_tailscale_ip("invalid-ip") is False


# -----------------------------------------------------------------------------
# 2. Cross-Platform Fallback & Offline Resilience (Invariant 1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mesh_client_graceful_offline_degradation():
    """Client must never crash when daemon socket is absent and CLI fails."""
    client = TailscaleMeshClient(socket_paths=[Path("/nonexistent/tailscaled.sock")])
    assert client.get_socket_path() is None

    with patch("shutil.which", return_value=None):
        status = await client.get_status()
        assert status == {"BackendState": "Stopped", "Self": None, "Peer": {}}

        nodes = await client.list_nodes()
        assert nodes == []

        identity = await client.whois("100.93.104.46")
        assert identity is None

        ping_res = await client.ping("100.93.104.46")
        assert ping_res["success"] is False
        assert "binary not found" in ping_res["error"]


@pytest.mark.asyncio
async def test_mesh_client_cli_fallback():
    """When Unix domain socket is absent, falls back to CLI output."""
    client = TailscaleMeshClient(socket_paths=[Path("/nonexistent/tailscaled.sock")])
    mock_cli_json = json_payload = {
        "Self": {
            "ID": "node-1",
            "HostName": "test-host",
            "DNSName": "test-host.tailnet.ts.net.",
            "TailscaleIPs": ["100.64.0.1"],
            "OS": "linux",
        },
        "Peer": {
            "peer-2": {
                "ID": "node-2",
                "HostName": "peer-host",
                "ComputedName": "peer-node",
                "DNSName": "peer-node.tailnet.ts.net.",
                "TailscaleIPs": ["100.64.0.2"],
                "OS": "windows",
                "Online": True,
                "Active": True,
            }
        },
    }

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (json.dumps(mock_cli_json).encode("utf-8"), b"")
    mock_proc.returncode = 0

    with patch("shutil.which", return_value="/fake/tailscale"), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        nodes = await client.list_nodes()
        assert len(nodes) == 2
        self_node = next(n for n in nodes if n.is_self)
        assert self_node.name == "test-host"
        peer_node = next(n for n in nodes if not n.is_self)
        assert peer_node.name == "peer-node"
        assert peer_node.os == "windows"
        assert peer_node.online is True


# -----------------------------------------------------------------------------
# 3. Async Non-Blocking /whois with TTL Caching (Invariant 2)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whois_ttl_caching():
    """Subsequent lookups within TTL must be served from cache without extra calls."""
    client = TailscaleMeshClient(socket_paths=[Path("/nonexistent/tailscaled.sock")], ttl_seconds=60.0)

    cached_identity = TailscalePeerIdentity(
        node_name="lightbringer-windows",
        dns_name="lightbringer-windows.tailnet.ts.net",
        tailscale_ip="100.93.104.46",
        os="windows",
        hostname="LightBringer",
        user_login="mbgulden@gmail.com",
        user_display_name="Michael Gulden",
        verified=True,
        cached_at=time.time(),
    )
    client._cache["100.93.104.46"] = cached_identity

    # With cache populated, whois returns immediately without inspecting disk or CLI
    with patch("shutil.which", side_effect=AssertionError("Should not be called")):
        res = await client.whois("100.93.104.46")
        assert res is not None
        assert res.node_name == "lightbringer-windows"
        assert res.user_login == "mbgulden@gmail.com"


# -----------------------------------------------------------------------------
# 4. Anti-Spoofing Reverse Proxy Exemption & Auth Middleware (Invariant 3)
# -----------------------------------------------------------------------------

def test_middleware_loopback_and_reverse_proxy_exemption():
    """Localhost and reverse proxy callers (127.0.0.1) pass freely without whois."""
    test_app = FastAPI()
    test_app.add_middleware(TailscaleAuthMiddleware)

    @test_app.get("/api/test-secure")
    def secure_endpoint(request: Request):
        return {"status": "ok", "is_loopback": getattr(request.state, "is_loopback", False)}

    client = TestClient(test_app)
    res = client.get("/api/test-secure")
    assert res.status_code == 200
    assert res.json()["is_loopback"] is True


def test_middleware_rejects_untrusted_remote_ip_from_mesh_endpoints():
    """Remote non-loopback callers connecting from unauthorized non-Tailnet IPs are rejected."""
    test_app = FastAPI()
    mock_mesh_client = MagicMock()
    test_app.add_middleware(TailscaleAuthMiddleware, client=mock_mesh_client)

    @test_app.get("/api/mesh/nodes")
    def mesh_nodes():
        return {"nodes": []}

    # Simulate remote untrusted caller connecting from public IP
    client = TestClient(test_app, client=("198.51.100.5", 54321))
    res = client.get("/api/mesh/nodes", headers={"X-Forwarded-For": "127.0.0.1"})
    assert res.status_code == 403
    assert res.json()["error"] == "forbidden"


def test_middleware_authenticates_verified_tailscale_peer():
    """Remote callers connecting from Tailscale IPs must pass whois verification."""
    test_app = FastAPI()
    mock_client = MagicMock()

    mock_identity = TailscalePeerIdentity(
        node_name="lightbringer-windows",
        dns_name="lightbringer-windows.tailnet.ts.net",
        tailscale_ip="100.93.104.46",
        os="windows",
        hostname="LightBringer",
        user_login="mbgulden@gmail.com",
        user_display_name="Michael Gulden",
        verified=True,
    )
    mock_client.whois = AsyncMock(return_value=mock_identity)
    test_app.add_middleware(TailscaleAuthMiddleware, client=mock_client)

    @test_app.get("/api/mesh/nodes")
    def mesh_nodes(request: Request):
        peer = getattr(request.state, "tailscale_peer", None)
        return {"ok": True, "peer_name": peer.node_name if peer else None}

    # Valid Tailscale caller
    client = TestClient(test_app, client=("100.93.104.46", 41641))
    res = client.get("/api/mesh/nodes")
    assert res.status_code == 200
    assert res.json()["peer_name"] == "lightbringer-windows"

    # Unverified Tailscale caller (whois returns None)
    mock_client.whois = AsyncMock(return_value=None)
    res_rejected = client.get("/api/mesh/nodes")
    assert res_rejected.status_code == 403
    assert res_rejected.json()["error"] == "tailscale_auth_failed"


# -----------------------------------------------------------------------------
# 5. Live Gateway Endpoints Integration (Invariant 4)
# -----------------------------------------------------------------------------

def test_live_gateway_mesh_endpoints():
    """Assert /api/mesh/nodes, /api/mesh/whois, and /api/mesh/ping on canonical app."""
    client = TestClient(app)

    # 1. GET /api/mesh/nodes
    res_nodes = client.get("/api/mesh/nodes")
    assert res_nodes.status_code == 200
    nodes_data = res_nodes.json()
    assert nodes_data["ok"] is True
    assert "nodes" in nodes_data
    assert nodes_data["total_nodes"] >= 1
    assert nodes_data["self"]["name"] == "webtop-hermes"

    # 2. GET /api/mesh/whois for Lightbringer
    res_whois = client.get("/api/mesh/whois?addr=100.93.104.46")
    assert res_whois.status_code == 200
    whois_data = res_whois.json()
    assert whois_data["ok"] is True
    assert whois_data["identity"]["node_name"] == "lightbringer-windows"
    assert whois_data["identity"]["os"] == "windows"

    # 3. POST /api/mesh/ping to Lightbringer
    res_ping = client.post("/api/mesh/ping", json={"target": "100.93.104.46"})
    assert res_ping.status_code == 200
    ping_data = res_ping.json()
    assert ping_data["ok"] is True
    assert ping_data["result"]["target"] == "100.93.104.46"
    assert ping_data["result"]["rtt_ms"] is not None
    assert ping_data["result"]["rtt_ms"] > 0
