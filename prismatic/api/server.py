"""FastAPI gateway server for the Prismatic Engine.

Provides REST API for job submission, credit checks, and event ingest,
plus a WebSocket endpoint for real-time event streaming.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from prismatic.api.auth import verify_api_key
from prismatic.api.routers import credits, jobs
from prismatic.telemetry import get_collector

logger = logging.getLogger("prismatic.api")

API_PREFIX = "/api/v1"

app = FastAPI(
    title="Prismatic Engine API",
    description="API gateway and event bridge for Prismatic Engine",
    version="0.2.0",
    docs_url=f"{API_PREFIX}/docs",
    openapi_url=f"{API_PREFIX}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebSocket Management ──────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]):
        if not self.active_connections:
            return

        message_json = json.dumps(message)
        # Create a list to iterate to avoid issues if set changes during broadcast
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.error(f"Error broadcasting to websocket: {e}")
                self.disconnect(connection)

manager = ConnectionManager()

# ── Models ────────────────────────────────────────────────

class EventIngest(BaseModel):
    event: str = Field(..., description="Event type (e.g. launched, completed, error)")
    agent_name: str = Field(..., description="Name of the agent")
    issue_id: str | None = Field(None, description="Related Linear issue ID")
    title: str | None = Field(None, description="Short summary")
    message: str | None = Field(None, description="Detailed message or log")
    payload: dict[str, Any] | None = Field(None, description="Arbitrary event metadata")

# ── Health & Root ────────────────────────────────────────

@app.get(f"{API_PREFIX}/health")
async def health():
    return {
        "status": "ok",
        "version": "0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get(f"{API_PREFIX}/")
async def root():
    return {
        "service": "Prismatic Engine API",
        "version": "0.2.0",
        "docs": f"{API_PREFIX}/docs",
    }

# ── Event Ingest ──────────────────────────────────────────

@app.post(f"{API_PREFIX}/events/ingest", status_code=202)
async def ingest_event(
    event_data: EventIngest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_api_key)
):
    """Ingest an event and broadcast it to all connected WebSocket clients."""
    full_event = event_data.model_dump()
    full_event["timestamp"] = datetime.now(timezone.utc).isoformat()

    # 1. Broadcast to WebSockets
    background_tasks.add_task(manager.broadcast, full_event)

    # 2. Record to telemetry if appropriate
    if event_data.event in ("launched", "completed", "failed", "error"):
        try:
            collector = get_collector()
            # Mapping some events to telemetry
            if event_data.event == "launched":
                background_tasks.add_task(
                    collector.record_agent_run,
                    run_id=f"evt-{datetime.now().timestamp()}",
                    agent=event_data.agent_name,
                    issue_id=event_data.issue_id or "unknown",
                    status="dispatched"
                )
        except Exception as e:
            logger.error(f"Failed to record telemetry for ingested event: {e}")

    return {"status": "accepted", "event": event_data.event}

# ── WebSocket Endpoint ────────────────────────────────────

@app.websocket(f"{API_PREFIX}/events/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str | None = None
):
    """WebSocket endpoint for real-time event streaming.

    Accepts token via query parameter 'token'.
    """
    from prismatic.api.auth import VALID_KEYS

    if token not in VALID_KEYS:
        await websocket.accept()
        await websocket.send_text(json.dumps({"error": "Unauthorized", "message": "Invalid API key"}))
        await websocket.close(code=4001)
        return

    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, wait for messages (though we mostly broadcast)
            data = await websocket.receive_text()
            # Echo or handle incoming WS messages if needed
            await websocket.send_text(json.dumps({"echo": data}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# ── Routers ───────────────────────────────────────────────

app.include_router(credits.router, prefix=API_PREFIX, tags=["credits"])
app.include_router(jobs.router, prefix=API_PREFIX, tags=["jobs"])

# ── Execution ─────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(description="Prismatic Engine API Gateway")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    args, _ = parser.parse_known_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    logger.info("Starting Prismatic API Server on %s:%d", args.host, args.port)
    uvicorn.run(
        "prismatic.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )

if __name__ == "__main__":
    run()
