"""Autonomy digest: a bounded, counts-only autonomy section for the overnight report.

Phase 4 of the approved earned-autonomy plan. This module is deliberately
dependency-free: it works on plain data (dicts/lists) and never imports the
trust ledger, learn loop, or autonomy engine modules at module level, so the
report generator keeps working whether those live in a later PR or not.

Boundedness rule (approved plan): the section must fit on one screen —
counts and bounded exception lists, never raw event logs. Revocations are
capped at MAX_EXCEPTIONS with an explicit truncation count so nothing is
silently dropped.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_EXCEPTIONS = 10

STRANDED_FRAGMENT = "~/.prismatic/audit/stranded-verdicts-issued.json"

DEFAULT_JANITOR: dict[str, Any] = {
    "removed": 0,
    "archived": 0,
    "skipped": 0,
    "source": "janitor phase pending",
}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _pause_total(tier_status: dict) -> int | None:
    for key in ("pauses_trailing_30", "pause_count", "jev_pauses_total"):
        total = _as_int(tier_status.get(key))
        if total is not None:
            return total
    return None


def _pause_agreed(tier_status: dict) -> int | None:
    for key in ("pauses_agreed_trailing_30", "jev_pauses_agreed", "pauses_agreed"):
        agreed = _as_int(tier_status.get(key))
        if agreed is not None:
            return agreed
    return None


def _pause_precision(tier_status: dict) -> float | None:
    precision = tier_status.get("pause_precision")
    if isinstance(precision, bool):
        return None
    return float(precision) if isinstance(precision, (int, float)) else None


def _freeze_active(tier_status: dict) -> bool:
    freeze_until = tier_status.get("promotion_freeze_until")
    if not freeze_until:
        return False
    try:
        until = datetime.fromisoformat(str(freeze_until).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        # Unparseable but present: the ledger is signalling a freeze; trust it.
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def summarize_progress(tier_status: dict | None) -> list[str]:
    """Human-readable ledger progress lines from a phase-1-shaped tier_status dict.

    The dict is accepted as plain data — this module never imports the trust
    ledger. Missing or malformed input yields ["ledger unavailable"], never a
    raise.
    """
    if not isinstance(tier_status, dict):
        return ["ledger unavailable"]
    lines: list[str] = []
    tier = tier_status.get("current_tier")

    clean = tier_status.get("consecutive_clean_by_class")
    targets = tier_status.get("clean_target_by_class")
    if isinstance(clean, dict) and clean:
        pairs = []
        for cls in clean:
            count = clean.get(cls)
            target = targets.get(cls, "?") if isinstance(targets, dict) else "?"
            pairs.append((cls, count, target))
        if len({(count, target) for _, count, target in pairs}) == 1:
            # All classes share the same count/target: one combined line.
            _, count, target = pairs[0]
            classes = "/".join(cls for cls, _, _ in pairs)
            lines.append(f"T{tier}: {count}/{target} clean {classes}")
        else:
            for cls, count, target in pairs:
                lines.append(f"T{tier}: {count}/{target} clean {cls}")

    rollbacks = _as_int(tier_status.get("rollback_count_30d"))
    if rollbacks is not None:
        lines.append(f"rollbacks (30d): {rollbacks}")

    precision = _pause_precision(tier_status)
    total = _pause_total(tier_status)
    if total is not None:
        pretty = f"{precision:.2f}" if precision is not None else "n/a"
        lines.append(f"pause precision: {pretty} ({total} pauses)")

    if _freeze_active(tier_status):
        lines.append(
            f"promotions frozen until {tier_status.get('promotion_freeze_until')}"
        )

    return lines or ["ledger unavailable"]


def _normalize_revocations(
    revocations: Any, max_exceptions: int
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    if isinstance(revocations, list):
        for event in revocations:
            if not isinstance(event, dict):
                continue
            items.append(
                {
                    "ts": event.get("ts") or event.get("timestamp"),
                    "from_tier": event.get("from_tier"),
                    "to_tier": event.get("to_tier"),
                    "reason": event.get("reason"),
                }
            )
    try:
        cap = max(0, int(max_exceptions))
    except (TypeError, ValueError):
        cap = MAX_EXCEPTIONS
    truncated_away = max(0, len(items) - cap)
    return items[:cap], truncated_away


def _normalize_waivers(
    waivers: Any, max_exceptions: int
) -> tuple[list[dict[str, Any]], int]:
    """Bounded contract-waiver list, same shape discipline as revocations."""
    items: list[dict[str, Any]] = []
    if isinstance(waivers, list):
        for entry in waivers:
            if not isinstance(entry, dict):
                continue
            items.append(
                {
                    "pr": entry.get("pr"),
                    "title": entry.get("title"),
                    "ts": entry.get("ts") or entry.get("timestamp"),
                }
            )
    try:
        cap = max(0, int(max_exceptions))
    except (TypeError, ValueError):
        cap = MAX_EXCEPTIONS
    truncated_away = max(0, len(items) - cap)
    return items[:cap], truncated_away


def _normalize_stranded(
    stranded: Any, max_exceptions: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Bounded stranded-work lists for the no-strand pipeline (item 4).

    Input is caller-supplied plain data — the digest never fetches PRs
    itself (same discipline as contract_waivers):
      {"approaching": [{"pr", "title", "age_days", "author"}],
       "verdicts_issued": [{"pr", "verdict", "reason", "ts"}]}
    "approaching" = fleet PRs nearing the 7-day verdict line, oldest first.
    Returns (approaching[:cap], verdicts[:cap], truncated_away).
    """
    approaching: list[dict[str, Any]] = []
    verdicts: list[dict[str, Any]] = []
    if isinstance(stranded, dict):
        raw_approaching = stranded.get("approaching")
        if isinstance(raw_approaching, list):
            for entry in raw_approaching:
                if not isinstance(entry, dict):
                    continue
                approaching.append(
                    {
                        "pr": entry.get("pr"),
                        "title": entry.get("title"),
                        "age_days": entry.get("age_days"),
                        "author": entry.get("author"),
                    }
                )
        raw_verdicts = stranded.get("verdicts_issued")
        if isinstance(raw_verdicts, list):
            for entry in raw_verdicts:
                if not isinstance(entry, dict):
                    continue
                verdicts.append(
                    {
                        "pr": entry.get("pr"),
                        "verdict": entry.get("verdict"),
                        "reason": entry.get("reason"),
                        "ts": entry.get("ts") or entry.get("timestamp"),
                    }
                )
    try:
        cap = max(0, int(max_exceptions))
    except (TypeError, ValueError):
        cap = MAX_EXCEPTIONS
    truncated_away = max(0, len(approaching) - cap) + max(0, len(verdicts) - cap)
    return approaching[:cap], verdicts[:cap], truncated_away


