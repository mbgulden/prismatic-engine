"""Stranded-work verdict pipeline (T1 deterministic plan, item 3).

DRY-RUN ONLY. For every open fleet PR this computes a verdict against
the 7-day no-strand line and appends the decision records to the
dry-run log. This module performs NO GitHub writes: it never closes,
merges, comments on, or labels any PR, and it imports nothing capable
of making network calls (stdlib only).

Planned live behavior — verdicts made visible through comment + label
+ digest — is NOT implemented here. Live verdicts require Michael's
review of the dry-run log.

Every evaluated PR produces exactly one decision record: decisions are
never silent. Records are JSONL::

    {"pr", "title", "author", "age_days", "verdict", "recommended",
     "reason", "mode", "ts"}

``verdict`` is one of ``watching`` (fresh), ``approaching`` (nearing the
line), ``overdue`` (past the line), or ``skipped`` (unparseable input —
still recorded, never dropped silently). ``recommended`` is set only
for ``overdue`` and names what the live pipeline WOULD do.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODE = "dry_run"

VERDICT_LINE_DAYS = 7
APPROACHING_DAYS = 5

T1_CLASSES = ("docs", "chore", "dep_bump")

VERDICTS = ("watching", "approaching", "overdue", "skipped")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _record(
    pr: Any,
    title: str,
    author: str,
    age_days: float | None,
    verdict: str,
    recommended: str | None,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "pr": pr,
        "title": title,
        "author": author,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "verdict": verdict,
        "recommended": recommended,
        "reason": reason,
        "mode": MODE,
        "ts": now.isoformat(),
    }


def _recommend_overdue(pr: dict[str, Any]) -> tuple[str, str]:
    """What the live pipeline WOULD do with an overdue PR. Deterministic."""
    superseded_by = pr.get("superseded_by")
    if superseded_by is not None:
        return "supersede", f"superseded by PR #{superseded_by}"
    if (
        pr.get("change_class") in T1_CLASSES
        and pr.get("ci_green")
        and not pr.get("policy_excluded")
    ):
        return "merge", "T1 class with green CI"
    return "reject", "stale: no verdict within 7 days"


def evaluate_pr(pr: Any, now: datetime | None = None) -> dict[str, Any]:
    """Compute one PR's verdict. Never raises; unparseable input still records."""
    now = now or _utcnow()
    if not isinstance(pr, dict):
        return _record(
            None, "", "", None, "skipped", None, "unparseable PR entry", now
        )
    ref = pr.get("pr", pr.get("number"))
    title = str(pr.get("title") or "")
    author = str(pr.get("author") or "")
    if ref is None:
        return _record(
            None, title, author, None, "skipped", None, "missing PR number", now
        )
    created = _parse_ts(pr.get("created_at"))
    if created is None:
        return _record(
            ref, title, author, None, "skipped", None, "unparseable created_at", now
        )
    age_days = (now - created).total_seconds() / 86400.0
    if age_days < APPROACHING_DAYS:
        return _record(
            ref,
            title,
            author,
            age_days,
            "watching",
            None,
            f"fresh — {age_days:.1f}d old, inside the 7-day line",
            now,
        )
    if age_days < VERDICT_LINE_DAYS:
        return _record(
            ref,
            title,
            author,
            age_days,
            "approaching",
            None,
            f"nearing the 7-day verdict line ({age_days:.1f}d old)",
            now,
        )
    recommended, why = _recommend_overdue(pr)
    return _record(
        ref,
        title,
        author,
        age_days,
        "overdue",
        recommended,
        f"past the 7-day line ({age_days:.1f}d old) — dry-run recommendation: "
        f"{recommended} ({why}); no action taken",
        now,
    )


def run_pipeline(
    prs: list[Any] | None,
    *,
    log_path: str | Path,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every PR and append one JSONL record per PR to the dry-run log.

    Returns the records. Appends only; never modifies or deletes the log.
    Performs no GitHub writes of any kind.
    """
    now = now or _utcnow()
    records = [evaluate_pr(pr, now) for pr in (prs or [])]
    path = Path(log_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return records
