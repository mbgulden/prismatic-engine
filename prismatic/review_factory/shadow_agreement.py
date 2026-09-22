"""Agreement-rate comparison for Phase 0 shadow mode.

The nightly job (scheduling out of scope — this module is the pure
comparison logic) joins the shadow observer's merge/skip calls against
Michael's actual PR outcomes and computes the running agreement rate.

Exit criteria (from next-step-plan.md Step 3):
  - >= 30 PRs evaluated in shadow
  - >= 95% agreement between system calls and Michael's actual merges
  - zero cases where the system called "merge" and the PR later needed
    repair, rollback, or human revert ("bad merge calls")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

CALL_MERGE = "merge"
CALL_SKIP = "skip"

ACTUAL_MERGED = "merged"
ACTUAL_CLOSED_UNMERGED = "closed_unmerged"
ACTUAL_OPEN = "open"

SHADOW_MIN_PRS = 30
SHADOW_MIN_AGREEMENT = 0.95


def calls_agree(system_call: str, actual_outcome: str) -> bool | None:
    """Do the system's call and Michael's actual outcome agree?

    Returns None when the PR is still open (not decidable yet).
    Agreement means: system said "merge" exactly when Michael merged.
    """
    if actual_outcome == ACTUAL_OPEN:
        return None
    if system_call not in (CALL_MERGE, CALL_SKIP):
        raise ValueError(f"unknown system call: {system_call!r}")
    if actual_outcome not in (ACTUAL_MERGED, ACTUAL_CLOSED_UNMERGED):
        raise ValueError(f"unknown actual outcome: {actual_outcome!r}")
    return (system_call == CALL_MERGE) == (actual_outcome == ACTUAL_MERGED)


def agreement_rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the agreement rate over joined records.

    Each record: {"system_call": ..., "actual_outcome": ...}.
    Open PRs are excluded from the denominator (not decidable yet).
    """
    decided = [
        r
        for r in records
        if r.get("actual_outcome") in (ACTUAL_MERGED, ACTUAL_CLOSED_UNMERGED)
    ]
    agreed = sum(
        1 for r in decided if calls_agree(r["system_call"], r["actual_outcome"])
    )
    n = len(decided)
    return {
        "n_decided": n,
        "n_agreed": agreed,
        "n_pending": len(records) - n,
        "rate": (agreed / n) if n else None,
    }


def shadow_exit_met(
    records: list[dict[str, Any]],
    bad_merge_calls: int = 0,
) -> dict[str, Any]:
    """Check the measurable Phase 0 exit criteria.

    ``bad_merge_calls``: count of PRs where the system called "merge" but
    the PR later needed repair, rollback, or human revert. Supplied by the
    nightly job from deploy/rollback records; the comparison here is pure.
    """
    stats = agreement_rate(records)
    n = stats["n_decided"]
    rate = stats["rate"]
    checks = {
        "min_prs": n >= SHADOW_MIN_PRS,
        "min_agreement": rate is not None and rate >= SHADOW_MIN_AGREEMENT,
        "zero_bad_merges": bad_merge_calls == 0,
    }
    return {
        **stats,
        "bad_merge_calls": bad_merge_calls,
        "checks": checks,
        "exit_met": all(checks.values()),
    }


# ─────────────────────────────────────────────────────────────────────
# Agreement metric v2: decayed, outcome-classified agreement
# (agreement-metric-v2-spec.md). Everything above this line is the v1
# metric and is untouched; v2 lives alongside it behind the
# ``agreement_metric`` policy flag (default "v1").
# ─────────────────────────────────────────────────────────────────────

# Disagreement classes (spec §4.1).
CLASS_TOO_AGGRESSIVE = "D1"  # Jev said merge, human closed it
CLASS_TOO_CAUTIOUS = "D2"  # Jev said skip, human merged, outcome clean
CLASS_JEV_WAS_RIGHT = "D3"  # Jev said skip, human merged, outcome bad
CLASS_PENDING = "pending"  # Jev said skip, human merged, outcome unknown

# Post-merge outcome values a record may carry (joined by the nightly job
# from the deploy-outcome / rollback feeds; see spec §4.2).
OUTCOME_CLEAN = "clean"
OUTCOME_ROLLBACK = "rolled_back"

# The only correction verdict the classifier understands.
CORRECTION_JEV_WAS_RIGHT = "jev-was-right"

AGREEMENT_METRIC_V1 = "v1"
AGREEMENT_METRIC_V2 = "v2"

# Tunable policy constants (spec §11: approved defaults are half-life 14d,
# outcome window 7d, D2 weight 0.25, D3 excluded not bonused).
AGREEMENT_HALF_LIFE_DAYS = 14
AGREEMENT_OUTCOME_WINDOW_DAYS = 7
AGREEMENT_D1_WEIGHT = 1.0
AGREEMENT_D2_WEIGHT = 0.25
AGREEMENT_D3_WEIGHT = 0.0
AGREEMENT_PENDING_WEIGHT = 0.5
SHADOW_MIN_EFFECTIVE_N = 10


