"""
prismatic/breaker/api.py — FastAPI Router for Breaker Management

Provides REST endpoints for the Prismatic Breaker system, exposed via
the existing gateway server on Port 9000.

Authentication: Bearer token from credentials.json (BREAKER_API_TOKEN).
If no token is configured, auth is bypassed (development mode).

Endpoints
---------
GET  /breaker/status          — List all breaker states
GET  /breaker/status/{issue_id} — Inspect a single breaker
POST /breaker/correct         — Inject correction + reset
POST /breaker/clear           — Clear a breaker

Usage
-----
    from prismatic.breaker.api import breaker_router
    app.include_router(breaker_router)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

logger = logging.getLogger("prismatic.breaker.api")

# ── Database path ───────────────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# ── Auth token path ─────────────────────────────────────────────────
DEFAULT_CREDENTIALS_PATH = os.environ.get(
    "PRISMATIC_CREDENTIALS",
    os.path.expanduser("~/.hermes/profiles/orchestrator/credentials.json"),
)

# ── Router ──────────────────────────────────────────────────────────
breaker_router = APIRouter(prefix="/breaker", tags=["breaker"])


# ═══════════════════════════════════════════════════════════════════
# Request / Response models
# ═══════════════════════════════════════════════════════════════════


class BreakerState(BaseModel):
    """Serializable breaker state returned by the API."""

    issue_id: str
    agent: str
    micro_count: int = 0
    macro_count: int = 0
    last_seen: str | None = None
    tripped: bool = False
    tripped_at: str | None = None
    tripped_duration_s: float | None = None


class CorrectRequest(BaseModel):
    issue_id: str = Field(..., description="Issue identifier (e.g., GRO-1234)")
    message: str = Field(..., description="Correction description from operator")


class ClearRequest(BaseModel):
    issue_id: str = Field(..., description="Issue identifier (e.g., GRO-1234)")


class CorrectResponse(BaseModel):
    success: bool
    issue_id: str
    message: str
    was_tripped: bool
    micro_count_reset: int
    macro_count_reset: int


class ClearResponse(BaseModel):
    success: bool
    issue_id: str
    was_tripped: bool
    micro_count_cleared: int
    macro_count_cleared: int


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


# ═══════════════════════════════════════════════════════════════════
# Authentication
# ═══════════════════════════════════════════════════════════════════


def _load_breaker_token() -> str | None:
    """Load BREAKER_API_TOKEN from credentials.json."""
    try:
        path = Path(DEFAULT_CREDENTIALS_PATH)
        if not path.exists():
            return None
        with open(path) as f:
            creds = json.load(f)
        token_entry = creds.get("credentials", {}).get("BREAKER_API_TOKEN", {})
        return token_entry.get("value") or None
    except (json.JSONDecodeError, KeyError, OSError):
        return None


async def verify_bearer(request: Request) -> None:
    """Dependency: validate Bearer token from Authorization header.

    If BREAKER_API_TOKEN is not configured (development mode), auth
    is bypassed and a warning is logged.
    """
    token = _load_breaker_token()
    if token is None:
        logger.warning(
            "BREAKER_API_TOKEN not configured — auth bypassed (development mode)"
        )
        return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. "
                   "Expected: Bearer <token>",
        )

    provided = auth_header.removeprefix("Bearer ").strip()
    if provided != token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Bearer token",
        )


# ═══════════════════════════════════════════════════════════════════
# Database helpers
# ═══════════════════════════════════════════════════════════════════


def _get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection to the telemetry database."""
    path = db_path or DEFAULT_DB_PATH
    if not Path(path).exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Telemetry database not found at {path}",
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_state(row: sqlite3.Row) -> BreakerState:
    """Convert a database row to a BreakerState model."""
    tripped_at = row["tripped_at"]
    tripped_duration = None
    if tripped_at and row["tripped"]:
        try:
            tripped_dt = datetime.fromisoformat(tripped_at)
            now = datetime.now(timezone.utc)
            tripped_duration = (now - tripped_dt).total_seconds()
        except (ValueError, TypeError):
            pass

    return BreakerState(
        issue_id=row["issue_id"],
        agent=row["agent"],
        micro_count=row["micro_count"],
        macro_count=row["macro_count"],
        last_seen=row["last_seen"],
        tripped=bool(row["tripped"]),
        tripped_at=tripped_at,
        tripped_duration_s=tripped_duration,
    )


# ═══════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════


