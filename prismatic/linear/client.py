"""Linear GraphQL client + per-cycle polling budget gate.

Phase 1 of the dispatcher-removal plan (dispatcher-removal-plan-2026-10-23.md,
kanban t_350929e6): the salvageable heart of prismatic/dispatcher.py — a
budget-gated Linear GraphQL client. Code below is MOVED verbatim from
dispatcher.py (origin/main @ 67bc5f69); only this docstring and the import
header are new.

The dispatch loop in dispatcher.py still owns cycle bookkeeping
(`_POLL_CYCLE_NUMBER`) and the label scan cache; it constructs the
LinearCycleBudget, assigns it to BOTH dispatcher._CURRENT_POLL_BUDGET and
this module's _CURRENT_POLL_BUDGET, so every gql()/skip_budget_section() call
is gated through the client-side budget.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prismatic.linear_rate_limit import (
    LinearRateLimitCircuitOpen,
    ensure_linear_circuit_closed,
    get_linear_rate_limit_snapshot,
    record_linear_response_headers,
    trip_linear_circuit_from_error,
)


class LinearBudgetExhaustedError(RuntimeError):
    """Raised when the Linear API budget is exhausted for this cycle."""


DISPATCHER_POLLING_BUDGET_MARKER = "DISPATCHER_POLLING_BUDGET_OK"
DEFAULT_POLL_MAX_CALLS_PER_CYCLE = int(
    os.environ.get("PRISMATIC_POLL_MAX_LINEAR_CALLS_PER_CYCLE", "6")
)
DEFAULT_LABEL_SCAN_TTL_SECONDS = int(
    os.environ.get("PRISMATIC_POLL_LABEL_SCAN_TTL_SECONDS", "300")
)
DEFAULT_ROUTE_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_ROUTE_SCAN_CADENCE", "10")
)
DEFAULT_AGENT_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_AGENT_SCAN_CADENCE", "1")
)
DEFAULT_RECOVERY_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_RECOVERY_SCAN_CADENCE", "20")
)
DEFAULT_ORIGIN_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_ORIGIN_SCAN_CADENCE", "20")
)
DEFAULT_PIPELINE_SCAN_CADENCE = int(
    os.environ.get("PRISMATIC_POLL_PIPELINE_SCAN_CADENCE", "20")
)
_CURRENT_POLL_BUDGET: "LinearCycleBudget | None" = None
_LAST_POLL_BUDGET_STATUS: dict[str, Any] = {}


@dataclass
class LinearCycleBudget:
    max_calls: int
    cycle_number: int
    poll_fallback_enabled: bool
    calls_used: int = 0
    calls_by_source: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0
    skipped_sections: list[str] = field(default_factory=list)
    last_skip_reason: str | None = None

    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def consume(self, source: str) -> None:
        if self.calls_used >= self.max_calls:
            self.last_skip_reason = f"linear call budget exhausted before {source}"
            raise LinearBudgetExhaustedError(self.last_skip_reason)
        self.calls_used += 1
        self.calls_by_source[source] = self.calls_by_source.get(source, 0) + 1

    def skip(self, section: str, reason: str) -> None:
        if section not in self.skipped_sections:
            self.skipped_sections.append(section)
        self.last_skip_reason = reason

    def as_dict(self, *, rate_limit_cooldown_active: bool = False) -> dict[str, Any]:
        return {
            "marker": DISPATCHER_POLLING_BUDGET_MARKER,
            "poll_fallback_enabled": self.poll_fallback_enabled,
            "cycle_number": self.cycle_number,
            "max_calls_per_cycle": self.max_calls,
            "calls_used_this_cycle": self.calls_used,
            "last_cycle_calls": self.calls_used,
            "calls_remaining_this_cycle": self.remaining(),
            "calls_by_source": dict(sorted(self.calls_by_source.items())),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "skipped_sections": list(self.skipped_sections),
            "last_skip_reason": self.last_skip_reason,
            "rate_limit_cooldown_active": rate_limit_cooldown_active,
            "label_scan_ttl_seconds": label_scan_ttl_seconds(),
            "cadence": {
                "pipeline_scan": scan_cadence("pipeline_scan"),
                "route_scan": scan_cadence("route_scan"),
                "agent_scan": scan_cadence("agent_scan"),
                "recovery_scan": scan_cadence("recovery_scan"),
                "origin_scan": scan_cadence("origin_scan"),
            },
            "non_claims": {
                "webhook_queue_active": False,
                "assigned_agent_event_dispatch": False,
                "linear_mutation_applied": False,
            },
        }


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def poll_fallback_enabled() -> bool:
    value = os.environ.get("PRISMATIC_POLL_FALLBACK_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def poll_max_calls_per_cycle() -> int:
    return _env_int(
        "PRISMATIC_POLL_MAX_LINEAR_CALLS_PER_CYCLE", DEFAULT_POLL_MAX_CALLS_PER_CYCLE
    )


def label_scan_ttl_seconds() -> int:
    return _env_int(
        "PRISMATIC_POLL_LABEL_SCAN_TTL_SECONDS", DEFAULT_LABEL_SCAN_TTL_SECONDS
    )


def scan_cadence(section: str) -> int:
    defaults = {
        "pipeline_scan": DEFAULT_PIPELINE_SCAN_CADENCE,
        "route_scan": DEFAULT_ROUTE_SCAN_CADENCE,
        "agent_scan": DEFAULT_AGENT_SCAN_CADENCE,
        "recovery_scan": DEFAULT_RECOVERY_SCAN_CADENCE,
        "origin_scan": DEFAULT_ORIGIN_SCAN_CADENCE,
    }
    env_names = {
        "pipeline_scan": "PRISMATIC_POLL_PIPELINE_SCAN_CADENCE",
        "route_scan": "PRISMATIC_POLL_ROUTE_SCAN_CADENCE",
        "agent_scan": "PRISMATIC_POLL_AGENT_SCAN_CADENCE",
        "recovery_scan": "PRISMATIC_POLL_RECOVERY_SCAN_CADENCE",
        "origin_scan": "PRISMATIC_POLL_ORIGIN_SCAN_CADENCE",
    }
    return max(1, _env_int(env_names[section], defaults[section]))


def _polling_budget_state_path() -> Path:
    explicit = os.environ.get("PRISMATIC_DISPATCHER_POLLING_BUDGET_STATE")
    if explicit:
        return Path(explicit)
    state_dir = Path(
        os.environ.get("PRISMATIC_STATE_DIR", str(Path.cwd() / "prismatic_state"))
    )
    return state_dir / "dispatcher_polling_budget_state.json"


def _persist_polling_budget_status(status: dict[str, Any]) -> None:
    global _LAST_POLL_BUDGET_STATUS
    _LAST_POLL_BUDGET_STATUS = status
    try:
        path = _polling_budget_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True))
        tmp.replace(path)
    except Exception as exc:
        print(f"[dispatcher] polling budget status write failed: {exc}")


def get_dispatcher_polling_budget_snapshot() -> dict[str, Any]:
    if _LAST_POLL_BUDGET_STATUS:
        return dict(_LAST_POLL_BUDGET_STATUS)
    path = _polling_budget_state_path()
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            return raw
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    snapshot = get_linear_rate_limit_snapshot()
    return {
        "marker": DISPATCHER_POLLING_BUDGET_MARKER,
        "poll_fallback_enabled": poll_fallback_enabled(),
        "cycle_number": 0,
        "max_calls_per_cycle": poll_max_calls_per_cycle(),
        "calls_used_this_cycle": 0,
        "last_cycle_calls": 0,
        "calls_remaining_this_cycle": poll_max_calls_per_cycle(),
        "calls_by_source": {},
        "cache_hits": 0,
        "cache_misses": 0,
        "skipped_sections": [],
        "last_skip_reason": None,
        "rate_limit_cooldown_active": bool(snapshot.get("cooldown_active")),
        "label_scan_ttl_seconds": label_scan_ttl_seconds(),
        "cadence": {
            "pipeline_scan": scan_cadence("pipeline_scan"),
            "route_scan": scan_cadence("route_scan"),
            "agent_scan": scan_cadence("agent_scan"),
            "recovery_scan": scan_cadence("recovery_scan"),
            "origin_scan": scan_cadence("origin_scan"),
        },
        "non_claims": {
            "webhook_queue_active": False,
            "assigned_agent_event_dispatch": False,
            "linear_mutation_applied": False,
        },
    }


def section_due(section: str, cycle_number: int) -> bool:
    cadence = scan_cadence(section)
    return cadence <= 1 or cycle_number % cadence == 0


def skip_budget_section(section: str, reason: str) -> None:
    if _CURRENT_POLL_BUDGET is not None:
        _CURRENT_POLL_BUDGET.skip(section, reason)
    print(f"[dispatcher] polling budget skip {section}: {reason}")


# ═══════════════════════════════════════════════════════════════
# Linear GraphQL helpers
# ═══════════════════════════════════════════════════════════════


def _linear_api_key() -> str:
    """Get the Linear API key from the environment.

    Raises RuntimeError if ``LINEAR_API_KEY`` is not set.
    """
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        raise RuntimeError("LINEAR_API_KEY environment variable is required")
    return key


def gql(
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    source: str = "dispatcher.gql",
) -> dict[str, Any]:
    """Execute a Linear GraphQL query or mutation with request-count circuit breaking.

    Uses the ``LINEAR_API_KEY`` env var for authentication. No
    ``Bearer`` prefix is added — the raw key value is used directly
    as the HTTP ``Authorization`` header (Linear's API token format).
    """
    import urllib.request
    import urllib.error

    try:
        ensure_linear_circuit_closed(source=source)
    except LinearRateLimitCircuitOpen as exc:
        raise LinearBudgetExhaustedError(str(exc)) from exc

    if _CURRENT_POLL_BUDGET is not None:
        _CURRENT_POLL_BUDGET.consume(source)

    api_key = _linear_api_key()
    payload = json.dumps(
        {
            "query": query,
            "variables": variables or {},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,  # No "Bearer" prefix
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            record_linear_response_headers(resp.headers, source=source)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode(errors="replace")
        try:
            error_payload: Any = json.loads(raw_body)
        except json.JSONDecodeError:
            error_payload = raw_body
        tripped = trip_linear_circuit_from_error(error_payload, source=source)
        if tripped:
            raise LinearBudgetExhaustedError(
                f"Linear API rate-limit circuit open: {tripped.get('reason')}"
            ) from exc
        raise RuntimeError(f"Linear API HTTP {exc.code}: {raw_body[:500]}") from exc
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Linear API request failed: {exc}") from exc

    if "errors" in body:
        tripped = trip_linear_circuit_from_error(body, source=source)
        if tripped:
            raise LinearBudgetExhaustedError(
                f"Linear API rate-limit circuit open: {tripped.get('reason')}"
            )
        raise RuntimeError(
            f"Linear API error(s): {json.dumps(body['errors'], indent=2)[:1000]}"
        )

    return body.get("data", {})


def linear_broad_poll_allowed(*, source: str = "dispatcher.broad_poll") -> bool:
    """Return False when request-count cooldown should skip broad Linear scans."""

    snapshot = get_linear_rate_limit_snapshot()
    if snapshot.get("cooldown_active"):
        print(
            "[dispatcher] Linear rate-limit circuit open; "
            f"skipping broad poll source={source} until {snapshot.get('cooldown_until')} "
            f"reason={snapshot.get('reason')}"
        )
        return False
    return True
