"""Public-facing FastAPI gateway server for the Prismatic Engine.

Provides REST API for job submission, credit checks, asset retrieval,
and cluster health — all secured via Bearer token auth.

Usage:
    python -m prismatic.api         # Start on port 8000
    python -m prismatic.api --port 8000 --reload
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from prismatic.api.auth import verify_api_key
from prismatic.api.routers import credits, jobs, router_config

logger = logging.getLogger("prismatic.api")

API_PREFIX = "/api/v1"

app = FastAPI(
    title="Prismatic Engine Public API",
    description="Public-facing API gateway for Prismatic Engine agent orchestration",
    version="0.1.0",
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


# ── Health ────────────────────────────────────────────────


@app.get(f"{API_PREFIX}/health")
async def health(current_user: dict = Depends(verify_api_key)):  # noqa: B008
    from datetime import datetime, timezone

    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get(f"{API_PREFIX}/")
async def root():
    return {
        "service": "Prismatic Engine Public API",
        "version": "0.1.0",
        "docs": f"{API_PREFIX}/docs",
    }


# ── Routers ───────────────────────────────────────────────

app.include_router(credits.router, prefix=API_PREFIX, tags=["credits"])
app.include_router(jobs.router, prefix=API_PREFIX, tags=["jobs"])
app.include_router(router_config.router, prefix=API_PREFIX, tags=["router-config"])


# ── CLI ───────────────────────────────────────────────────


def run() -> None:
    parser = argparse.ArgumentParser(description="Prismatic Engine API Gateway")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    args, _ = parser.parse_known_args()

    # Load .env from env/ directory relative to project root
    env_dir = os.path.join(os.path.dirname(__file__), "..", "..", "env")
    env_path = os.path.join(env_dir, ".env")
    if os.path.isfile(env_path):
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
            logger.info("Loaded env from %s", env_path)
        except ImportError:
            logger.warning("python-dotenv not installed, skipping .env load")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    logger.info("Starting Prismatic API Gateway on %s:%d", args.host, args.port)
    uvicorn.run(
        "prismatic.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    run()