def _parse_decided_at(record: dict[str, Any]) -> datetime | None:
    value = record.get("decided_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def classify_disagreement(record: dict[str, Any]) -> str | None:
    """Classify one shadow record's disagreement. Pure.

    Returns "D1" | "D2" | "D3" | "pending", or None when the record
    agrees or is still undecided (open PR).

    Classification is a pure function of the record: ``system_call``,
    ``actual_outcome``, the joined post-merge ``outcome``
    ("clean" | "rolled_back" | absent), and ``corrected_by``
    ("jev-was-right" from a human correction). D1 is never reclassified:
    the PR never merged, so there is no outcome to observe.
    """
    agree = calls_agree(record.get("system_call"), record.get("actual_outcome"))
    if agree is not False:
        return None
    if record.get("system_call") == CALL_MERGE:
        # Jev said merge, the human closed it. Always a real disagreement.
        return CLASS_TOO_AGGRESSIVE
    # Jev said skip, the human merged: classify by what happened next.
    if record.get("corrected_by") == CORRECTION_JEV_WAS_RIGHT:
        return CLASS_JEV_WAS_RIGHT
    outcome = record.get("outcome")
    if outcome == OUTCOME_ROLLBACK:
        return CLASS_JEV_WAS_RIGHT
    if outcome == OUTCOME_CLEAN:
        return CLASS_TOO_CAUTIOUS
    return CLASS_PENDING


def _is_legacy_unclassifiable(
    record: dict[str, Any],
    *,
    outcome_window_days: int = AGREEMENT_OUTCOME_WINDOW_DAYS,
    as_of: datetime | None = None,
) -> bool:
    """True when a skip/merged disagreement predates the outcome feeds.

    A record with no joined outcome is legacy (not merely pending) when
    the outcome window has already passed — or when there is no
    ``decided_at`` to age it by. Legacy records keep class weight 1.0
    (history is not rewritten generously); decay still applies when
    ``decided_at`` is known.
    """
    if record.get("outcome") is not None:
        return False
    decided = _parse_decided_at(record)
    if decided is None:
        return True
    now = as_of or datetime.now(timezone.utc)
    age_days = (now - decided).total_seconds() / 86400
    return age_days > outcome_window_days


def record_weight(
    record: dict[str, Any],
    *,
    half_life_days: int = AGREEMENT_HALF_LIFE_DAYS,
    outcome_window_days: int = AGREEMENT_OUTCOME_WINDOW_DAYS,
    as_of: datetime | None = None,
) -> float:
    """Weight of one record in the decayed agreement rate. Pure.

    ``class_weight * 2^(-age_days / half_life)``. Open (undecided)
    records weigh 0 — excluded, as in v1. Agreements weigh full (1.0)
    before decay.
    """
    agree = calls_agree(record.get("system_call"), record.get("actual_outcome"))
    if agree is None:
        return 0.0
    cls = classify_disagreement(record)
    if cls is None:
        base = 1.0
    elif cls == CLASS_TOO_AGGRESSIVE:
        base = AGREEMENT_D1_WEIGHT
    elif cls == CLASS_TOO_CAUTIOUS:
        base = AGREEMENT_D2_WEIGHT
    elif cls == CLASS_JEV_WAS_RIGHT:
        base = AGREEMENT_D3_WEIGHT
    elif _is_legacy_unclassifiable(
        record, outcome_window_days=outcome_window_days, as_of=as_of
    ):
        base = 1.0
    else:
        base = AGREEMENT_PENDING_WEIGHT
    decided = _parse_decided_at(record)
    if decided is None:
        return base
    now = as_of or datetime.now(timezone.utc)
    age_days = max(0.0, (now - decided).total_seconds() / 86400)
    return base * (2.0 ** (-age_days / half_life_days))


def decayed_agreement_rate(
    records: list[dict[str, Any]],
    *,
    half_life_days: int = AGREEMENT_HALF_LIFE_DAYS,
    outcome_window_days: int = AGREEMENT_OUTCOME_WINDOW_DAYS,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Decayed, outcome-classified agreement rate. Pure.

    Returns ``n_decided`` (v1 definition), ``n_open`` (undecided, excluded),
    ``effective_n`` (sum of weights), ``agreed_weight``, ``decayed_rate``
    (None when the effective sample is empty), per-class disagreement
    counts (``n_d1``/``n_d2``/``n_d3``/``n_pending``), ``n_legacy``
    (pending-class records treated at legacy weight), and ``trend_7d`` —
    the decayed rate recomputed as of 7 days earlier, so the meter can
    show "climbing back" instead of a stuck number.
    """
    now = as_of or datetime.now(timezone.utc)

    def _stats(rs: list[dict[str, Any]], ref: datetime) -> dict[str, Any]:
        total_w = 0.0
        agreed_w = 0.0
        counts = {
            CLASS_TOO_AGGRESSIVE: 0,
            CLASS_TOO_CAUTIOUS: 0,
            CLASS_JEV_WAS_RIGHT: 0,
            CLASS_PENDING: 0,
        }
        n_legacy = 0
        for r in rs:
            w = record_weight(
                r,
                half_life_days=half_life_days,
                outcome_window_days=outcome_window_days,
                as_of=ref,
            )
            total_w += w
            if calls_agree(r["system_call"], r["actual_outcome"]):
                agreed_w += w
            else:
                cls = classify_disagreement(r)
                counts[cls] += 1
                if cls == CLASS_PENDING and _is_legacy_unclassifiable(
                    r, outcome_window_days=outcome_window_days, as_of=ref
                ):
                    n_legacy += 1
        return {
            "effective_n": total_w,
            "agreed_weight": agreed_w,
            "decayed_rate": (agreed_w / total_w) if total_w > 0 else None,
            "n_d1": counts[CLASS_TOO_AGGRESSIVE],
            "n_d2": counts[CLASS_TOO_CAUTIOUS],
            "n_d3": counts[CLASS_JEV_WAS_RIGHT],
            "n_pending": counts[CLASS_PENDING],
            "n_legacy": n_legacy,
        }

    decided = [
        r
        for r in records
        if r.get("actual_outcome") in (ACTUAL_MERGED, ACTUAL_CLOSED_UNMERGED)
    ]
    stats = _stats(decided, now)
    trend_cutoff = now - timedelta(days=7)
    trend_rs = [
        r for r in decided if (_parse_decided_at(r) or trend_cutoff) <= trend_cutoff
    ]
    trend = _stats(trend_rs, trend_cutoff)
    return {
        "n_decided": len(decided),
        "n_open": len(records) - len(decided),
        **stats,
        "trend_7d": trend["decayed_rate"],
    }


def shadow_exit_met_v2(
    records: list[dict[str, Any]],
    bad_merge_calls: int = 0,
    *,
    half_life_days: int = AGREEMENT_HALF_LIFE_DAYS,
    outcome_window_days: int = AGREEMENT_OUTCOME_WINDOW_DAYS,
    min_effective_n: float = SHADOW_MIN_EFFECTIVE_N,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Phase 0 exit criteria with the v2 agreement metric. Pure.

    v1's ``shadow_exit_met`` is untouched. Passes iff ALL hold:
      - min_prs: raw decided count >= 30 (experience requirement, unchanged)
      - min_agreement_v2: decayed rate >= 0.95
      - zero_bad_merges: bad_merge_calls == 0 (no decay, no classification)
      - min_effective_n: decayed sample >= 10 (blocks tiny-sample passes)
    """
    stats = decayed_agreement_rate(
        records,
        half_life_days=half_life_days,
        outcome_window_days=outcome_window_days,
        as_of=as_of,
    )
    n = stats["n_decided"]
    rate = stats["decayed_rate"]
    eff = stats["effective_n"]
    checks = {
        "min_prs": n >= SHADOW_MIN_PRS,
        "min_agreement_v2": rate is not None and rate >= SHADOW_MIN_AGREEMENT,
        "zero_bad_merges": bad_merge_calls == 0,
        "min_effective_n": eff >= min_effective_n,
    }
    return {
        **stats,
        "metric": AGREEMENT_METRIC_V2,
        "bad_merge_calls": bad_merge_calls,
        "checks": checks,
        "exit_met": all(checks.values()),
    }


def attach_outcomes(
    records: list[dict[str, Any]],
    outcomes_by_pr: dict[Any, str],
    key: str = "pr_number",
) -> list[dict[str, Any]]:
    """Join post-merge outcomes onto shadow records by PR number. Pure.

    ``outcomes_by_pr`` maps PR number -> "clean" | "rolled_back". Returns
    new record dicts; records with no known outcome are returned unchanged
    (the classifier treats them as pending/legacy). The nightly job owns
    the actual join against the deploy-outcome / rollback feeds; this is
    the shared pure step so the logic is never reimplemented per caller.
    """
    joined = []
    for r in records:
        pr = r.get(key)
        outcome = outcomes_by_pr.get(pr) if pr is not None else None
        if outcome in (OUTCOME_CLEAN, OUTCOME_ROLLBACK):
            r = {**r, "outcome": outcome}
        joined.append(r)
    return joined


def apply_corrections(
    records: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    key: str = "pr_number",
) -> list[dict[str, Any]]:
    """Fold append-only correction rows onto copies of shadow records. Pure.

    Each correction: {pr_number, verdict, corrected_by, corrected_at, ...}.
    Only the "jev-was-right" verdict is understood; unknown verdicts are
    ignored so future verdict types never break the reader. The input
    records are never mutated.
    """
    by_pr: dict[Any, dict[str, Any]] = {}
    for c in corrections:
        if isinstance(c, dict) and c.get("verdict") == CORRECTION_JEV_WAS_RIGHT:
            by_pr[c.get(key)] = c
    out = []
    for r in records:
        c = by_pr.get(r.get(key))
        if c is not None:
            r = {
                **r,
                "corrected_by": c.get("verdict"),
                "corrected_at": c.get("corrected_at"),
            }
        out.append(r)
    return out