def build_autonomy_section(
    *,
    tier_status: dict | None = None,
    revocations: list | None = None,
    brake_engaged: bool = False,
    janitor: dict | None = None,
    max_exceptions: int = MAX_EXCEPTIONS,
    auto_merges: dict | None = None,
    contract_waivers: list | None = None,
    stranded: dict | None = None,
) -> dict:
    """Build the bounded autonomy section dict. Never raises on None/empty inputs.

    Keys: tier, progress, auto_merges_by_tier, jev_pauses, revocations,
    revocations_truncated_away, contract_waivers,
    contract_waivers_truncated_away, stranded_work,
    stranded_work_truncated_away, brake, janitor, frozen. Counts and
    bounded exception lists only — never raw event logs.

    stranded: caller-supplied {"approaching": [...], "verdicts_issued": [...]}
    for the no-strand pipeline — fleet PRs nearing the 7-day verdict line
    and verdicts the janitor issued. The digest never fetches PRs itself.

    contract_waivers: plain-data list of {"pr", "title", "ts"} dicts for PRs
    carrying the contract-waiver label — the audited escape hatch. The
    digest never fetches labels itself; callers supply the data (a GitHub
    collector feeding this is a follow-up).

    janitor: full janitor totals arrive with phase 5; until then callers may
    pass their own dict or accept the "janitor phase pending" placeholder.
    """
    try:
        status = tier_status if isinstance(tier_status, dict) else None
        revocation_list, truncated_away = _normalize_revocations(
            revocations, max_exceptions
        )
        waiver_list, waivers_truncated_away = _normalize_waivers(
            contract_waivers, max_exceptions
        )
        stranded_approaching, stranded_verdicts, stranded_truncated = (
            _normalize_stranded(stranded, max_exceptions)
        )
        return {
            "tier": status.get("current_tier") if status else None,
            "progress": summarize_progress(status),
            "auto_merges_by_tier": dict(auto_merges)
            if isinstance(auto_merges, dict)
            else {},
            "jev_pauses": {
                "total": _pause_total(status) if status else None,
                "agreed": _pause_agreed(status) if status else None,
                "precision": _pause_precision(status) if status else None,
            },
            "revocations": revocation_list,
            "revocations_truncated_away": truncated_away,
            "contract_waivers": waiver_list,
            "contract_waivers_truncated_away": waivers_truncated_away,
            "stranded_work": {
                "approaching": stranded_approaching,
                "verdicts_issued": stranded_verdicts,
                "note": "no stranded work"
                if not stranded_approaching and not stranded_verdicts
                else None,
            },
            "stranded_work_truncated_away": stranded_truncated,
            "brake": {"engaged": bool(brake_engaged)},
            "janitor": dict(janitor)
            if isinstance(janitor, dict)
            else dict(DEFAULT_JANITOR),
            "frozen": _freeze_active(status) if status else False,
        }
    except Exception as exc:  # never break report generation
        return {"status": "unavailable", "reason": str(exc)[:200]}


