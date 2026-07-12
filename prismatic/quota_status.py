"""Quota and subscription pressure read models for the Prismatic dashboard.

This module normalizes existing quota/cost primitives into one dashboard-safe
contract. It reads local ledgers and subscription limit definitions; it does not
shell out or call external services.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SOURCE = "vertex_ledger+cost_tracker+credit_tracker+subscription_caps+quota_control_state"


@dataclass(frozen=True)
class QuotaCap:
    id: str
    display_name: str
    provider: str
    model: str
    quota_type: str
    period: str
    limit_value: float | None
    unit: str
    subscription: str
    monthly_cost_usd: float | None = None
    notes: str = ""


DEFAULT_CAPS: tuple[QuotaCap, ...] = (
    QuotaCap(
        id="google-jules-cli-sessions-day",
        display_name="Google Jules / Jules CLI sessions",
        provider="google-jules",
        model="jules-cli",
        quota_type="session",
        period="day",
        limit_value=300,
        unit="sessions",
        subscription="Google Jules / Google AI Ultra entitlement",
        notes="Daily session cap supplied by Michael.",
    ),
    QuotaCap(
        id="google-antigravity-sessions-day",
        display_name="AGY CLI / Google Antigravity daily sessions",
        provider="google-antigravity",
        model="agy-cli",
        quota_type="session",
        period="day",
        limit_value=None,
        unit="sessions",
        subscription="Google Antigravity / Google AI Ultra entitlement",
        notes="Provider exposes daily/weekly caps; exact numeric limit not locally known.",
    ),
    QuotaCap(
        id="google-antigravity-sessions-week",
        display_name="AGY CLI / Google Antigravity weekly sessions",
        provider="google-antigravity",
        model="agy-cli",
        quota_type="session",
        period="week",
        limit_value=None,
        unit="sessions",
        subscription="Google Antigravity / Google AI Ultra entitlement",
        notes="Weekly session cap tracked when provider telemetry is available.",
    ),
    QuotaCap(
        id="google-ai-ultra-credits-month",
        display_name="Google AI Ultra AI credits",
        provider="google-antigravity",
        model="gemini-omni-veo-lyria-pool",
        quota_type="credit_pool",
        period="month",
        limit_value=25000,
        unit="AI credits",
        subscription="$199/mo Google AI Ultra",
        monthly_cost_usd=199,
        notes="Shared pool for AGY SDK Gemini Omni, Veo, Lyria, and related AI Ultra credit consumers.",
    ),
    QuotaCap(
        id="gcp-ai-ultra-cloud-credits-month",
        display_name="GCP credits from Google AI Ultra",
        provider="gcp",
        model="google-cloud",
        quota_type="cloud_credit",
        period="month",
        limit_value=100,
        unit="USD credits",
        subscription="$100/mo Google Cloud credits from Google AI Ultra",
        notes="Monthly Google Cloud credit allocation tied to AI Ultra subscription.",
    ),
    QuotaCap(
        id="openai-codex-subscription-month",
        display_name="OpenAI Codex subscription",
        provider="openai",
        model="codex",
        quota_type="subscription",
        period="month",
        limit_value=200,
        unit="USD subscription value",
        subscription="$200/mo OpenAI Codex subscription",
        monthly_cost_usd=200,
        notes="Subscription-backed usage; daily/weekly/monthly limits depend on provider telemetry.",
    ),
    QuotaCap(
        id="minimax-subscription-month",
        display_name="Minimax subscription",
        provider="minimax",
        model="minimax",
        quota_type="subscription",
        period="month",
        limit_value=50,
        unit="USD subscription value",
        subscription="$50/mo Minimax subscription",
        monthly_cost_usd=50,
        notes="Subscription-backed usage; provider-specific limits tracked when telemetry is available.",
    ),
    QuotaCap(
        id="deepseek-api-rates",
        display_name="DeepSeek standard API rates",
        provider="deepseek",
        model="deepseek-api",
        quota_type="api_rate",
        period="usage",
        limit_value=None,
        unit="USD metered API",
        subscription="Standard API rates",
        notes="Metered API provider; cost pressure is derived from cost ledger where available.",
    ),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")).expanduser()


def event_router_db_path() -> Path:
    return Path(os.environ.get("PRISMATIC_EVENT_ROUTER_DB", str(_state_dir() / "event_router.db"))).expanduser()


def cost_db_path() -> Path:
    return Path(os.environ.get("PRISMATIC_COST_DB", str(Path("~/.prismatic/cost.db").expanduser()))).expanduser()


def _period_start(period: str, now: datetime) -> datetime | None:
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _ledger_usage(provider: str, model: str, period: str, db_path: Path, now: datetime) -> dict[str, Any]:
    start = _period_start(period, now)
    if start is None or not db_path.exists():
        return {"count": None, "credits_spent": None, "last_recorded_at": None}
    try:
        with sqlite3.connect(str(db_path)) as conn:
            if not _sqlite_table_exists(conn, "telemetry_credit_ledger"):
                return {"count": None, "credits_spent": None, "last_recorded_at": None}
            like_provider = f"%{provider}%"
            like_model = f"%{model}%"
            row = conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(credits_spent), 0), MAX(recorded_at)
                FROM telemetry_credit_ledger
                WHERE recorded_at >= ?
                  AND (provider = ? OR provider LIKE ? OR agent LIKE ? OR model = ? OR model LIKE ?)
                """,
                (start.isoformat(), provider, like_provider, like_provider, model, like_model),
            ).fetchone()
            return {
                "count": int(row[0] or 0),
                "credits_spent": float(row[1] or 0),
                "last_recorded_at": row[2],
            }
    except Exception as exc:
        return {"count": None, "credits_spent": None, "last_recorded_at": None, "error": str(exc)}


