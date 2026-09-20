"""Unit tests for HypervisorClient automatic multi-transport failover (GRO-4853)."""

import os
import socket
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from prismatic.client.interceptor import (
    HypervisorClient,
    TransportCandidate,
    get_candidate_transports,
    _execute_transport_request,
)


def test_candidate_transports_order():
    candidates = get_candidate_transports()
    kinds = [c.kind for c in candidates]
    # At least one UDS and at least one HTTP
    assert "uds" in kinds
    assert "http" in kinds

    # UDS candidates must appear before loopback HTTP candidates
    first_uds_idx = next(i for i, c in enumerate(candidates) if c.kind == "uds")
    first_http_idx = next(i for i, c in enumerate(candidates) if c.kind == "http")
    assert first_uds_idx < first_http_idx

    # Explicit endpoint prepended
    custom = get_candidate_transports("http://10.0.0.1:9000")
    assert custom[0].target == "http://10.0.0.1:9000"
    assert custom[0].kind == "http"

    custom_uds = get_candidate_transports("unix:///tmp/custom.sock")
    assert custom_uds[0].target == "/tmp/custom.sock"
    assert custom_uds[0].kind == "uds"


def test_client_active_transport_caching_and_invalidation():
    client = HypervisorClient()
    assert client.active_transport is None

    # Mock _execute_transport_request to simulate first transport failing, second succeeding
    cand_fail = TransportCandidate("uds", "/tmp/nonexistent.sock")
    cand_ok = TransportCandidate("http", "http://127.0.0.1:9000")
    client._candidates = [cand_fail, cand_ok]

    with patch("prismatic.client.interceptor._execute_transport_request") as mock_exec:
        # First call fails on cand_fail, succeeds on cand_ok
        mock_exec.side_effect = [None, {"status": "ok", "ping": "pong"}]

        res = client._get("/health")
        assert res == {"status": "ok", "ping": "pong"}
        assert client.active_transport == cand_ok
        assert client.endpoint == "http://127.0.0.1:9000"

        # Second call uses cached active_transport directly
        mock_exec.side_effect = [{"status": "ok", "active": True}]
        res2 = client._get("/health")
        assert res2 == {"status": "ok", "active": True}
        assert mock_exec.call_count == 3  # 2 in first request + 1 in second request

        # Third call: cached active_transport fails, triggers invalidation and re-probe
        mock_exec.side_effect = [None, {"status": "ok", "reconnected": True}]
        res3 = client._get("/health")
        assert res3 == {"status": "ok", "reconnected": True}


def test_uds_transport_execution():
    sock_path = tempfile.mktemp(suffix=".sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    s.listen(1)

    def srv():
        try:
            conn, _ = s.accept()
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 17\r\n\r\n"
                b"{\"status\":\"mock\"}"
            )
            conn.sendall(resp)

            conn.close()
        except Exception:
            pass

    t = threading.Thread(target=srv, daemon=True)
    t.start()

    cand = TransportCandidate("uds", sock_path)
    res = _execute_transport_request(cand, "GET", "/mock", timeout=2.0)
    assert res == {"status": "mock"}

    s.close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


def test_acquire_lease_convenience():
    client = HypervisorClient()
    lease = client.acquire_lease("my_file.py", ttl=60, owner="test_agent", task_id="TEST-1")
    assert lease.paths == ["my_file.py"]
    assert lease.ttl == 60
    assert lease.owner == "test_agent"
    assert lease.task_id == "TEST-1"
