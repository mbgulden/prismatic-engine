"""Observability: one trace record per decide() call.

A trace record captures everything needed to understand, replay, and audit a
decision WITHOUT storing raw state values (which may carry customer data) or
credentials:

- correlation: trace_id, timestamp
- versions: schema_version, model, prompt refs (``prompt_id@version#hash``)
- input identity: state_hash (SHA-256 of canonical state), state_keys
- performance: per-phase latencies, attempts, repair attempts
- cost: tokens in/out, cost_usd
- payload: the returned probability distribution
- flags: fallback_used, deterministic, abstained, budget_exceeded,
  breaker_open, repaired, memo_hit
- failures: error type + message (never credentials, never state values)

Fail-open seam: telemetry failure must NEVER fail the decision. ``emit_trace``
swallows every exception — a broken trace sink produces a bounded
observability gap, never a blocked agent. (Fail-closed is for the decision
path; fail-open is correct for observability — the hazard of acting on a
fabricated decision outweighs the hazard of a missing trace.)
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from .redact import redact_pii

logger = logging.getLogger("prismatic.jev.trace")


@dataclass(frozen=True)
class TraceRecord:
    """One decision, fully described, with no raw state and no secrets."""

    trace_id: str
    ts: str
    backend: str
    model: str | None
    schema_version: str
    prompt_refs: dict[str, str] = field(default_factory=dict)
    state_hash: str = ""
    state_keys: list[str] = field(default_factory=list)
    phases_ms: dict[str, float] = field(default_factory=dict)
    attempts: int = 1
    repair_attempts: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        # Error text is the one free-form string in the record: redact secret
        # assignments here so every producer gets the invariant, not just the
        # client. (No raw state values or credentials ever enter a record.)
        if self.error:
            object.__setattr__(self, "error", redact_pii(self.error))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _append_jsonl(path: str, line: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def emit_trace(record: TraceRecord, *, path: str | None = None) -> None:
    """Emit one trace record. NEVER raises — telemetry is the fail-open seam.

    Writes JSONL to ``path`` when given, and always logs at DEBUG on the
    ``prismatic.jev.trace`` logger. Any failure (disk, permissions,
    serialization) is swallowed: a missing trace must never block a decision.
    """
    try:
        line = json.dumps(record.to_dict(), sort_keys=True, default=str)
        if path:
            _append_jsonl(path, line)
        logger.debug("jev trace: %s", line)
    except Exception:
        # Fail-open, deliberately: observability gaps are bounded and
        # recoverable; blocking the decision over a broken sink is not.
        pass
