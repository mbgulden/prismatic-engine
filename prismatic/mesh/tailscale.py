"""Distributed Tailscale Mesh & Universal Node Registry for Prismatic Engine.

Provides cross-platform (Linux & Windows) Tailscale LocalAPI client,
async non-blocking /whois with TTL caching, anti-spoofing reverse proxy exemption,
and distributed mesh node discovery.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("prismatic.mesh.tailscale")

# Tailscale CGNAT IPv4 range (100.64.0.0/10) and ULA IPv6 range (fd7a:115c:a1e0::/48)
_TAILSCALE_IPV4_NET = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_IPV6_NET = ipaddress.ip_network("fd7a:115c:a1e0::/48")

_DEFAULT_SOCKET_PATHS = [
    Path("/run/tailscale/tailscaled.sock"),
    Path("/var/run/tailscale/tailscaled.sock"),
]


def is_tailscale_ip(ip_str: str) -> bool:
    """Determine whether an IP address belongs to the Tailscale subnet."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip in _TAILSCALE_IPV4_NET or ip in _TAILSCALE_IPV6_NET
    except ValueError:
        return False


@dataclass
class TailscalePeerIdentity:
    """Authenticated identity of a Tailscale peer node."""
    node_name: str
    dns_name: str
    tailscale_ip: str
    os: str
    hostname: str
    user_login: str
    user_display_name: str
    verified: bool
    cached_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TailscaleNode:
    """Representation of a node participating in the distributed mesh."""
    id: str
    name: str
    dns_name: str
    tailscale_ips: list[str]
    os: str
    hostname: str
    online: bool
    active: bool
    is_self: bool
    last_seen: str | None = None
    rtt_ms: float | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TailscaleMeshClient:
    """Client for Tailscale daemon LocalAPI with cross-platform fallback."""

    def __init__(
        self,
        socket_paths: list[Path] | None = None,
        ttl_seconds: float = 60.0,
        timeout_seconds: float = 1.0,
    ) -> None:
        self.socket_paths = socket_paths or _DEFAULT_SOCKET_PATHS
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, TailscalePeerIdentity] = {}
        self._cache_lock = asyncio.Lock()

    def get_socket_path(self) -> Path | None:
        """Find an accessible Tailscale LocalAPI Unix domain socket."""
        for path in self.socket_paths:
            if path.is_socket():
                return path
        return None

    async def get_status(self) -> dict[str, Any]:
        """Fetch status dictionary from Tailscale LocalAPI or CLI."""
        sock_path = self.get_socket_path()
        if sock_path is not None:
            try:
                transport = httpx.AsyncHTTPTransport(uds=str(sock_path))
                async with httpx.AsyncClient(transport=transport, timeout=self.timeout_seconds) as client:
                    resp = await client.get("http://local-tailscaled.sock/localapi/v0/status")
                    if resp.status_code == 200:
                        return resp.json()
            except Exception as exc:
                logger.debug("LocalAPI socket status query failed (%s), trying CLI fallback", exc)

        # Fallback to tailscale CLI
        cli_bin = shutil.which("tailscale")
        if cli_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    cli_bin, "status", "--json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds + 1.0)
                if proc.returncode == 0 and stdout:
                    return json.loads(stdout.decode("utf-8"))
            except Exception as exc:
                logger.debug("Tailscale CLI status failed: %s", exc)

        return {"BackendState": "Stopped", "Self": None, "Peer": {}}

    async def list_nodes(self) -> list[TailscaleNode]:
        """List all discovered nodes in the Tailnet mesh with online status."""
        status = await self.get_status()
        nodes: list[TailscaleNode] = []

        # Parse Self node
        self_data = status.get("Self")
        if self_data:
            self_node = TailscaleNode(
                id=str(self_data.get("ID", "")),
                name=self_data.get("HostName", "self"),
                dns_name=self_data.get("DNSName", "").rstrip("."),
                tailscale_ips=self_data.get("TailscaleIPs", []),
                os=self_data.get("OS", "unknown"),
                hostname=self_data.get("HostName", ""),
                online=True,
                active=True,
                is_self=True,
                tags=self_data.get("Tags") or [],
            )
            nodes.append(self_node)

        # Parse Peer nodes
        peers = status.get("Peer") or {}
        for peer_id, pdata in peers.items():
            hostname = pdata.get("HostName") or ""
            computed_name = pdata.get("ComputedName") or hostname
            dns_name = (pdata.get("DNSName") or "").rstrip(".")
            ips = pdata.get("TailscaleIPs") or []
            os_type = pdata.get("OS") or "unknown"
            online = bool(pdata.get("Online", False))
            active = bool(pdata.get("Active", False))
            last_seen = pdata.get("LastSeen")
            tags = pdata.get("Tags") or []

            peer_node = TailscaleNode(
                id=str(pdata.get("ID", peer_id)),
                name=computed_name,
                dns_name=dns_name,
                tailscale_ips=ips,
                os=os_type,
                hostname=hostname,
                online=online,
                active=active,
                is_self=False,
                last_seen=last_seen,
                tags=tags,
            )
            nodes.append(peer_node)

        return nodes

    def list_nodes_sync(self) -> list[TailscaleNode]:
        """Synchronous wrapper around list_nodes for non-async contexts."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(lambda: asyncio.run(self.list_nodes()))
                    return future.result(timeout=self.timeout_seconds + 2.0)
            else:
                return loop.run_until_complete(self.list_nodes())
        except RuntimeError:
            return asyncio.run(self.list_nodes())

    async def whois(self, addr: str) -> TailscalePeerIdentity | None:
        """Resolve IP address to authenticated Tailscale peer identity with TTL caching."""
        # Strip port if present
        clean_addr = addr.split(":")[0].strip("[]")

        # 1. Check TTL Cache
        now = time.time()
        async with self._cache_lock:
            cached = self._cache.get(clean_addr)
            if cached and (now - cached.cached_at) < self.ttl_seconds:
                return cached

        # 2. Query LocalAPI Unix Domain Socket
        sock_path = self.get_socket_path()
        if sock_path is not None:
            try:
                transport = httpx.AsyncHTTPTransport(uds=str(sock_path))
                async with httpx.AsyncClient(transport=transport, timeout=self.timeout_seconds) as client:
                    resp = await client.get(f"http://local-tailscaled.sock/localapi/v0/whois?addr={clean_addr}")
                    if resp.status_code == 200:
                        data = resp.json()
                        node = data.get("Node") or {}
                        user = data.get("UserProfile") or {}
                        hostinfo = node.get("Hostinfo") or {}

                        identity = TailscalePeerIdentity(
                            node_name=node.get("ComputedName") or node.get("Name", "unknown").rstrip("."),
                            dns_name=node.get("Name", "").rstrip("."),
                            tailscale_ip=clean_addr,
                            os=hostinfo.get("OS") or node.get("OS", "unknown"),
                            hostname=hostinfo.get("Hostname") or node.get("HostName", ""),
                            user_login=user.get("LoginName", ""),
                            user_display_name=user.get("DisplayName", ""),
                            verified=True,
                            cached_at=now,
                        )
                        async with self._cache_lock:
                            self._cache[clean_addr] = identity
                        return identity
            except Exception as exc:
                logger.debug("LocalAPI socket whois query failed (%s), trying CLI fallback", exc)

        # 3. Fallback to tailscale whois CLI
        cli_bin = shutil.which("tailscale")
        if cli_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    cli_bin, "whois", clean_addr,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds + 1.0)
                if proc.returncode == 0 and stdout:
                    output = stdout.decode("utf-8")
                    name_match = re.search(r"Name:\s+([^\n]+)", output)
                    user_match = re.search(r"User:\s+Name:\s+([^\n]+)", output)
                    if name_match:
                        raw_name = name_match.group(1).strip().rstrip(".")
                        user_login = user_match.group(1).strip() if user_match else ""
                        identity = TailscalePeerIdentity(
                            node_name=raw_name.split(".")[0],
                            dns_name=raw_name,
                            tailscale_ip=clean_addr,
                            os="unknown",
                            hostname=raw_name.split(".")[0],
                            user_login=user_login,
                            user_display_name=user_login,
                            verified=True,
                            cached_at=now,
                        )
                        async with self._cache_lock:
                            self._cache[clean_addr] = identity
                        return identity
            except Exception as exc:
                logger.debug("Tailscale CLI whois failed: %s", exc)

        return None

    async def ping(self, target: str, count: int = 1) -> dict[str, Any]:
        """Measure round-trip time (RTT) to a peer node using tailscale ping."""
        cli_bin = shutil.which("tailscale")
        if not cli_bin:
            return {"target": target, "success": False, "error": "tailscale CLI binary not found"}

        try:
            proc = await asyncio.create_subprocess_exec(
                cli_bin, "ping", f"-c={count}", target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            out_str = stdout.decode("utf-8") + stderr.decode("utf-8")

            # Parse e.g. "pong from lightbringer-windows (100.93.104.46) via 192.168.1.58:41641 in 5ms"
            rtt_match = re.search(r"in\s+([0-9.]+)(ms|s)", out_str)
            via_match = re.search(r"via\s+([^\s]+)", out_str)
            peer_match = re.search(r"pong from\s+([^\s]+)", out_str)

            if rtt_match:
                val = float(rtt_match.group(1))
                unit = rtt_match.group(2)
                rtt_ms = val if unit == "ms" else val * 1000.0
                return {
                    "target": target,
                    "peer": peer_match.group(1) if peer_match else target,
                    "rtt_ms": rtt_ms,
                    "via": via_match.group(1) if via_match else "direct",
                    "success": True,
                    "raw": out_str.strip(),
                }
            else:
                return {
                    "target": target,
                    "success": False,
                    "error": "No pong response received",
                    "raw": out_str.strip(),
                }
        except asyncio.TimeoutError:
            return {"target": target, "success": False, "error": "Ping timed out (>5.0s)"}
        except Exception as exc:
            return {"target": target, "success": False, "error": str(exc)}


_GLOBAL_CLIENT: TailscaleMeshClient | None = None


def get_tailscale_mesh_client() -> TailscaleMeshClient:
    """Singleton getter for the global TailscaleMeshClient."""
    global _GLOBAL_CLIENT
    if _GLOBAL_CLIENT is None:
        _GLOBAL_CLIENT = TailscaleMeshClient()
    return _GLOBAL_CLIENT


class TailscaleAuthMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware authenticating Tailscale peer nodes with anti-spoofing."""

    def __init__(
        self,
        app: Any,
        client: TailscaleMeshClient | None = None,
        exempt_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.client = client or get_tailscale_mesh_client()
        self.exempt_paths = set(exempt_paths or [
            "/health",
            "/docs",
            "/openapi.json",
            "/static",
        ])

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        # Invariant 3: Anti-spoofing reverse proxy exemption.
        # Direct TCP socket origin check:
        client_host = request.client.host if request.client else ""

        # Allow local loopback callers (nginx reverse proxy, test suites, local CLI)
        if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
            request.state.tailscale_peer = None
            request.state.is_loopback = True
            return await call_next(request)

        # Allow exempt paths
        req_path = request.url.path
        if any(req_path == ep or req_path.startswith(f"{ep}/") for ep in self.exempt_paths):
            return await call_next(request)

        # If incoming from a Tailscale IP, authenticate peer identity via LocalAPI / whois
        if is_tailscale_ip(client_host):
            identity = await self.client.whois(client_host)
            if identity and identity.verified:
                request.state.tailscale_peer = identity
                request.state.is_loopback = False
                return await call_next(request)
            else:
                logger.warning("Rejected unverified Tailscale caller from %s on %s", client_host, req_path)
                return JSONResponse(
                    {
                        "error": "tailscale_auth_failed",
                        "detail": f"Untrusted or unverified Tailscale peer IP: {client_host}",
                    },
                    status_code=403,
                )

        # For non-loopback, non-Tailscale IPs accessing mesh endpoints: reject
        if req_path.startswith("/api/mesh"):
            return JSONResponse(
                {
                    "error": "forbidden",
                    "detail": f"Mesh endpoints require Tailscale or loopback origin; received {client_host}",
                },
                status_code=403,
            )

        # Pass through all other requests to existing gateway IP filters/route handlers
        return await call_next(request)