@breaker_router.get(
    "/status",
    response_model=list[BreakerState],
    dependencies=[Depends(verify_bearer)],
    summary="List all breaker states",
)
async def list_breakers(tripped: bool = False) -> list[BreakerState]:
    """Return all breaker states, optionally filtered to tripped only."""
    conn = _get_db()
    try:
        query = "SELECT * FROM telemetry_circuit_breakers"
        params: list = []
        if tripped:
            query += " WHERE tripped = 1"
        query += " ORDER BY last_seen DESC"

        rows = conn.execute(query, params).fetchall()
        return [_row_to_state(r) for r in rows]
    finally:
        conn.close()


@breaker_router.get(
    "/status/{issue_id}",
    response_model=BreakerState,
    dependencies=[Depends(verify_bearer)],
    summary="Inspect a single breaker",
)
async def inspect_breaker(issue_id: str) -> BreakerState:
    """Return full state for a specific breaker."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Breaker state not found for {issue_id}",
            )
        return _row_to_state(row)
    finally:
        conn.close()


@breaker_router.post(
    "/correct",
    response_model=CorrectResponse,
    dependencies=[Depends(verify_bearer)],
    summary="Inject HITL correction and reset breaker",
)
async def correct_breaker(req: CorrectRequest) -> CorrectResponse:
    """Inject a human-in-the-loop correction and reset the breaker.

    The correction message is recorded in telemetry_loop_events and
    the breaker state is deleted (fully reset).
    """
    conn = _get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (req.issue_id,),
        ).fetchone()

        was_tripped = bool(row["tripped"]) if row else False
        micro_count = row["micro_count"] if row else 0
        macro_count = row["macro_count"] if row else 0

        if not row:
            # No breaker state to correct
            logger.info(
                "No breaker state for %s — recording correction as annotation",
                req.issue_id,
            )

        # Record the HITL correction as a loop event
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO telemetry_loop_events "
            "(run_id, issue_id, agent, loop_type, trigger, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"breaker-{req.issue_id}",
                req.issue_id,
                "human-operator",
                "circuit_breaker_correction",
                f"HITL correction via API: {req.message} "
                f"(was_tripped={was_tripped}, micro={micro_count}, macro={macro_count})",
                now_iso,
            ),
        )

        # Delete breaker state if it exists
        if row:
            conn.execute(
                "DELETE FROM telemetry_circuit_breakers WHERE issue_id = ?",
                (req.issue_id,),
            )

        conn.commit()
        logger.info(
            "Breaker corrected for %s: micro=%d, macro=%d, was_tripped=%s",
            req.issue_id, micro_count, macro_count, was_tripped,
        )

        return CorrectResponse(
            success=True,
            issue_id=req.issue_id,
            message=req.message,
            was_tripped=was_tripped,
            micro_count_reset=micro_count,
            macro_count_reset=macro_count,
        )
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Correction failed for %s: %s", req.issue_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Correction failed: {e}",
        )
    finally:
        conn.close()


@breaker_router.post(
    "/clear",
    response_model=ClearResponse,
    dependencies=[Depends(verify_bearer)],
    summary="Clear a breaker without correction",
)
async def clear_breaker(req: ClearRequest) -> ClearResponse:
    """Clear a breaker state without correction annotation.

    Use this when the trip was a false positive and no root-cause
    correction is needed.
    """
    conn = _get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (req.issue_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Breaker state not found for {req.issue_id} — nothing to clear",
            )

        micro_count = row["micro_count"]
        macro_count = row["macro_count"]
        was_tripped = bool(row["tripped"])

        # Record the clear action
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO telemetry_loop_events "
            "(run_id, issue_id, agent, loop_type, trigger, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"breaker-{req.issue_id}",
                req.issue_id,
                "human-operator",
                "circuit_breaker_clear",
                f"Breaker cleared via API "
                f"(was_tripped={was_tripped}, micro={micro_count}, macro={macro_count})",
                now_iso,
            ),
        )

        # Delete breaker state
        conn.execute(
            "DELETE FROM telemetry_circuit_breakers WHERE issue_id = ?",
            (req.issue_id,),
        )

        conn.commit()
        logger.info(
            "Breaker cleared for %s: micro=%d, macro=%d, was_tripped=%s",
            req.issue_id, micro_count, macro_count, was_tripped,
        )

        return ClearResponse(
            success=True,
            issue_id=req.issue_id,
            was_tripped=was_tripped,
            micro_count_cleared=micro_count,
            macro_count_cleared=macro_count,
        )
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Clear failed for %s: %s", req.issue_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Clear failed: {e}",
        )
    finally:
        conn.close()
