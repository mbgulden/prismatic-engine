"""JevConfig: explicit, validated configuration for the typed-decision primitive.

Every knob has a safe default and can be overridden explicitly or via a
``SWARMJEV_*`` environment variable. ``from_env()`` validates each value and
raises ``DecisionError`` on anything it does not understand — the primitive
never runs on misunderstood configuration (fail-closed at construction).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import DecisionError

_TRUTHY = {"1", "true", "yes", "on"}


def _get_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise DecisionError(f"invalid {name}={raw!r}: expected a number") from None
    if minimum is not None and value < minimum:
        raise DecisionError(f"invalid {name}={raw!r}: must be >= {minimum}")
    return value


def _get_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip(), 10)
    except ValueError:
        raise DecisionError(f"invalid {name}={raw!r}: expected an integer") from None
    if minimum is not None and value < minimum:
        raise DecisionError(f"invalid {name}={raw!r}: must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise DecisionError(f"invalid {name}={raw!r}: must be <= {maximum}")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def _get_str(name: str, default: str | None) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


@dataclass(frozen=True)
class JevConfig:
    """All tunables for the primitive. Frozen: safe to share across threads."""

    timeout_s: float = 10.0
    max_attempts: int = 3  # 1 initial + up to 2 retries (transient only)
    backoff_base_s: float = 0.2
    backoff_cap_s: float = 5.0
    breaker_failure_threshold: int = 5
    breaker_reset_timeout_s: float = 30.0
    max_concurrent: int = 20  # bulkhead: concurrent calls per backend
    max_repair_attempts: int = 1  # schema-repair resends, inside the budget
    memoize: bool = False  # exact-hash memoization, opt-in
    trace_path: str | None = None  # JSONL trace sink; unset = log only
    include_meta: bool = True  # include the advisory "meta" payload object

    @classmethod
    def from_env(cls) -> "JevConfig":
        """Build config from SWARMJEV_* env vars. Invalid values raise."""
        return cls(
            timeout_s=_get_float("SWARMJEV_TIMEOUT_S", 10.0, minimum=0.1),
            max_attempts=_get_int("SWARMJEV_MAX_ATTEMPTS", 3, minimum=1, maximum=10),
            backoff_base_s=_get_float("SWARMJEV_BACKOFF_BASE_S", 0.2, minimum=0.0),
            backoff_cap_s=_get_float("SWARMJEV_BACKOFF_CAP_S", 5.0, minimum=0.1),
            breaker_failure_threshold=_get_int(
                "SWARMJEV_BREAKER_THRESHOLD", 5, minimum=1, maximum=100
            ),
            breaker_reset_timeout_s=_get_float(
                "SWARMJEV_BREAKER_RESET_S", 30.0, minimum=1.0
            ),
            max_concurrent=_get_int("SWARMJEV_MAX_CONCURRENT", 20, minimum=1),
            max_repair_attempts=_get_int(
                "SWARMJEV_MAX_REPAIR_ATTEMPTS", 1, minimum=0, maximum=2
            ),
            memoize=_get_bool("SWARMJEV_MEMOIZE", False),
            trace_path=_get_str("SWARMJEV_TRACE_PATH", None),
            include_meta=_get_bool("SWARMJEV_INCLUDE_META", True),
        )