def collect_autonomy_inputs(ledger: Any = None) -> dict:
    """Gather raw autonomy inputs, lazily importing the trust ledger.

    Returns {"tier_status", "revocations", "brake_engaged"}, or
    {"unavailable": True, "reason": ...} when the trust module is absent
    (phases 1-3 unmerged) or the ledger cannot be read.

    The brake state is read directly from the PRISMATIC_AUTONOMY_ENABLED
    environment variable: "0"/"false"/"no" (case-insensitive) means the
    autonomy brake is engaged.

    Note: full janitor totals arrive with phase 5; build_autonomy_section()
    fills a "janitor phase pending" placeholder until then.
    """
    try:
        from prismatic.review_factory import trust
    except ImportError:
        return {"unavailable": True, "reason": "trust_module_absent"}

    if ledger is None:
        try:
            ledger = trust.TrustLedger()
        except Exception as exc:
            return {"unavailable": True, "reason": f"ledger_unavailable: {exc}"[:200]}

    try:
        tier_status = ledger.tier_status()
        events = ledger.events()
    except Exception as exc:
        return {"unavailable": True, "reason": f"ledger_read_failed: {exc}"[:200]}

    revocations = [
        event
        for event in (events or [])
        if isinstance(event, dict) and event.get("event_type") == "tier_revoked"
    ]
    env_value = os.environ.get("PRISMATIC_AUTONOMY_ENABLED", "").strip().lower()
    result: dict[str, Any] = {
        "tier_status": tier_status,
        "revocations": revocations,
        "brake_engaged": env_value in {"0", "false", "no"},
    }
    # Stranded-work fragment written by scripts/stranded_verdict_tick.py.
    # Best-effort: a missing/unparseable fragment just means no stranded data.
    try:
        fragment = json.loads(Path(STRANDED_FRAGMENT).expanduser().read_text())
        if isinstance(fragment, dict):
            result["stranded"] = {
                "approaching": fragment.get("approaching", []),
                "verdicts_issued": fragment.get("verdicts_issued", []),
            }
    except (OSError, ValueError):
        pass
    return result
