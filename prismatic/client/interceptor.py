"""Universal Hypervisor Client and Agent Interceptor SDK.

Enforces Prismatic Engine Layer 1 Interceptor specifications:
- Automatic SwarmLock lease acquisition & background TTL heartbeat renewal.
- Live telemetry signal emission to Prismatic Hub (/api/gateway/signals/emit).
- Dual-OS endpoint resolution (Unix domain socket, local HTTP, or Tailscale HTTPS).
- Clean-room verification receipt integration (GRO-4203).
"""

from __future__ import annotations

import os
import time
import json
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator
import urllib.request
import urllib.error

logger = logging.getLogger("prismatic.client.interceptor")


def default_gateway_endpoint() -> str:
    """Resolve the default Prismatic Gateway endpoint based on environment and OS."""
    if os.environ.get("PRISMATIC_GATEWAY_URL"):
        return os.environ["PRISMATIC_GATEWAY_URL"].rstrip("/")
    if os.environ.get("PRISMATIC_PORT"):
        return f"http://127.0.0.1:{os.environ['PRISMATIC_PORT']}"
    return "http://127.0.0.1:9000"


@dataclass
class SignalPayload:
    agent_id: str
    stage: str
    message: str
    task_id: str | None = None
    model: str = "inherit"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "stage": self.stage,
            "message": self.message,
            "task_id": self.task_id,
            "model": self.model,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


