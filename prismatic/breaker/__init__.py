"""
prismatic/breaker — Human Intervention (HITL) Breaker Engine

Provides CLI and REST tooling for inspecting and clearing circuit breaker
states in the Prismatic Engine's telemetry database. Designed for human
operators to manually intervene when automatic fallback loops trip.

Components
----------
- cli.py — ``prismatic-breaker`` command-line tool with 4 subcommands
- api.py — FastAPI router for remote breaker management (Bearer auth)

Database
--------
Reads from ``telemetry_circuit_breakers`` in the telemetry SQLite database
(default: ``prismatic_state/event_router.db``).
"""
from __future__ import annotations

__all__: list[str] = []
