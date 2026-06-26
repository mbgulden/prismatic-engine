"""External-facing Task Intake API.

Accepts task submissions from external systems (webhooks, Zapier, scripts),
validates payloads, enriches them with metadata, and enqueues them into
the Prismatic routing pipeline. Supports idempotency via the
``Idempotency-Key`` header so retried webhooks don't create duplicates.

Run from server.py alongside the other routers.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from prismatic.api.auth import verify_api_key

router = APIRouter()

# In-memory task store + idempotency index.
# (MVP — replace with the durable store used by jobs.py when promoted.)
_tasks: dict[str, dict[str, Any]] = {}
_idempotency_index: dict[str, str] = {}  # idempotency_key -> task_id


# --- Pydantic models ---------------------------------------------------------


class TaskSubmission(BaseModel):
    """Inbound task payload from an external system."""

    source: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Origin system identifier (e.g. 'zapier', 'cf-webhook', 'cli')",
    )
    title: str = Field(default="", max_length=256, description="Short human title")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form task payload passed downstream to the agent",
    )
    agent: str | None = Field(
        default=None,
        description="Optional preferred agent; routing decides if None",
    )
    priority: int = Field(default=3, ge=1, le=5, description="1=highest, 5=lowest")
    tags: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Free-form tags for routing/filtering",
    )

    @field_validator("tags")
    @classmethod
    def _strip_tags(cls, v: list[str]) -> list[str]:
        out = [t.strip().lower() for t in v if t and t.strip()]
        if len(out) > 16:
            raise ValueError("max 16 non-empty tags")
        return out


class TaskRecord(BaseModel):
    """Outbound view of an accepted task."""

    task_id: str
    source: str
    title: str
    agent: str | None
    priority: int
    tags: list[str]
    status: str
    payload: dict[str, Any]
    payload_hash: str
    received_at: str
    idempotency_key: str | None = None


# --- Helpers -----------------------------------------------------------------


def _hash_payload(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of the canonical (sorted-keys) JSON payload."""
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    # Microsecond precision so same-second inserts still sort correctly.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _enrich(record: dict[str, Any]) -> dict[str, Any]:
    """Apply server-side metadata fields the client didn't send."""
    record.setdefault("task_id", str(uuid.uuid4()))
    record.setdefault("status", "queued")
    record.setdefault("received_at", _now_iso())
    if "payload_hash" not in record:
        record["payload_hash"] = _hash_payload(record.get("payload") or {})
    return record


# --- Endpoints ---------------------------------------------------------------


@router.post(
    "/intake",
    status_code=201,
    summary="Submit an external task to the Prismatic routing pipeline",
)
async def submit_task(
    task: TaskSubmission,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Optional client-generated key (1-128 chars). Replays return the original task.",
    ),
    current_user: dict = Depends(verify_api_key),
) -> TaskRecord:
    """Validate, enrich, and enqueue a task. Idempotent on ``Idempotency-Key``."""

    # 1. Idempotency check (must run before validation errors so retries behave).
    if idempotency_key is not None:
        if not (1 <= len(idempotency_key) <= 128):
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must be 1-128 characters",
            )
        existing_id = _idempotency_index.get(idempotency_key)
        if existing_id and existing_id in _tasks:
            return TaskRecord(**_tasks[existing_id])

    # 2. Build the canonical record.
    record = task.model_dump()
    record["idempotency_key"] = idempotency_key
    _enrich(record)

    # 3. Persist (and register idempotency).
    _tasks[record["task_id"]] = record
    if idempotency_key is not None:
        _idempotency_index[idempotency_key] = record["task_id"]

    return TaskRecord(**record)


@router.get("/intake", summary="List accepted tasks (most-recent first)")
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    source: str | None = Query(default=None, description="Filter by source"),
    status: str | None = Query(default=None, description="Filter by status"),
    current_user: dict = Depends(verify_api_key),
) -> dict[str, Any]:
    """Paginated read-only view of the in-memory task store."""
    items = list(_tasks.values())
    if source is not None:
        items = [t for t in items if t.get("source") == source]
    if status is not None:
        items = [t for t in items if t.get("status") == status]
    items.sort(key=lambda t: t.get("received_at", ""), reverse=True)
    return {"count": len(items[:limit]), "tasks": items[:limit]}


@router.get("/intake/{task_id}", summary="Fetch a single task by ID")
async def get_task(
    task_id: str,
    current_user: dict = Depends(verify_api_key),
) -> TaskRecord:
    record = _tasks.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return TaskRecord(**record)


@router.delete(
    "/intake/{task_id}",
    status_code=204,
    summary="Drop a task from the intake store",
)
async def delete_task(
    task_id: str,
    current_user: dict = Depends(verify_api_key),
) -> None:
    record = _tasks.pop(task_id, None)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    # Also clear the idempotency index entry so a future identical replay re-creates cleanly.
    idem = record.get("idempotency_key")
    if idem and _idempotency_index.get(idem) == task_id:
        _idempotency_index.pop(idem, None)
