"""Linear API request-count circuit breaker.

The breaker is intentionally local and conservative: it records Linear response
headers/errors, trips cooldown until the reset window when request count is low
or exhausted, and lets broad pollers fail closed without making another Linear
call. It does not mutate Linear and does not know about dispatcher internals.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping

LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER = "LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_OK"
LINEAR_RATE_LIMIT_BLOCKED_MARKER = "LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_BLOCKED"
DEFAULT_STATE_FILE = "linear_rate_limit_state.json"
DEFAULT_MIN_REMAINING = int(os.environ.get("PRISMATIC_LINEAR_MIN_REMAINING", "100"))
DEFAULT_COOLDOWN_SECONDS = int(os.environ.get("PRISMATIC_LINEAR_COOLDOWN_SECONDS", "300"))


@dataclass(frozen=True)
class LinearRateLimitSnapshot:
    ok: bool
    cooldown_active: bool
    reason: str
    remaining: int | None
    limit: int | None
    reset_at: str | None
    cooldown_until: str | None
    last_call_source: str | None
    last_error: str | None
    marker: str = LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "cooldown_active": self.cooldown_active,
            "reason": self.reason,
            "remaining": self.remaining,
            "limit": self.limit,
            "reset_at": self.reset_at,
            "cooldown_until": self.cooldown_until,
            "last_call_source": self.last_call_source,
            "last_error": self.last_error,
            "marker": self.marker,
            "non_claims": {
                "linear_budget_recovered": False,
                "linear_mutation_applied": False,
                "dispatcher_architecture_fixed": False,
            },
        }


class LinearRateLimitCircuitOpen(RuntimeError):
    """Raised when a broad Linear call should be skipped until reset."""


class LinearRateLimitState:
    def __init__(self, state_path: str | Path | None = None, *, min_remaining: int | None = None) -> None:
        self.state_path = Path(state_path) if state_path else default_state_path()
        self.min_remaining = DEFAULT_MIN_REMAINING if min_remaining is None else int(min_remaining)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {"state_error": "unreadable"}
        return raw if isinstance(raw, dict) else {}

    def write(self, state: Mapping[str, Any]) -> None:
        payload = dict(state)
        payload["updated_at"] = now_iso()
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.state_path)

    def snapshot(self) -> LinearRateLimitSnapshot:
        state = self.read()
        cooldown_until = _string(state.get("cooldown_until"))
        active = _is_future(cooldown_until)
        reason = _string(state.get("reason")) or ("cooldown active" if active else "no cooldown recorded")
        return LinearRateLimitSnapshot(
            ok=not active,
            cooldown_active=active,
            reason=reason,
            remaining=_int_or_none(state.get("remaining")),
            limit=_int_or_none(state.get("limit")),
            reset_at=_string(state.get("reset_at")),
            cooldown_until=cooldown_until,
            last_call_source=_string(state.get("last_call_source")),
            last_error=_string(state.get("last_error")),
        )

    def ensure_allowed(self, *, source: str = "unknown") -> None:
        snapshot = self.snapshot()
        if snapshot.cooldown_active:
            raise LinearRateLimitCircuitOpen(
                f"Linear rate-limit cooldown active until {snapshot.cooldown_until}: {snapshot.reason}"
            )
        state = self.read()
        state["last_call_source"] = source
        self.write(state)

    def record_response_headers(self, headers: Mapping[str, Any], *, source: str = "unknown") -> LinearRateLimitSnapshot:
        remaining = _header_int(headers, "x-ratelimit-requests-remaining")
        limit = _header_int(headers, "x-ratelimit-requests-limit")
        reset_at = _header_str(headers, "x-ratelimit-requests-reset")
        cooldown_until: str | None = None
        reason = "headers observed"
        if remaining is not None and remaining <= self.min_remaining:
            cooldown_until = reset_at or future_iso(DEFAULT_COOLDOWN_SECONDS)
            reason = f"Linear request remaining {remaining} <= floor {self.min_remaining}"
        payload = {
            "remaining": remaining,
            "limit": limit,
            "reset_at": reset_at,
            "cooldown_until": cooldown_until,
            "reason": reason,
            "last_call_source": source,
            "last_error": None,
            "marker": LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
        }
        self.write(payload)
        return self.snapshot()

    def trip(self, *, reason: str, reset_at: str | None = None, source: str = "unknown", remaining: int | None = None, limit: int | None = None) -> LinearRateLimitSnapshot:
        cooldown_until = reset_at or future_iso(DEFAULT_COOLDOWN_SECONDS)
        self.write(
            {
                "remaining": remaining,
                "limit": limit,
                "reset_at": reset_at,
                "cooldown_until": cooldown_until,
                "reason": reason,
                "last_call_source": source,
                "last_error": reason,
                "marker": LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
            }
        )
        return self.snapshot()

    def clear(self, *, source: str = "operator") -> LinearRateLimitSnapshot:
        self.write(
            {
                "remaining": None,
                "limit": None,
                "reset_at": None,
                "cooldown_until": None,
                "reason": f"cleared by {source}",
                "last_call_source": source,
                "last_error": None,
                "marker": LINEAR_RATE_LIMIT_CIRCUIT_BREAKER_MARKER,
            }
        )
        return self.snapshot()


def default_state_path() -> Path:
    explicit = os.environ.get("PRISMATIC_LINEAR_RATE_LIMIT_STATE")
    if explicit:
        return Path(explicit)
    state_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", str(Path.cwd() / "prismatic_state")))
    return state_dir / DEFAULT_STATE_FILE


def get_linear_rate_limit_snapshot(*, state_path: str | Path | None = None) -> dict[str, Any]:
    return LinearRateLimitState(state_path).snapshot().as_dict()


def ensure_linear_circuit_closed(*, source: str = "unknown", state_path: str | Path | None = None) -> None:
    LinearRateLimitState(state_path).ensure_allowed(source=source)


def record_linear_response_headers(headers: Mapping[str, Any], *, source: str = "unknown", state_path: str | Path | None = None) -> dict[str, Any]:
    return LinearRateLimitState(state_path).record_response_headers(headers, source=source).as_dict()


def trip_linear_circuit_from_error(error_payload: Any, *, source: str = "unknown", state_path: str | Path | None = None) -> dict[str, Any] | None:
    reason, reset_at, remaining, limit = parse_rate_limit_error(error_payload)
    if not reason:
        return None
    return LinearRateLimitState(state_path).trip(reason=reason, reset_at=reset_at, source=source, remaining=remaining, limit=limit).as_dict()


def parse_rate_limit_error(error_payload: Any) -> tuple[str | None, str | None, int | None, int | None]:
    errors = []
    if isinstance(error_payload, Mapping):
        if isinstance(error_payload.get("errors"), list):
            errors = error_payload["errors"]
        else:
            errors = [error_payload]
    elif isinstance(error_payload, list):
        errors = error_payload
    else:
        text = str(error_payload)
        if "RATELIMITED" in text or "Rate limit exceeded" in text:
            return "Linear API rate limit exceeded", None, None, None
        return None, None, None, None

    for error in errors:
        if not isinstance(error, Mapping):
            continue
        message = str(error.get("message") or "")
        raw_extensions = error.get("extensions")
        extensions: Mapping[str, Any] = raw_extensions if isinstance(raw_extensions, Mapping) else {}
        code = str(extensions.get("code") or extensions.get("type") or "")
        if "RATELIMITED" not in code.upper() and "rate limit" not in message.lower():
            continue
        raw_meta = extensions.get("meta")
        meta: Mapping[str, Any] = raw_meta if isinstance(raw_meta, Mapping) else {}
        raw_result = meta.get("rateLimitResult")
        result: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
        reset_at = _string(result.get("resetAt") or result.get("reset_at") or result.get("reset"))
        remaining = _int_or_none(result.get("remaining") or result.get("requestsRemaining"))
        limit = _int_or_none(result.get("limit") or result.get("requestsLimit"))
        return message or "Linear API rate limit exceeded", reset_at, remaining, limit
    return None, None, None, None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def future_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def _is_future(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = _parse_dt(value)
    except ValueError:
        return False
    return parsed > datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(value) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _header_str(headers: Mapping[str, Any], key: str) -> str | None:
    for candidate in (key, key.lower(), key.upper(), key.title()):
        if candidate in headers:
            value = headers[candidate]
            return str(value) if value is not None else None
    try:
        value = headers.get(key)  # type: ignore[attr-defined]
    except Exception:
        return None
    return str(value) if value is not None else None


def _header_int(headers: Mapping[str, Any], key: str) -> int | None:
    return _int_or_none(_header_str(headers, key))


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
