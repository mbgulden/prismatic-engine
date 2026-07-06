"""Phase 6 guardrails for Prismatic Engine.

This module is intentionally harness-agnostic: it does not know about Hermes,
cron profiles, or Telegram.  Callers hand it event dictionaries and callable
checks; it produces deterministic replay records, smoke-test verdicts,
stall alerts, and rollout stop/go decisions.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


class GuardrailStatus(str, Enum):
    """Common status vocabulary returned by Phase 6 guardrails."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class ReplayRecord:
    """One durable queue event that can be replayed after outage/corruption."""

    event_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReplayRecord":
        return cls(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            payload=dict(data.get("payload") or {}),
            created_at=float(data.get("created_at") or time.time()),
            attempts=int(data.get("attempts") or 0),
            last_error=str(data.get("last_error") or ""),
        )


@dataclass(frozen=True)
class ReplayResult:
    """Replay/backfill execution summary."""

    processed: int
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> GuardrailStatus:
        if self.failed:
            return GuardrailStatus.FAIL
        return GuardrailStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "status": self.status.value,
        }


class ReplayQueue:
    """Append-only JSONL replay queue.

    The queue is deliberately simple.  Production code can mirror live ingest,
    dispatch, artifact, and state-sync events into this file.  After an outage,
    call :meth:`replay` with a handler map to re-drive the live path in original
    order.  Failed records remain visible with incremented attempts.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event_id: str, event_type: str, payload: dict[str, Any]) -> ReplayRecord:
        record = ReplayRecord(event_id=event_id, event_type=event_type, payload=dict(payload))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        return record

    def load(self) -> list[ReplayRecord]:
        if not self.path.exists():
            return []
        records: list[ReplayRecord] = []
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(ReplayRecord.from_dict(json.loads(line)))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid replay record at {self.path}:{lineno}: {exc}") from exc
        return records

    def replay(
        self,
        handlers: dict[str, Callable[[ReplayRecord], None]],
        *,
        event_types: Iterable[str] | None = None,
        since: float | None = None,
    ) -> ReplayResult:
        allowed = set(event_types) if event_types is not None else None
        succeeded: list[str] = []
        failed: dict[str, str] = {}
        processed = 0
        rewritten: list[ReplayRecord] = []

        for record in self.load():
            if allowed is not None and record.event_type not in allowed:
                rewritten.append(record)
                continue
            if since is not None and record.created_at < since:
                rewritten.append(record)
                continue

            processed += 1
            handler = handlers.get(record.event_type)
            if handler is None:
                failed[record.event_id] = f"no handler registered for {record.event_type}"
                rewritten.append(ReplayRecord(**{**record.to_dict(), "attempts": record.attempts + 1, "last_error": failed[record.event_id]}))
                continue

            try:
                handler(record)
            except Exception as exc:  # caller-facing guardrail: record, don't mask
                failed[record.event_id] = str(exc)
                rewritten.append(ReplayRecord(**{**record.to_dict(), "attempts": record.attempts + 1, "last_error": str(exc)}))
            else:
                succeeded.append(record.event_id)
                rewritten.append(record)

        self._rewrite(rewritten)
        return ReplayResult(processed=processed, succeeded=succeeded, failed=failed)

    def _rewrite(self, records: Sequence[ReplayRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(r.to_dict(), sort_keys=True) + "\n" for r in records)
        self.path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class SmokeCheck:
    """A live-path smoke check."""

    name: str
    probe: Callable[[], tuple[bool, str] | bool]


@dataclass(frozen=True)
class SmokeSuiteResult:
    status: GuardrailStatus
    checks: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "checks": self.checks}


class SmokeSuite:
    """Run live-path smoke checks for ingest, dispatch, artifact, and state sync."""

    def __init__(self, checks: Sequence[SmokeCheck]):
        if not checks:
            raise ValueError("SmokeSuite requires at least one check")
        self.checks = list(checks)

    def run(self) -> SmokeSuiteResult:
        results: dict[str, dict[str, Any]] = {}
        failed = False
        for check in self.checks:
            try:
                raw = check.probe()
                if isinstance(raw, tuple):
                    ok, detail = bool(raw[0]), str(raw[1])
                else:
                    ok, detail = bool(raw), "ok" if raw else "failed"
            except Exception as exc:
                ok, detail = False, f"exception: {exc}"
            failed = failed or not ok
            results[check.name] = {"passed": ok, "detail": detail}
        return SmokeSuiteResult(status=GuardrailStatus.FAIL if failed else GuardrailStatus.PASS, checks=results)


@dataclass(frozen=True)
class StallAlert:
    status: GuardrailStatus
    stalled_keys: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"status": self.status.value}


def detect_silent_stalls(
    heartbeats: dict[str, float],
    *,
    now: float | None = None,
    max_age_seconds: float = 300.0,
    alert_path: str = "ops-feed",
) -> StallAlert:
    """Detect keys whose heartbeat has gone silent.

    A forced failure is just an old timestamp.  The caller decides whether the
    concrete alert path is ops feed, Linear comment, webhook, or pager.
    """

    current = time.time() if now is None else now
    stalled = sorted(key for key, ts in heartbeats.items() if current - float(ts) > max_age_seconds)
    if stalled:
        return StallAlert(
            status=GuardrailStatus.FAIL,
            stalled_keys=stalled,
            message=f"silent stall detected for {len(stalled)} key(s); alert via {alert_path}",
        )
    return StallAlert(status=GuardrailStatus.PASS, stalled_keys=[], message="all heartbeats fresh")


@dataclass(frozen=True)
class RolloutDecision:
    status: GuardrailStatus
    go: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "go": self.go, "reasons": self.reasons}


def rollout_gate(
    *,
    smoke: SmokeSuiteResult,
    replay: ReplayResult,
    stalls: StallAlert,
    manual_approval: bool,
    rollback_target: str | None,
) -> RolloutDecision:
    """Explicit stop/go gate for rollout and rollback safety."""

    reasons: list[str] = []
    if smoke.status is not GuardrailStatus.PASS:
        reasons.append("smoke checks failed")
    if replay.status is not GuardrailStatus.PASS:
        reasons.append("replay/backfill failed")
    if stalls.status is not GuardrailStatus.PASS:
        reasons.append(stalls.message)
    if not manual_approval:
        reasons.append("manual rollout approval missing")
    if not rollback_target:
        reasons.append("rollback target missing")

    if reasons:
        return RolloutDecision(status=GuardrailStatus.FAIL, go=False, reasons=reasons)
    return RolloutDecision(status=GuardrailStatus.PASS, go=True, reasons=["all rollout gates passed"])


__all__ = [
    "GuardrailStatus",
    "ReplayQueue",
    "ReplayRecord",
    "ReplayResult",
    "SmokeCheck",
    "SmokeSuite",
    "SmokeSuiteResult",
    "StallAlert",
    "RolloutDecision",
    "detect_silent_stalls",
    "rollout_gate",
]
