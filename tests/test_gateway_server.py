"""Tests for the Prismatic Gateway HTTP/gRPC server (GRO-1562)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import grpc
import prismatic.lock
import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.gateway.proto_out import gateway_pb2, gateway_pb2_grpc
from prismatic.run_records import AgentRunRecordStore
from prismatic.gateway.webhook_router import (
    _verify_github_signature,
    _verify_linear_signature,
)


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def temp_lock_file(tmp_path: Path) -> None:
    """Redirect lock file to a temp path for test isolation."""
    lock_path = tmp_path / "swarm_locks.json"
    with patch("prismatic.lock.LOCK_FILE", Path(str(lock_path))):
        lock_path.write_text("[]")
        yield


@pytest.fixture(autouse=True)
def temp_state_dir(tmp_path: Path) -> None:
    """Redirect state directory for run record store."""
    state_dir = tmp_path / "prismatic_state"
    state_dir.mkdir()
    with patch.dict(os.environ, {"PRISMATIC_STATE_DIR": str(state_dir)}):
        yield


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """FastAPI TestClient with manually-initialized run store."""
    state_dir = tmp_path / "prismatic_state"
    state_dir.mkdir(exist_ok=True)
    store_path = str(state_dir / "run_records.json")
    import prismatic.gateway.server as srv
    srv._run_store = AgentRunRecordStore(store_path=store_path)
    with TestClient(app) as c:
        yield c


# ── Health Endpoint ───────────────────────────────────────


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_health_uptime_increases(self, client: TestClient) -> None:
        """Uptime should increase over sequential calls."""
        t0 = client.get("/health").json()["uptime_seconds"]
        time.sleep(0.01)
        t1 = client.get("/health").json()["uptime_seconds"]
        assert t1 >= t0


# ── Lock Endpoints ────────────────────────────────────────


class TestLockEndpoints:
    LOCK_PAYLOAD = {
        "file": "/test/locks/test.txt",
        "agent": "test-agent",
        "heartbeat_ms": 1_000_000.0,
        "acquired_ms": 1_000_000.0,
    }

    def _write_lock_file(self, locks: list[dict[str, Any]]) -> None:
        Path(prismatic.lock.LOCK_FILE).write_text(json.dumps(locks, indent=2))

    def test_list_locks_empty(self, client: TestClient) -> None:
        resp = client.get("/locks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_locks_with_data(self, client: TestClient) -> None:
        self._write_lock_file([self.LOCK_PAYLOAD])
        resp = client.get("/locks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["file"] == self.LOCK_PAYLOAD["file"]

    def test_get_specific_lock_found(self, client: TestClient) -> None:
        # Lock path from the route param is URL-decoded — match with leading /
        lock_data = {"file": "/test/path", "agent": "test", "heartbeat_ms": 1000.0, "acquired_ms": 1000.0}
        self._write_lock_file([lock_data])
        resp = client.get("/locks//test/path")
        assert resp.status_code == 200
        assert resp.json()["file"] == "/test/path"

    def test_get_specific_lock_not_found(self, client: TestClient) -> None:
        self._write_lock_file([self.LOCK_PAYLOAD])
        resp = client.get("/locks/nonexistent.txt")
        assert resp.status_code == 404
        assert "not locked" in resp.text

    def test_stale_locks_empty(self, client: TestClient) -> None:
        self._write_lock_file([])
        resp = client.get("/locks/stale")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_stale_locks_detects_expired(self, client: TestClient) -> None:
        """A lock with a very old heartbeat should be flagged as stale."""
        old_lock = dict(self.LOCK_PAYLOAD)
        old_lock["heartbeat_ms"] = 1.0  # 0.001 seconds — ancient
        self._write_lock_file([old_lock])
        resp = client.get("/locks/stale")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["file"] == old_lock["file"]

    def test_stale_locks_skips_fresh(self, client: TestClient) -> None:
        """A lock with a recent heartbeat should NOT be stale."""
        fresh_lock = dict(self.LOCK_PAYLOAD)
        fresh_lock["heartbeat_ms"] = time.time() * 1000  # now
        self._write_lock_file([fresh_lock])
        resp = client.get("/locks/stale")
        assert resp.status_code == 200
        assert resp.json() == []


# ── Run Record Endpoints ──────────────────────────────────


class TestRunRecordEndpoints:
    def test_list_runs_empty(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_list_run(self, client: TestClient) -> None:
        # Create a run
        create_resp = client.post("/runs", json={
            "issue_id": "GRO-9999",
            "agent_name": "test-bot",
        })
        assert create_resp.status_code == 200
        create_data = create_resp.json()
        assert "run_id" in create_data
        run_id = create_data["run_id"]

        # List runs
        list_resp = client.get("/runs")
        assert list_resp.status_code == 200
        runs = list_resp.json()
        assert len(runs) == 1
        assert runs[0]["run_id"] == run_id
        assert runs[0]["issue_id"] == "GRO-9999"
        assert runs[0]["agent_name"] == "test-bot"

    def test_get_single_run(self, client: TestClient) -> None:
        create = client.post("/runs", json={"issue_id": "GRO-0001", "agent_name": "test"}).json()
        run_id = create["run_id"]
        resp = client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == run_id

    def test_get_run_not_found(self, client: TestClient) -> None:
        resp = client.get("/runs/nonexistent-run-id")
        assert resp.status_code == 404

    def test_complete_run(self, client: TestClient) -> None:
        create = client.post("/runs", json={"issue_id": "GRO-0002", "agent_name": "test"}).json()
        run_id = create["run_id"]

        complete = client.post(f"/runs/{run_id}/complete", json={
            "status": "completed",
            "output_path": "/tmp/output.txt",
        })
        assert complete.status_code == 200
        assert complete.json()["status"] == "completed"

        # Verify the run shows completed
        run = client.get(f"/runs/{run_id}").json()
        assert run["status"] == "completed"

    def test_list_runs_filtered_by_status(self, client: TestClient) -> None:
        r1 = client.post("/runs", json={"issue_id": "GRO-001", "agent_name": "t1"}).json()
        r2 = client.post("/runs", json={"issue_id": "GRO-002", "agent_name": "t2"}).json()
        client.post(f"/runs/{r2['run_id']}/complete", json={"status": "completed"})

        # Filter for pending (initial state from create_run)
        pending = client.get("/runs?status=pending").json()
        assert len(pending) == 1
        assert pending[0]["run_id"] == r1["run_id"]

        # Filter for completed
        completed = client.get("/runs?status=completed").json()
        assert len(completed) == 1
        assert completed[0]["run_id"] == r2["run_id"]


# ── HMAC Signature Verification ───────────────────────────


class TestHMACVerification:
    def test_github_signature_valid(self) -> None:
        body = b'{"action": "opened"}'
        secret = "my-secret-key"
        sig = "sha256=" + _compute_hmac(body, secret)
        assert _verify_github_signature(body, sig, secret) is True

    def test_github_signature_invalid(self) -> None:
        body = b'{"action": "opened"}'
        assert _verify_github_signature(body, "sha256=invalid", "secret") is False

    def test_github_signature_missing(self) -> None:
        body = b'{"action": "opened"}'
        assert _verify_github_signature(body, None, "secret") is False

    def test_github_signature_wrong_prefix(self) -> None:
        body = b'{"action": "opened"}'
        assert _verify_github_signature(body, "md5=bad", "secret") is False

    def test_linear_signature_valid(self) -> None:
        body = b'{"type": "Issue", "data": {}}'
        secret = "linear-secret"
        sig = _compute_hmac(body, secret)
        assert _verify_linear_signature(body, sig, secret) is True

    def test_linear_signature_invalid(self) -> None:
        body = b'{"type": "Issue", "data": {}}'
        assert _verify_linear_signature(body, "bad-sig", "secret") is False

    def test_linear_signature_different_secret(self) -> None:
        body = b'{"type": "Issue", "data": {}}'
        sig = _compute_hmac(body, "correct-secret")
        assert _verify_linear_signature(body, sig, "wrong-secret") is False


def _compute_hmac(body: bytes, secret: str) -> str:
    import hmac, hashlib
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ── gRPC Endpoints ─────────────────────────────────────────


class TestGrpcEndpoints:
    """Integration tests for the gRPC server (LockService, RunService, WatchdogService).

    Starts a gRPC server on a random port alongside the FastAPI test client.
    """

    @pytest.fixture(autouse=True)
    def grpc_server_and_channel(self, tmp_path: Path) -> Any:
        """Start a gRPC server on a random port and return a channel.

        Runs the gRPC event loop in a background daemon thread.
        """
        import asyncio
        import socket
        import threading
        from prismatic.gateway.grpc_server import serve_grpc
        from prismatic.run_records import AgentRunRecordStore

        state_dir = tmp_path / "prismatic_state"
        state_dir.mkdir(exist_ok=True)
        store = AgentRunRecordStore(store_path=str(state_dir / "run_records.json"))

        loop = asyncio.new_event_loop()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

        server = loop.run_until_complete(serve_grpc(port, store))

        # Run event loop in background thread so it processes RPCs
        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        bg_thread = threading.Thread(target=_run_loop, daemon=True)
        bg_thread.start()

        channel = grpc.insecure_channel(f"localhost:{port}")
        grpc.channel_ready_future(channel).result(timeout=5)

        yield (channel, store)

        channel.close()
        loop.call_soon_threadsafe(server.stop, 5)
        loop.call_soon_threadsafe(loop.stop)
        bg_thread.join(timeout=5)

    # ── LockService tests ────────────────────────────────

    def test_list_locks_empty(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.LockServiceStub(channel)
        resp = stub.ListLocks(gateway_pb2.ListLocksRequest())
        assert len(resp.locks) == 0

    def test_list_locks_with_data(self, grpc_server_and_channel: Any, tmp_path: Path) -> None:
        channel, _ = grpc_server_and_channel
        lock_file = Path(prismatic.lock.LOCK_FILE)
        lock_file.write_text(json.dumps([{
            "file": "/test/foo.txt", "agent": "jules",
            "heartbeat_ms": 1_000_000.0, "acquired_ms": 1_000_000.0,
        }]))
        stub = gateway_pb2_grpc.LockServiceStub(channel)
        resp = stub.ListLocks(gateway_pb2.ListLocksRequest())
        assert len(resp.locks) == 1
        assert resp.locks[0].file == "/test/foo.txt"

    def test_get_lock_found(self, grpc_server_and_channel: Any, tmp_path: Path) -> None:
        channel, _ = grpc_server_and_channel
        Path(prismatic.lock.LOCK_FILE).write_text(json.dumps([{
            "file": "/test/bar.txt", "agent": "jules",
            "heartbeat_ms": 1_000_000.0, "acquired_ms": 1_000_000.0,
        }]))
        stub = gateway_pb2_grpc.LockServiceStub(channel)
        resp = stub.GetLock(gateway_pb2.GetLockRequest(file_path="/test/bar.txt"))
        assert resp.file == "/test/bar.txt"
        assert resp.agent == "jules"

    def test_get_lock_not_found(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.LockServiceStub(channel)
        with pytest.raises(grpc.RpcError) as exc:
            stub.GetLock(gateway_pb2.GetLockRequest(file_path="/nonexistent.txt"))
        assert exc.value.code() == grpc.StatusCode.NOT_FOUND

    def test_list_stale_locks(self, grpc_server_and_channel: Any, tmp_path: Path) -> None:
        channel, _ = grpc_server_and_channel
        lock_file = Path(prismatic.lock.LOCK_FILE)
        lock_file.write_text(json.dumps([
            {"file": "/stale/a", "agent": "old", "heartbeat_ms": 1.0, "acquired_ms": 1.0},
            {"file": "/fresh/b", "agent": "new", "heartbeat_ms": time.time() * 1000, "acquired_ms": time.time() * 1000},
        ]))
        stub = gateway_pb2_grpc.LockServiceStub(channel)
        resp = stub.ListStaleLocks(gateway_pb2.ListStaleLocksRequest())
        assert len(resp.locks) == 1
        assert resp.locks[0].file == "/stale/a"

    # ── RunService tests ─────────────────────────────────

    def test_list_runs_empty_grpc(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.RunServiceStub(channel)
        resp = stub.ListRuns(gateway_pb2.ListRunsRequest())
        assert len(resp.records) == 0

    def test_create_and_list_run_grpc(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.RunServiceStub(channel)
        create_resp = stub.CreateRun(gateway_pb2.CreateRunRequest(issue_id="GRO-9999", agent_name="jules"))
        assert create_resp.run_id != ""
        assert create_resp.status == "created"
        run_id = create_resp.run_id

        list_resp = stub.ListRuns(gateway_pb2.ListRunsRequest())
        assert len(list_resp.records) >= 1
        ids = [r.run_id for r in list_resp.records]
        assert run_id in ids

    def test_complete_run_grpc(self, grpc_server_and_channel: Any) -> None:
        channel, store = grpc_server_and_channel
        stub = gateway_pb2_grpc.RunServiceStub(channel)
        create = stub.CreateRun(gateway_pb2.CreateRunRequest(issue_id="GRO-0002", agent_name="jules"))
        complete = stub.CompleteRun(gateway_pb2.CompleteRunRequest(
            run_id=create.run_id, status="completed", output_path="/tmp/out.txt",
        ))
        assert complete.status == "completed"

        # Verify via HTTP endpoint too
        record = store.get_run(create.run_id)
        assert record is not None
        assert record.status == "completed"

    def test_get_run_not_found_grpc(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.RunServiceStub(channel)
        with pytest.raises(grpc.RpcError) as exc:
            stub.GetRun(gateway_pb2.GetRunRequest(run_id="nonexistent"))
        assert exc.value.code() == grpc.StatusCode.NOT_FOUND

    # ── WatchdogService tests ────────────────────────────

    def test_health_grpc(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.WatchdogServiceStub(channel)
        resp = stub.Health(gateway_pb2.HealthRequest())
        assert resp.status == "ok"
        assert resp.uptime_seconds >= 0
        assert resp.started_at > 0

    def test_heartbeat_grpc(self, grpc_server_and_channel: Any) -> None:
        channel, _ = grpc_server_and_channel
        stub = gateway_pb2_grpc.WatchdogServiceStub(channel)
        resp = stub.Heartbeat(gateway_pb2.HeartbeatRequest(agent_name="jules"))
        assert resp.status == "ok"
        assert resp.server_time > 0


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── Webhook Router Integration ────────────────────────────


class TestWebhookEndpoints:
    def test_github_webhook_unauthenticated(self, client: TestClient) -> None:
        """Missing signature should return 401."""
        resp = client.post(
            "/api/gateway/github",
            json={"action": "opened"},
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 401

    def test_github_webhook_authenticated(self, client: TestClient) -> None:
        """Valid signature should return 202."""
        import hmac, hashlib
        body = json.dumps({"action": "opened", "pull_request": {"number": 42}})
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "test-secret")
        sig = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "test-secret"}):
            resp = client.post(
                "/api/gateway/github",
                content=body,
                headers={
                    "x-github-event": "pull_request",
                    "x-hub-signature-256": sig,
                    "content-type": "application/json",
                },
            )
            assert resp.status_code == 202
            assert resp.json()["status"] == "accepted"

    def test_linear_webhook_unauthenticated(self, client: TestClient) -> None:
        resp = client.post(
            "/api/gateway/linear",
            json={"type": "Issue", "data": {}},
        )
        assert resp.status_code == 401

    def test_linear_webhook_authenticated(self, client: TestClient) -> None:
        import hmac, hashlib
        body = json.dumps({"type": "Issue", "action": "update", "data": {"id": "abc", "state": {"name": "Todo"}}})
        secret = "test-linear-secret"
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"LINEAR_WEBHOOK_SECRET": secret}):
            resp = client.post(
                "/api/gateway/linear",
                content=body,
                headers={
                    "linear-signature": sig,
                    "content-type": "application/json",
                },
            )
            assert resp.status_code == 202

    def test_github_webhook_pr_review(self, client: TestClient) -> None:
        """PR review events should also be accepted."""
        import hmac, hashlib
        body = json.dumps({
            "action": "submitted",
            "pull_request": {"number": 7},
            "review": {"state": "approved"},
        })
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "test-secret")
        sig = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "test-secret"}):
            resp = client.post(
                "/api/gateway/github",
                content=body,
                headers={
                    "x-github-event": "pull_request_review",
                    "x-hub-signature-256": sig,
                    "content-type": "application/json",
                },
            )
            assert resp.status_code == 202