def _build_pressure(*, used: float | None, limit: float | None) -> tuple[float | None, float | None, str]:
    if used is None or limit is None or limit <= 0:
        return None, None, "unknown"
    usage_pct = max(0.0, (used / limit) * 100.0)
    remaining_pct = max(0.0, 100.0 - usage_pct)
    if usage_pct >= 100:
        status = "exhausted"
    elif usage_pct >= 90:
        status = "critical"
    elif usage_pct >= 75:
        status = "warning"
    else:
        status = "ok"
    return round(usage_pct, 2), round(remaining_pct, 2), status


def _subscription_items(db_path: Path, now: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for cap in DEFAULT_CAPS:
        usage = _ledger_usage(cap.provider, cap.model, cap.period, db_path, now)
        used: float | None
        if cap.quota_type == "session":
            used = _safe_float(usage.get("count"))
        elif cap.quota_type == "credit_pool":
            used = _safe_float(usage.get("credits_spent"))
        elif cap.quota_type in {"cloud_credit", "subscription"}:
            used = None
        else:
            used = _safe_float(usage.get("credits_spent"))
        usage_pct, remaining_pct, status = _build_pressure(used=used, limit=cap.limit_value)
        remaining_value = None
        if used is not None and cap.limit_value is not None:
            remaining_value = max(cap.limit_value - used, 0.0)
        items.append(
            {
                "id": cap.id,
                "display_name": cap.display_name,
                "provider": cap.provider,
                "model": cap.model,
                "metric_type": cap.quota_type,
                "quota_type": cap.quota_type,
                "period": cap.period,
                "subscription": cap.subscription,
                "monthly_cost_usd": cap.monthly_cost_usd,
                "usage": used,
                "limit_value": cap.limit_value,
                "remaining_value": remaining_value,
                "usage_pct": usage_pct,
                "remaining_pct": remaining_pct,
                "status": status,
                "exhausted": status == "exhausted",
                "unit": cap.unit,
                "reset_time": _next_reset(cap.period, now),
                "last_recorded_at": usage.get("last_recorded_at"),
                "notes": cap.notes,
                "source": "subscription_caps+telemetry_credit_ledger",
            }
        )
    return items


def _next_reset(period: str, now: datetime) -> str | None:
    if period == "day":
        return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
    if period == "week":
        start = now - timedelta(days=now.weekday())
        return (start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=7)).isoformat()
    if period == "month":
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    return None


def _vertex_items(db_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not db_path.exists():
        return [], {"available": False, "reason": "event_router.db not found", "db_path": str(db_path)}
    try:
        from prismatic.vertex_telemetry import VertexBillingLedger

        summary = VertexBillingLedger(str(db_path)).get_status_summary()
        records = []
        for rec in summary.get("quota_records", []):
            util = _safe_float(rec.get("utilization_pct"))
            remaining_pct = None if util is None else max(0.0, 100.0 - util)
            limit = _safe_float(rec.get("limit_value"))
            usage = _safe_float(rec.get("usage"))
            status = "unknown"
            if util is not None:
                if util >= 100:
                    status = "exhausted"
                elif util >= 90:
                    status = "critical"
                elif util >= 75:
                    status = "warning"
                else:
                    status = "ok"
            records.append(
                {
                    "id": f"vertex-{rec.get('region')}-{rec.get('model')}-{rec.get('metric_type')}",
                    "display_name": f"Vertex {rec.get('model')} {rec.get('metric_type')} · {rec.get('region')}",
                    "provider": "gcp-vertex-ai",
                    "model": rec.get("model"),
                    "region": rec.get("region"),
                    "metric_type": rec.get("metric_type"),
                    "quota_type": "api_quota",
                    "period": "provider_window",
                    "usage": usage,
                    "limit_value": limit,
                    "remaining_value": rec.get("remaining_value"),
                    "usage_pct": util,
                    "remaining_pct": round(remaining_pct, 2) if remaining_pct is not None else None,
                    "status": status,
                    "exhausted": status == "exhausted",
                    "unit": rec.get("metric_type"),
                    "reset_time": None,
                    "last_recorded_at": rec.get("recorded_at"),
                    "source": "gcp_vertex_quota_snapshots",
                }
            )
        return records, {"available": True, **summary, "db_path": str(db_path)}
    except Exception as exc:
        return [], {"available": False, "reason": str(exc), "db_path": str(db_path)}


def _cost_summary() -> dict[str, Any]:
    path = cost_db_path()
    try:
        from prismatic.cost.tracker import cost_summary

        if path.exists():
            return {"available": True, "db_path": str(path), **cost_summary(db_path=path)}
        return {"available": False, "db_path": str(path), "reason": "cost.db not found"}
    except Exception as exc:
        return {"available": False, "db_path": str(path), "reason": str(exc)}


def _ai_ultra_summary(db_path: Path) -> dict[str, Any]:
    try:
        from prismatic.credit_tracker import AIUltraCreditTracker, MONTHLY_ALLOCATION_LIMIT

        tracker = AIUltraCreditTracker(str(db_path))
        spent = tracker.calculate_monthly_spent()
        remaining = tracker.get_remaining_credits()
        velocity = tracker.calculate_burn_velocity()
        warning = tracker.evaluate_exhaustion_warning()
        return {
            "available": True,
            "limit": MONTHLY_ALLOCATION_LIMIT,
            "spent": spent,
            "remaining": remaining,
            "remaining_pct": round((remaining / MONTHLY_ALLOCATION_LIMIT) * 100, 2) if MONTHLY_ALLOCATION_LIMIT else None,
            "burn_velocity_per_hour": velocity,
            "warning": warning,
            "db_path": str(db_path),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc), "db_path": str(db_path)}


