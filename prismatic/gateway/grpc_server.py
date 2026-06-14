"""
gRPC service implementations for the Prismatic Gateway.

Runs alongside FastAPI on the same port (9000/9001) via grpc.aio.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import grpc
from grpc import aio as grpc_aio

from prismatic.gateway.proto_out import gateway_pb2
from prismatic.gateway.proto_out import gateway_pb2_grpc
from prismatic.lock import _read_locks as read_swarm_locks
from prismatic.run_records import AgentRunRecordStore

logger = logging.getLogger("prismatic.gateway.grpc")

SERVER_STARTED_AT: float = time.time()


# ── LockService ────────────────────────────────────────


class LockServiceServicer(gateway_pb2_grpc.LockServiceServicer):
    async def ListLocks(
        self, request: gateway_pb2.ListLocksRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.ListLocksResponse:
        locks = read_swarm_locks()
        return gateway_pb2.ListLocksResponse(
            locks=[
                gateway_pb2.Lock(
                    file=lock.get("file", ""),
                    agent=lock.get("agent", ""),
                    heartbeat_ms=lock.get("heartbeat_ms", 0),
                    acquired_ms=lock.get("acquired_ms", 0),
                )
                for lock in locks
            ]
        )

    async def GetLock(
        self, request: gateway_pb2.GetLockRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.Lock:
        for lock in read_swarm_locks():
            if lock.get("file") == request.file_path:
                return gateway_pb2.Lock(
                    file=lock.get("file", ""),
                    agent=lock.get("agent", ""),
                    heartbeat_ms=lock.get("heartbeat_ms", 0),
                    acquired_ms=lock.get("acquired_ms", 0),
                )
        await context.abort(grpc.StatusCode.NOT_FOUND, f"file '{request.file_path}' is not locked")
        return gateway_pb2.Lock()

    async def ListStaleLocks(
        self, request: gateway_pb2.ListStaleLocksRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.ListStaleLocksResponse:
        from prismatic.lock import STALE_TTL_MS

        now_ms = time.time() * 1000
        stale = []
        for lock in read_swarm_locks():
            hb = lock.get("heartbeat_ms", 0)
            if now_ms - hb > STALE_TTL_MS:
                stale.append(
                    gateway_pb2.Lock(
                        file=lock.get("file", ""),
                        agent=lock.get("agent", ""),
                        heartbeat_ms=lock.get("heartbeat_ms", 0),
                        acquired_ms=lock.get("acquired_ms", 0),
                    )
                )
        return gateway_pb2.ListStaleLocksResponse(locks=stale)


# ── RunService ─────────────────────────────────────────


class RunServiceServicer(gateway_pb2_grpc.RunServiceServicer):
    def __init__(self, store: AgentRunRecordStore) -> None:
        self._store = store

    async def ListRuns(
        self, request: gateway_pb2.ListRunsRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.ListRunsResponse:
        records = self._store.get_recent_runs(limit=request.limit or 50)
        if request.status_filter:
            records = [r for r in records if r.status == request.status_filter]
        return gateway_pb2.ListRunsResponse(
            records=[_record_to_pb(r) for r in records]
        )

    async def GetRun(
        self, request: gateway_pb2.GetRunRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.RunRecord:
        record = self._store.get_run(request.run_id)
        if record is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"run '{request.run_id}' not found")
        return _record_to_pb(record)

    async def CreateRun(
        self, request: gateway_pb2.CreateRunRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.CreateRunResponse:
        run_id = self._store.create_run(
            issue_id=request.issue_id,
            agent_name=request.agent_name,
        )
        return gateway_pb2.CreateRunResponse(run_id=run_id, status="created")

    async def CompleteRun(
        self, request: gateway_pb2.CompleteRunRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.CompleteRunResponse:
        record = self._store.get_run(request.run_id)
        if record is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"run '{request.run_id}' not found")
        self._store.update_run(
            request.run_id,
            status=request.status or "completed",
            output_path=request.output_path or None,
            error=request.error_message or None,
        )
        return gateway_pb2.CompleteRunResponse(run_id=request.run_id, status=request.status or "completed")


# ── WatchdogService ────────────────────────────────────


class WatchdogServiceServicer(gateway_pb2_grpc.WatchdogServiceServicer):
    async def Health(
        self, request: gateway_pb2.HealthRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.HealthResponse:
        uptime = time.time() - SERVER_STARTED_AT
        return gateway_pb2.HealthResponse(
            status="ok",
            uptime_seconds=uptime,
            started_at=SERVER_STARTED_AT,
        )

    async def Heartbeat(
        self, request: gateway_pb2.HeartbeatRequest, context: grpc_aio.ServicerContext
    ) -> gateway_pb2.HeartbeatResponse:
        logger.debug("Heartbeat from agent '%s'", request.agent_name)
        return gateway_pb2.HeartbeatResponse(
            status="ok",
            server_time=time.time(),
        )


# ── Helpers ────────────────────────────────────────────


def _record_to_pb(record: Any) -> gateway_pb2.RunRecord:
    return gateway_pb2.RunRecord(
        run_id=record.run_id,
        issue_id=record.issue_id,
        agent_name=record.agent_name,
        status=record.status,
        started_at=record.started_at,
        completed_at=record.completed_at or "",
        output_path=record.output_path or "",
        error_message=record.error_message or "",
    )


# ── gRPC Server Factory ────────────────────────────────


async def serve_grpc(port: int, store: AgentRunRecordStore) -> grpc_aio.Server:
    """Start the gRPC server on the given port alongside FastAPI."""
    server = grpc_aio.server(maximum_concurrent_rpcs=100)

    gateway_pb2_grpc.add_LockServiceServicer_to_server(LockServiceServicer(), server)
    gateway_pb2_grpc.add_RunServiceServicer_to_server(RunServiceServicer(store), server)
    gateway_pb2_grpc.add_WatchdogServiceServicer_to_server(WatchdogServiceServicer(), server)

    listen_addr = f"0.0.0.0:{port}"
    server.add_insecure_port(listen_addr)
    logger.info("gRPC server listening on %s", listen_addr)

    await server.start()
    return server
