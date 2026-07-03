"""
HD Synthesis Backend — FastAPI Webhook Handler
===============================================

Receiver for incoming HD synthesis requests. Integrates with Prismatic's
signal provider to nudge the HD Synthesis Agent.
"""
from __future__ import annotations

import os
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from prismatic.providers.signals import create_signal_provider, SignalPayload, SignalAction

app = FastAPI(title="HD Synthesis Webhook")

class HDSynthesisRequest(BaseModel):
    user_id: str
    request_type: str  # individual | relationship | transit
    params: Dict[str, Any]
    callback_url: str | None = None

@app.post("/webhook/hd-synthesis")
async def handle_hd_synthesis(request: HDSynthesisRequest):
    """Receive a request for HD synthesis and nudge the agent."""

    # 1. Create a signal for the HD Synthesis Agent
    signal_provider = create_signal_provider({
        "type": "file",
        "directory": os.environ.get("PRISMATIC_NUDGE_DIR", "/tmp/prismatic"),
    })

    payload = SignalPayload(
        target="hd_synthesis",
        action=SignalAction.WORK,
        issue_id=f"hd-{request.user_id}-{request.request_type}",
        title=f"HD {request.request_type.capitalize()} Report for {request.user_id}",
        metadata={
            "user_id": request.user_id,
            "request_type": request.request_type,
            "params": request.params,
            "callback_url": request.callback_url
        }
    )

    # 2. Send the signal
    success = signal_provider.send("hd_synthesis", payload)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to queue synthesis task")

    return {"status": "queued", "task_id": payload.issue_id}

@app.get("/health")
async def health_check():
    return {"status": "ok"}
