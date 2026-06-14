"""
Prismatic Gateway — HTTP/gRPC server on port 9000 (live) / 9001 (sandbox).

REST endpoints for:
  - /health — Watchdog health check
  - /locks — Lock state query & management
  - /runs — Agent run log query & ingestion
  - /api/gateway/github — GitHub webhook receiver
  - /api/gateway/linear — Linear webhook receiver

gRPC endpoints (port matching HTTP):
  - prismatic.v1.LockService — Lock state queries
  - prismatic.v1.RunService — Run record queries
  - prismatic.v1.WatchdogService — Watchdog heartbeats
"""
from __future__ import annotations
