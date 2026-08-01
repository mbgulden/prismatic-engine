"""Budget cap persistence and dispatch guard helpers.

The Resources panel uses this module as the shared contract between the
FastAPI gateway and the dispatcher. It intentionally stores plain JSON under
``~/.prismatic`` so the guard works without a database migration and can be
inspected/edited by operators when the gateway is offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BUDGET_CAPS: dict[str, Any] = {
    "daily_limit": 15.0,
    "per_model": {},
    "auto_pause": True,
}


def budget_caps_path() -> Path:
    """Return the operator-local budget caps JSON path."""

    return Path("~/.prismatic/budget_caps.json").expanduser()


def budget_caps_configured(path: Path | None = None) -> bool:
    """Return True when an operator has explicitly saved budget caps."""

    return (path or budget_caps_path()).exists()


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_budget_caps(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize untrusted budget cap input into the stored contract."""

    raw = raw or {}
    defaults = DEFAULT_BUDGET_CAPS
    per_model = raw.get("per_model")
    if not isinstance(per_model, dict):
        per_model = {}

    normalized_per_model: dict[str, float] = {}
    for model, value in per_model.items():
        if not isinstance(model, str) or not model.strip():
            continue
        normalized_per_model[model.strip()] = max(0.0, _coerce_float(value, 0.0))

    return {
        "daily_limit": max(0.0, _coerce_float(raw.get("daily_limit"), defaults["daily_limit"])),
        "per_model": normalized_per_model,
        "auto_pause": bool(raw.get("auto_pause", defaults["auto_pause"])),
    }


def read_budget_caps(path: Path | None = None) -> dict[str, Any]:
    """Read budget caps from disk, returning defaults on missing/invalid JSON."""

    caps_path = path or budget_caps_path()
    if not caps_path.exists():
        return dict(DEFAULT_BUDGET_CAPS)
    try:
        raw = json.loads(caps_path.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_BUDGET_CAPS)
    if not isinstance(raw, dict):
        return dict(DEFAULT_BUDGET_CAPS)
    return normalize_budget_caps(raw)


def write_budget_caps(body: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Persist normalized budget caps and return the stored value."""

    caps_path = path or budget_caps_path()
    caps_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_budget_caps(body)
    caps_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
    return normalized


@dataclass(frozen=True)
class BudgetDecision:
    """Decision returned by the dispatch budget guard."""

    allowed: bool
    reason: str
    caps: dict[str, Any]
    daily_spend: float


def evaluate_budget_caps(daily_spend: float, caps: dict[str, Any] | None = None) -> BudgetDecision:
    """Return whether dispatch is allowed under the current caps.

    ``daily_spend`` is intentionally a plain number so callers can source it
    from telemetry credits, provider dollars, or a test double without coupling
    this module to the telemetry collector.
    """

    normalized = normalize_budget_caps(caps or read_budget_caps())
    spend = max(0.0, _coerce_float(daily_spend, 0.0))
    daily_limit = normalized["daily_limit"]
    if normalized["auto_pause"] and daily_limit > 0 and spend >= daily_limit:
        return BudgetDecision(
            allowed=False,
            reason=f"Daily budget cap reached ({spend:.2f} >= {daily_limit:.2f})",
            caps=normalized,
            daily_spend=spend,
        )
    return BudgetDecision(
        allowed=True,
        reason="within budget caps",
        caps=normalized,
        daily_spend=spend,
    )
