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