def build_quota_status(control_state: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _now()
    db_path = event_router_db_path()
    vertex_current, vertex_summary = _vertex_items(db_path)
    subscription_current = _subscription_items(db_path, now)
    current = vertex_current + subscription_current
    status_counts: dict[str, int] = {"ok": 0, "warning": 0, "critical": 0, "exhausted": 0, "unknown": 0}
    for item in current:
        status_counts[item.get("status", "unknown")] = status_counts.get(item.get("status", "unknown"), 0) + 1
    latest_times = [
        ts for ts in (_parse_ts(item.get("last_recorded_at")) for item in current) if ts is not None
    ]
    last_snapshot = max(latest_times).isoformat() if latest_times else None
    snapshot_age_sec = None
    if latest_times:
        snapshot_age_sec = max(0, int((now - max(latest_times)).total_seconds()))
    recent_events = []
    for error in vertex_summary.get("latest_errors", []) if isinstance(vertex_summary, dict) else []:
        recent_events.append(
            {
                "timestamp": error.get("recorded_at"),
                "model": error.get("location") or "vertex",
                "event_type": error.get("error_type") or "quota_poll_error",
                "details": error.get("error_message") or error.get("source"),
                "source": "gcp_vertex_poll_errors",
            }
        )
    pressure = "ok"
    if status_counts.get("exhausted"):
        pressure = "exhausted"
    elif status_counts.get("critical"):
        pressure = "critical"
    elif status_counts.get("warning"):
        pressure = "warning"
    elif status_counts.get("unknown") == len(current):
        pressure = "unknown"
    cost = _cost_summary()
    ai_ultra = _ai_ultra_summary(db_path)
    state = control_state or {"actions": [], "last_poll": None}
    return {
        "source": SOURCE,
        "generated_at": now.isoformat(),
        "snapshot_at": last_snapshot,
        "snapshot_age_sec": snapshot_age_sec,
        "last_poll": state.get("last_poll"),
        "empty": len(vertex_current) == 0 and not any(item.get("usage") not in (None, 0, 0.0) for item in subscription_current),
        "pressure": pressure,
        "status_counts": status_counts,
        "current": current,
        "provider_pressure": _provider_pressure(current),
        "subscription_pressure": [item for item in subscription_current],
        "model_pressure": current,
        "cost_summary": cost,
        "ai_ultra_credits": ai_ultra,
        "recent_events": recent_events,
        "control_actions": state.get("actions", [])[-20:],
        "evidence": {
            "event_router_db": str(db_path),
            "event_router_db_exists": db_path.exists(),
            "vertex_ledger_available": vertex_summary.get("available", False),
            "vertex_quota_records": len(vertex_current),
            "subscription_caps": len(DEFAULT_CAPS),
            "cost_summary_available": cost.get("available", False),
            "ai_ultra_credit_tracker_available": ai_ultra.get("available", False),
            "poll_mode": "record-intent-only unless explicit in-process poll is enabled",
        },
    }


def _provider_pressure(items: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    severity_rank = {"unknown": 0, "ok": 1, "warning": 2, "critical": 3, "exhausted": 4}
    for item in items:
        provider = str(item.get("provider") or "unknown")
        bucket = grouped.setdefault(provider, {"status": "unknown", "items": 0, "critical_items": 0, "exhausted_items": 0})
        bucket["items"] += 1
        status = str(item.get("status") or "unknown")
        if severity_rank.get(status, 0) > severity_rank.get(bucket["status"], 0):
            bucket["status"] = status
        if status == "critical":
            bucket["critical_items"] += 1
        if status == "exhausted":
            bucket["exhausted_items"] += 1
    return grouped