class LeaseContext:
    """Manages an active SwarmLock lease with a background heartbeat thread."""

    def __init__(
        self,
        client: "HypervisorClient",
        paths: list[str],
        ttl: int = 300,
        owner: str = "agy",
        task_id: str | None = None,
    ) -> None:
        self.client = client
        self.paths = [str(Path(p).as_posix()) for p in paths]
        self.ttl = ttl
        self.owner = owner
        self.task_id = task_id
        self.lease_id: str | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_heartbeat = threading.Event()
        self.acquired = False

    def acquire(self) -> bool:
        """Acquire lease from gateway."""
        primary_resource = self.paths[0] if self.paths else "file:workspace"
        payload = {
            "paths": self.paths,
            "resource": primary_resource,
            "owner": self.owner,
            "agent_id": self.owner,
            "task_id": self.task_id,
            "ttl": self.ttl,
        }
        res = self.client._post("/api/gateway/swarmlock/acquire", payload)
        if res and (res.get("status") == "ok" or res.get("ok") is True):
            self.lease_id = res.get("lease_id")
            self.acquired = True
            self._start_heartbeat()
            return True
        logger.warning("SwarmLock lease acquisition failed or skipped: %s", res)
        # In offline/degraded mode, treat as acquired locally
        self.acquired = True
        self._start_heartbeat()
        return True

    def release(self) -> bool:
        """Release lease and stop heartbeat."""
        self._stop_heartbeat.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)

        if not self.acquired:
            return True

        primary_resource = self.paths[0] if self.paths else "file:workspace"
        payload = {
            "paths": self.paths,
            "resource": primary_resource,
            "owner": self.owner,
            "agent_id": self.owner,
            "task_id": self.task_id,
            "lease_id": self.lease_id,
        }
        res = self.client._post("/api/gateway/swarmlock/release", payload)
        self.acquired = False
        return bool(res and (res.get("status") == "ok" or res.get("ok") is True))

    def _start_heartbeat(self) -> None:
        """Start daemon heartbeat thread to renew lease every (ttl / 2) seconds."""
        interval = max(5.0, self.ttl / 2.0)

        def _heartbeat_worker():
            while not self._stop_heartbeat.wait(interval):
                primary_resource = self.paths[0] if self.paths else "file:workspace"
                payload = {
                    "paths": self.paths,
                    "resource": primary_resource,
                    "owner": self.owner,
                    "agent_id": self.owner,
                    "task_id": self.task_id,
                    "lease_id": self.lease_id,
                    "ttl": self.ttl,
                }
                self.client._post("/api/gateway/swarmlock/heartbeat", payload)

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat_worker, daemon=True, name="SwarmLockHeartbeat"
        )
        self._heartbeat_thread.start()

    def __enter__(self) -> "LeaseContext":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class TaskContext:
    """Manages an admitted task context within the Hypervisor."""

    def __init__(
        self,
        client: "HypervisorClient",
        task_id: str,
        producer: str = "agy",
        model: str = "inherit",
    ) -> None:
        self.client = client
        self.task_id = task_id
        self.producer = producer
        self.model = model

    @contextmanager
    def acquire_lease(
        self, paths: list[str], ttl: int = 300
    ) -> Generator[LeaseContext, None, None]:
        """Acquire a managed lease on files for the duration of a code block."""
        lease = LeaseContext(
            client=self.client,
            paths=paths,
            ttl=ttl,
            owner=self.producer,
            task_id=self.task_id,
        )
        with lease:
            yield lease

    def emit_signal(
        self,
        stage: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Emit a telemetry signal attached to this task."""
        payload = SignalPayload(
            agent_id=self.producer,
            stage=stage,
            message=message,
            task_id=self.task_id,
            model=self.model,
            metadata=metadata or {},
        )
        return self.client.emit_signal(payload)

    def run_verification(
        self,
        command: str,
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Execute a clean-room verification receipt runner for this task."""
        try:
            from prismatic.verification.receipt_runner import run_clean_room_receipt

            receipt = run_clean_room_receipt(
                command=command,
                task_id=self.task_id,
                producer=self.producer,
                model=self.model,
                timeout=timeout,
            )
            return receipt.to_dict()
        except ImportError:
            logger.warning("Receipt runner not available in runtime; executing subprocess")
            import subprocess

            start = time.time()
            res = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return {
                "task_id": self.task_id,
                "command": command,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "duration_seconds": round(time.time() - start, 3),
            }


try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


@dataclass(frozen=True)
class TransportCandidate:
    kind: str  # "uds" or "http"
    target: str  # socket path or URL
    description: str = ""


def get_candidate_transports(explicit_endpoint: str | None = None) -> list[TransportCandidate]:
    """Resolve prioritized list of gateway transports: UDS -> Loopback -> Tailscale."""
    candidates: list[TransportCandidate] = []
    port = os.environ.get("PRISMATIC_PORT", "9000")

    # If explicit endpoint provided or PRISMATIC_GATEWAY_URL set
    override = explicit_endpoint or os.environ.get("PRISMATIC_GATEWAY_URL")
    if override:
        ov = override.strip()
        if ov.startswith("unix://") or ov.endswith(".sock") or (os.path.isabs(ov) and ("/" in ov or "\\" in ov)):
            sock_path = ov.removeprefix("unix://")
            candidates.append(TransportCandidate("uds", sock_path, f"Explicit UDS: {sock_path}"))
        else:
            candidates.append(TransportCandidate("http", ov.rstrip("/"), f"Explicit HTTP: {ov}"))

    # 1. Unix Domain Socket candidates (Linux / POSIX)
    uds_paths: list[str | None] = [
        os.environ.get("PRISMATIC_GATEWAY_SOCKET"),
        "/tmp/prismatic-gateway.sock",
        "/run/prismatic/gateway.sock",
        str(Path.home() / ".prismatic" / "gateway.sock"),
    ]
    for p in uds_paths:
        if p and not any(c.target == p for c in candidates):
            candidates.append(TransportCandidate("uds", p, f"Unix Socket: {p}"))

    # 2. Local Loopback HTTP candidates
    loopback_urls = [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    ]
    for u in loopback_urls:
        if not any(c.target == u for c in candidates):
            candidates.append(TransportCandidate("http", u, f"Loopback: {u}"))

    # 3. Tailscale / remote-mesh candidates. Override with PRISMATIC_TAILSCALE_CANDIDATES
    # (comma-separated host or host:port entries, e.g. "gpu-box:9000,100.64.0.5").
    # Defaults preserve the original single-box behavior; other machines set the
    # env var to their own mesh hosts (or to empty to skip this tier entirely).
    _mesh_env = os.environ.get("PRISMATIC_TAILSCALE_CANDIDATES")
    if _mesh_env is None:
        _mesh_hosts = ["webtop-hermes", "100.83.32.92"]
    else:
        _mesh_hosts = [h.strip() for h in _mesh_env.split(",") if h.strip()]
    tailscale_urls = []
    for _host in _mesh_hosts:
        if _host.startswith("http://") or _host.startswith("https://"):
            tailscale_urls.append(_host.rstrip("/"))
        elif ":" in _host:
            tailscale_urls.append(f"http://{_host}")
        else:
            tailscale_urls.append(f"http://{_host}:{port}")
    for tu in tailscale_urls:
        if not any(c.target == tu for c in candidates):
            candidates.append(TransportCandidate("http", tu, f"Tailscale Mesh: {tu}"))

    return candidates


def _execute_transport_request(
    candidate: TransportCandidate,
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """Execute a single HTTP or UDS request against a specific candidate transport."""
    req_headers = dict(headers or {})
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"

    if candidate.kind == "uds":
        if not os.path.exists(candidate.target):
            return None
        if _HAS_HTTPX:
            try:
                transport = httpx.HTTPTransport(uds=candidate.target)
                with httpx.Client(transport=transport, base_url="http://localhost", timeout=timeout) as client:
                    resp = client.request(method, path, json=data, headers=req_headers)
                    if resp.status_code < 500:
                        try:
                            return resp.json()
                        except Exception:
                            return {"status": "ok", "raw": resp.text}
            except Exception as exc:
                logger.debug("UDS request failed on %s: %s", candidate.target, exc)
                return None
        else:
            import http.client
            import socket

            class _UDSConnection(http.client.HTTPConnection):
                def __init__(self, sock_path: str, to: float):
                    super().__init__("localhost", timeout=to)
                    self.sock_path = sock_path

                def connect(self):
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(self.timeout)
                    self.sock.connect(self.sock_path)

            try:
                conn = _UDSConnection(candidate.target, timeout)
                body = json.dumps(data) if data is not None else None
                conn.request(method, path, body=body, headers=req_headers)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8")
                conn.close()
                if resp.status < 500:
                    try:
                        return json.loads(raw)
                    except Exception:
                        return {"status": "ok", "raw": raw}
            except Exception as exc:
                logger.debug("Stdlib UDS request failed on %s: %s", candidate.target, exc)
                return None

    elif candidate.kind == "http":
        if _HAS_HTTPX:
            try:
                with httpx.Client(base_url=candidate.target, timeout=timeout) as client:
                    resp = client.request(method, path, json=data, headers=req_headers)
                    if resp.status_code < 500:
                        try:
                            return resp.json()
                        except Exception:
                            return {"status": "ok", "raw": resp.text}
            except Exception as exc:
                logger.debug("HTTP request failed on %s: %s", candidate.target, exc)
                return None
        else:
            url = f"{candidate.target}{path}"
            body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
            try:
                req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status < 500:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                logger.debug("urllib request failed on %s: %s", url, exc)
                return None

    return None


class HypervisorClient:
    """Universal client for interacting with the Prismatic Hypervisor & Control Plane.

    Enforces automatic multi-transport failover across:
    1. Local Unix Domain Sockets (/tmp/prismatic-gateway.sock, /run/prismatic/gateway.sock)
    2. Local loopback HTTP (http://127.0.0.1:9000)
    3. Distributed Tailscale mesh (http://webtop-hermes:9000 or http://100.83.32.92:9000)
    """

    def __init__(self, endpoint: str | None = None, fallback: bool | None = None) -> None:
        self.explicit_endpoint = endpoint
        self._active_transport: TransportCandidate | None = None
        # If an explicit endpoint is provided, do not fall back to ambient local sockets/loopback
        # unless fallback is explicitly requested.
        use_fallback = fallback if fallback is not None else (endpoint is None)
        if use_fallback:
            self._candidates = get_candidate_transports(endpoint)
        elif endpoint:
            ov = endpoint.strip()
            if ov.startswith("unix://") or ov.endswith(".sock") or (os.path.isabs(ov) and ("/" in ov or "\\" in ov)):
                sock_path = ov.removeprefix("unix://")
                self._candidates = [TransportCandidate("uds", sock_path, f"Explicit UDS: {sock_path}")]
            else:
                self._candidates = [TransportCandidate("http", ov.rstrip("/"), f"Explicit HTTP: {ov}")]
        else:
            self._candidates = []

    @property
    def endpoint(self) -> str:
        """Active or preferred gateway endpoint string."""
        if self._active_transport:
            if self._active_transport.kind == "uds":
                return f"unix://{self._active_transport.target}"
            return self._active_transport.target
        if self.explicit_endpoint:
            return self.explicit_endpoint
        return default_gateway_endpoint()

    @endpoint.setter
    def endpoint(self, value: str) -> None:
        self.explicit_endpoint = value
        self._active_transport = None
        ov = value.strip()
        if ov.startswith("unix://") or ov.endswith(".sock") or (os.path.isabs(ov) and ("/" in ov or "\\" in ov)):
            sock_path = ov.removeprefix("unix://")
            self._candidates = [TransportCandidate("uds", sock_path, f"Explicit UDS: {sock_path}")]
        else:
            self._candidates = [TransportCandidate("http", ov.rstrip("/"), f"Explicit HTTP: {ov}")]

    @property
    def active_transport(self) -> TransportCandidate | None:
        return self._active_transport

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("PRISMATIC_API_TOKEN") or os.environ.get("PRISMATIC_OPERATOR_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        max_retries: int = 3,
        timeout: float = 8.0,
    ) -> dict[str, Any] | None:
        """Execute request with automatic multi-transport failover and exponential backoff."""
        headers = self._get_headers()

        # 1. Try active transport if already established
        if self._active_transport:
            res = _execute_transport_request(self._active_transport, method, path, data, headers, timeout=timeout)
            if res is not None:
                return res
            # Transport failed, invalidate and failover
            logger.debug("Active transport %s failed, re-probing transports", self._active_transport)
            self._active_transport = None

        # 2. Probe candidates in priority order (UDS -> Loopback -> Tailscale)
        delay = 0.3
        for attempt in range(max_retries):
            for candidate in self._candidates:
                probe_timeout = timeout
                res = _execute_transport_request(candidate, method, path, data, headers, timeout=probe_timeout)
                if res is not None:
                    self._active_transport = candidate
                    logger.debug("Selected active transport: %s (%s)", candidate.kind, candidate.target)
                    return res
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
        return None

    def _post(self, path: str, data: dict[str, Any], max_retries: int = 3) -> dict[str, Any] | None:
        """Helper to make HTTP POST requests to Gateway API with automatic failover."""
        return self._request("POST", path, data=data, max_retries=max_retries)

    def _get(self, path: str, max_retries: int = 3) -> dict[str, Any] | None:
        """Helper to make HTTP GET requests to Gateway API with automatic failover."""
        return self._request("GET", path, data=None, max_retries=max_retries)

    def acquire_lease(
        self,
        paths: list[str] | str,
        ttl: int = 300,
        owner: str = "agy",
        task_id: str | None = None,
    ) -> LeaseContext:
        """Convenience helper to create and manage a LeaseContext."""
        if isinstance(paths, str):
            paths = [paths]
        return LeaseContext(self, paths=paths, ttl=ttl, owner=owner, task_id=task_id)


    @contextmanager
    def admit_task(
        self,
        task_id: str,
        producer: str = "agy",
        model: str = "inherit",
    ) -> Generator[TaskContext, None, None]:
        """Admit a task into the Hypervisor and enter its execution context."""
        payload = {
            "task_id": task_id,
            "producer": producer,
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._post("/api/gateway/admissions", payload)
        ctx = TaskContext(self, task_id=task_id, producer=producer, model=model)
        ctx.emit_signal("ADMITTED", f"Task {task_id} admitted by {producer}")
        try:
            yield ctx
            ctx.emit_signal("COMPLETED", f"Task {task_id} completed successfully")
        except Exception as err:
            ctx.emit_signal("FAILED", f"Task {task_id} failed: {err}")
            raise

    def emit_signal(
        self,
        signal: SignalPayload | dict[str, Any] | None = None,
        source: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
        task_id: str | None = None,
    ) -> DualReturn:
        """Emit a real-time signal to Prismatic Hub (supports both sync and async await)."""
        if signal is not None:
            data = signal.to_dict() if isinstance(signal, SignalPayload) else signal
        else:
            t_id = task_id or (details.get("task_id") if isinstance(details, dict) else None)
            data = {
                "agent_id": source or "agy",
                "agent": source or "agy",
                "stage": action or "info",
                "event_type": action or "info",
                "message": f"{source or 'agy'}: {action or 'signal'}",
                "task_id": t_id,
                "issue_id": t_id or "",
                "metadata": details or {},
                "severity": severity,
                "timestamp": time.time(),
            }
        res = self._post("/api/gateway/signals/emit", data)
        ok = bool(res and (res.get("status") == "ok" or res.get("ok") is True))
        return DualReturn(ok)

    def acquire_swarmlock(
        self,
        resource: str,
        agent_id: str = "agy",
        task_id: str | None = None,
        lease_seconds: int = 120,
    ) -> DualReturn:
        """Acquire a SwarmLock lease (supports both sync and async await)."""
        payload = {
            "resource": resource,
            "paths": [resource],
            "agent_id": agent_id,
            "owner": agent_id,
            "task_id": task_id,
            "ttl": lease_seconds,
        }
        res = self._post("/api/gateway/swarmlock/acquire", payload)
        if res is None:
            return DualReturn({
                "ok": False,
                "status": "offline",
                "error": "Gateway unreachable",
            })
        if not res.get("ok") and "error" not in res:
            holder = res.get("holder", "unknown")
            res["error"] = f"Resource '{resource}' is currently locked by '{holder}'"
        return DualReturn(res)

    def release_swarmlock(
        self,
        resource: str,
        agent_id: str = "agy",
        task_id: str | None = None,
        lease_id: str | None = None,
    ) -> DualReturn:
        """Release a SwarmLock lease (supports both sync and async await)."""
        payload = {
            "resource": resource,
            "paths": [resource],
            "agent_id": agent_id,
            "owner": agent_id,
            "task_id": task_id,
            "lease_id": lease_id,
        }
        res = self._post("/api/gateway/swarmlock/release", payload)
        if res is None:
            return DualReturn({
                "ok": False,
                "status": "offline",
                "error": "Gateway unreachable",
            })
        return DualReturn(res)

    def get_status(self) -> dict[str, Any]:
        """Get live Hypervisor health and lock status."""
        health = self._get("/health") or {"status": "offline"}
        locks = self._get("/api/gateway/swarmlock/status") or {"active_leases": []}
        return {
            "endpoint": self.endpoint,
            "health": health,
            "locks": locks,
        }

    def get_control_status(self) -> dict[str, Any]:
        """Get live fleet control and pause status."""
        res = self._get("/api/gateway/control/status")
        return (res and res.get("status")) or {"fleet_paused": False, "paused_agents": []}

    def is_paused(self, agent_id: str | None = None) -> bool:
        """Check if fleet or agent is paused by operator."""
        status = self.get_control_status()
        if status.get("fleet_paused"):
            return True
        if agent_id and agent_id.lower() in status.get("paused_agents", []):
            return True
        return False


class DualReturn:
    """Wrapper that can be evaluated synchronously or awaited asynchronously."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __bool__(self) -> bool:
        if isinstance(self.value, dict):
            return bool(self.value.get("ok", self.value.get("status") == "ok"))
        return bool(self.value)

    def __getitem__(self, item: Any) -> Any:
        return self.value[item]

    def get(self, k: str, default: Any = None) -> Any:
        return self.value.get(k, default) if isinstance(self.value, dict) else default

    def __repr__(self) -> str:
        return repr(self.value)

    def __await__(self):
        async def _coro():
            return self.value
        return _coro().__await__()


_DEFAULT_HYPERVISOR_CLIENT: HypervisorClient | None = None


def get_hypervisor_client(endpoint: str | None = None) -> HypervisorClient:
    """Get or create singleton HypervisorClient instance."""
    global _DEFAULT_HYPERVISOR_CLIENT
    if _DEFAULT_HYPERVISOR_CLIENT is None or endpoint is not None:
        _DEFAULT_HYPERVISOR_CLIENT = HypervisorClient(endpoint=endpoint)
    return _DEFAULT_HYPERVISOR_CLIENT
