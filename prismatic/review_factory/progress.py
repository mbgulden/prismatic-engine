"""Jev progress meter ("level up") — read-only eligibility rendering.

Beginners installing Jev see a silent learning period and quit before
trust is earned. This module renders how close the installation is to
"leveling up" the rollout ladder (0 -> 1 -> 2 -> 3) and what is blocking
it, in plain words.

READ-ONLY CONTRACT (do not weaken):
- This module never advances a phase, never arms anything, never writes
  state. It only *renders eligibility*.
- Gate logic is NEVER reimplemented here. Pass/fail for every gate comes
  from ``PhaseAdvancement.evaluate_exit_criteria`` via
  ``prismatic.review_factory.phase_advancement``; this module only
  formats the returned ``ExitResult.checks`` plus display values read
  from the evidence with the same public helpers.
- Exception (display-only): the agreement section's v2 *readiness*
  preview reuses the canonical ``shadow_exit_met_v2`` gate function
  itself — not a reimplementation — and only to render numbers. It never
  changes which metric the phase gates use; that is the
  ``agreement_metric`` policy flag (default "v1"), owned by
  phase_advancement.
- Missing evidence files render as zeros; nothing here raises on a fresh
  install.

Levels (user-facing names):
- Lv 0 "Observer"  — Jev watches and learns; advisory value only.
- Lv 1 "Co-pilot"  — auto-merge, safest tier only.
- Lv 2 "Autopilot" — bounded live tiers.
- Lv 3 "Commander" — full ladder; tier 2/3 stay human-only by policy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.review_factory.phase_advancement import (
    DECISION_ALLOWED,
    MAX_PHASE,
    OUTCOME_ROLLBACK,
    PHASE1_CONSECUTIVE_MERGES,
    PHASE2_MAX_ROLLBACK_RATE,
    PHASE2_WINDOW_DAYS,
    ExitResult,
    PhaseAdvancementError,
    discover_active_policy,
    evaluate_exit_criteria,
    load_evidence,
)
from prismatic.review_factory.shadow_agreement import (
    ACTUAL_CLOSED_UNMERGED,
    ACTUAL_MERGED,
    AGREEMENT_OUTCOME_WINDOW_DAYS,
    CLASS_PENDING,
    SHADOW_MIN_AGREEMENT,
    SHADOW_MIN_EFFECTIVE_N,
    SHADOW_MIN_PRS,
    agreement_rate,
    calls_agree,
    classify_disagreement,
    decayed_agreement_rate,
    shadow_exit_met_v2,
)

LEVEL_NAMES = {
    0: "Observer",
    1: "Co-pilot",
    2: "Autopilot",
    3: "Commander",
}

NEXT_TRANSITION = {0: (0, 1), 1: (1, 2), 2: (2, 3)}

FRESH_INSTALL_LINE = (
    "Jev is watching and learning. Advisory insights are live now "
    "(failure diagnosis, risk flags, novelty alerts); automation unlocks "
    "as evidence builds."
)

READY_TEMPLATE = (
    "READY — level up available (Lv {frm} → Lv {to}). Advancement still "
    "needs your explicit approval — the meter shows eligibility, never decides."
)

MACHINERY_DISABLED_LINE = (
    "Evidence complete, but the advancement machinery is disabled by policy "
    "(advancements_enabled: false). No phase can advance until it is enabled."
)


# ─────────────────────────────────────────────────────────────────────
# Evidence pointers
# ─────────────────────────────────────────────────────────────────────


def default_audit_dir() -> Path:
    """Where the installation's audit evidence lives."""
    return Path(os.path.expanduser("~/.prismatic/audit"))


def default_evidence_pointers(audit_dir: str | Path | None = None) -> dict[str, Any]:
    """Map evidence keys to default file pointers.

    Keys match ``load_evidence`` in phase_advancement.py. The heartbeat
    tick (scripts/phase_advancement_tick.py) adopts this same helper so
    the pointer table lives in exactly one place.

    The watchdog pointer names the *deployed* policy: the VM timer
    installation symlinks the policy the feed actually runs with to
    ``<audit>/watchdog-policy.yaml``. Until that exists the meter
    truthfully reports the watchdog as not armed.
    """
    base = Path(audit_dir) if audit_dir else default_audit_dir()
    return {
        # joined [{system_call, actual_outcome}] records (nightly join job)
        "shadow_records": str(base / "shadow-records.jsonl"),
        # emitted shadow audit signals (shadow observer)
        "shadow_signals": str(base / "shadow-decisions.jsonl"),
        # int, or a file holding one int
        "bad_merge_calls": str(base / "bad-merge-calls.txt"),
        # deployed watchdog policy (see docstring)
        "watchdog_policy": str(base / "watchdog-policy.yaml"),
        # merge-authority decision log rows
        "auto_merge_decisions": str(base / "auto-merge-decisions.jsonl"),
        # JSON list of novelty-page ids in the window
        "novelty_pages": str(base / "novelty-pages.json"),
        # dir of weekly learn reports (*.json)
        "weekly_reports_dir": str(base / "weekly-reports"),
    }


def _resolve_pointers(pointers: dict[str, Any]) -> dict[str, Any]:
    """Keep only pointers that resolve; missing files become empty defaults.

    ``load_evidence`` raises on an unreadable path pointer (fail-closed —
    evidence must be readable to count), so the meter filters first and
    substitutes inline empties. Missing evidence renders as zeros, never
    as an exception.
    """
    resolved: dict[str, Any] = {}
    for key, pointer in pointers.items():
        if key == "bad_merge_calls":
            path = Path(str(pointer))
            resolved[key] = str(path) if path.exists() else 0
            continue
        if key == "novelty_pages":
            path = Path(str(pointer))
            resolved[key] = str(path) if path.exists() else []
            continue
        path = Path(str(pointer))
        if path.exists():
            resolved[key] = str(path)
        # else: dropped — the exit checks default missing keys to empties.
    return resolved


def load_status_evidence(
    audit_dir: str | Path | None = None,
    pointers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load parsed evidence for the meter; missing/unreadable → {}.

    Never raises: a fresh install simply has no evidence yet.
    """
    try:
        table = (
            pointers if pointers is not None else default_evidence_pointers(audit_dir)
        )
        return load_evidence(_resolve_pointers(table))
    except (PhaseAdvancementError, OSError, ValueError):
        return {}


def current_phase_info(
    spec_dir: str | Path | None = None,
) -> tuple[int | None, bool, str | None]:
    """Return (phase, advancements_enabled, error).

    Never raises: with no readable phase policy the meter shows the
    fresh-install state instead of crashing.
    """
    try:
        policy = (
            discover_active_policy()
            if spec_dir is None
            else discover_active_policy(spec_dir)
        )
    except PhaseAdvancementError as exc:
        return None, False, str(exc)
    return policy.phase, policy.advancements_enabled, None


# ─────────────────────────────────────────────────────────────────────
# Gate rows (display only — pass/fail always comes from ExitResult)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateRow:
    """One rendered gate row: label, current/target, pass/fail.

    ``met`` is copied from ``ExitResult.checks`` — never computed here.
    ``fraction`` drives the progress bar (None → checkmark row).
    ``detail`` is the teaching line shown when the gate fails.
    """

    key: str
    label: str
    current: str
    target: str
    met: bool
    fraction: float | None = None
    detail: str = ""


def _is_fresh_evidence(evidence: dict[str, Any]) -> bool:
    """True when nothing has been observed yet.

    Inline defaults (bad_merge_calls=0, novelty_pages=[]) and a merely
    *present* watchdog policy do not count as evidence — freshness is
    about observed PR/merge activity.
    """
    for key in (
        "shadow_records",
        "shadow_signals",
        "auto_merge_decisions",
        "weekly_reports",
        "novelty_pages",
    ):
        if evidence.get(key):
            return False
    return not evidence.get("bad_merge_calls", 0)


def _parse_ts(value: Any) -> datetime | None:
    """Display-only timestamp parse (gate logic lives in phase_advancement)."""
    """Display-only timestamp parse (gate logic lives in phase_advancement)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _pr_ref(record: dict[str, Any]) -> str:
    for key in ("pr_number", "pr", "number", "id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return "?"


def _agreement_teaching(records: list[dict[str, Any]]) -> str:
    """Name the specific PRs where Jev's shadow call differed.

    This is the correction channel: the human sees their own overrides
    reflected back, which is the antidote to rubber-stamping.
    """
    disagreements: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            agree = calls_agree(
                str(record.get("system_call", "")),
                str(record.get("actual_outcome", "")),
            )
        except (ValueError, TypeError):
            continue
        if agree is False:
            disagreements.append(record)
    if not disagreements:
        return (
            "Agreement is below target but no decidable disagreements were "
            "found — more evaluated PRs are needed."
        )
    parts: list[str] = []
    for record in disagreements[:5]:
        pr = _pr_ref(record)
        call = record.get("system_call")
        actual = record.get("actual_outcome")
        if call == "skip" and actual == "merged":
            parts.append(f"Jev flagged #{pr} as skip-worthy — you merged it")
        elif call == "merge" and actual == "closed_unmerged":
            parts.append(f"Jev said merge on #{pr} — you closed it unmerged")
        else:
            parts.append(f"#{pr}: Jev said {call}, outcome was {actual}")
    suffix = f" (+{len(disagreements) - 5} more)" if len(disagreements) > 5 else ""
    return "Disagreements: " + "; ".join(parts) + suffix + "."


def _observation_windows_open(
    records: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    window_days: int = AGREEMENT_OUTCOME_WINDOW_DAYS,
) -> int:
    """Count decided skip→merged disagreements still inside the outcome window.

    Display-only definition for the v2 readiness line. A window is "open"
    when the record is a pending skip/merged disagreement with a known
    ``decided_at`` inside the outcome window. Records with no ``decided_at``
    are legacy — no outcome will ever arrive for them — so they never hold
    a window open.
    """
    now = now or datetime.now(timezone.utc)
    open_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("actual_outcome") not in (
            ACTUAL_MERGED,
            ACTUAL_CLOSED_UNMERGED,
        ):
            continue
        if classify_disagreement(record) != CLASS_PENDING:
            continue
        decided = _parse_ts(record.get("decided_at"))
        if decided is None:
            continue
        age_days = (now - decided).total_seconds() / 86400
        if 0 <= age_days <= window_days:
            open_count += 1
    return open_count


def agreement_section(
    evidence: dict[str, Any],
    *,
    now: datetime | None = None,
    active_metric: str = "v1",
) -> list[str]:
    """Render the v1/v2 side-by-side agreement block. Pure, read-only.

    v1 numbers come from ``agreement_rate``; v2 numbers and three of the
    four readiness checks come from the canonical ``shadow_exit_met_v2``
    (reused for display — this does not activate v2; the phase gates keep
    using whichever metric the ``agreement_metric`` policy flag selects).
    Replayed backfill records are never counted here: only the live
    ``shadow_records`` evidence the caller passes in is rendered.
    """
    records = [
        r for r in (evidence.get("shadow_records", []) or []) if isinstance(r, dict)
    ]
    bad = evidence.get("bad_merge_calls", 0) or 0

    v1 = agreement_rate(records)
    v1_rate = v1["rate"]
    v1_line = (
        f"  v1: {f'{v1_rate:.1%}' if v1_rate is not None else 'n/a'}"
        f" over {v1['n_decided']} decided PRs"
        + (" (active metric)" if active_metric == "v1" else "")
    )

    v2stats = decayed_agreement_rate(records, as_of=now)
    v2gate = shadow_exit_met_v2(records, bad_merge_calls=bad, as_of=now)
    v2_rate = v2stats["decayed_rate"]
    v2_line = (
        f"  v2: {f'{v2_rate:.1%}' if v2_rate is not None else 'n/a'} decayed"
        f" · {v2stats['n_decided']} decided PRs"
        f" · effective sample {v2stats['effective_n']:.1f}"
        + (" (active metric)" if active_metric == "v2" else "")
    )

    checks = v2gate["checks"]
    windows_open = _observation_windows_open(records, now=now)
    readiness_items = [
        (f"PRs≥{SHADOW_MIN_PRS}", bool(checks["min_prs"])),
        (
            f"effective sample≥{SHADOW_MIN_EFFECTIVE_N:g}",
            bool(checks["min_effective_n"]),
        ),
        ("zero bad merges", bool(checks["zero_bad_merges"])),
        ("observation windows complete", windows_open == 0),
    ]
    readiness = "  v2 readiness: " + " · ".join(
        f"{label} {_mark(met)}" for label, met in readiness_items
    )

    other = "v2" if active_metric == "v1" else "v1"
    lines = [
        f"Agreement — {active_metric} active · {other} shadow:",
        v1_line,
        v2_line,
        readiness,
    ]
    if active_metric != "v2":
        lines.append("  ↳ v2 shown for readiness only; v1 remains the active metric.")
    return lines


def _phase0_rows(evidence: dict[str, Any], result: ExitResult) -> list[GateRow]:
    records = evidence.get("shadow_records", []) or []
    signals = evidence.get("shadow_signals", []) or []
    bad = evidence.get("bad_merge_calls", 0) or 0
    checks = result.checks

    stats = agreement_rate(records)
    n_decided = stats["n_decided"]
    rate = stats["rate"]

    watchdog_met = bool(checks.get("watchdog_armed_monitor_only"))
    signals_met = bool(checks.get("all_shadow_signals_complete"))

    return [
        GateRow(
            key="min_prs_resolved",
            label="Shadow PRs evaluated",
            current=f"{n_decided}/{SHADOW_MIN_PRS}",
            target=str(SHADOW_MIN_PRS),
            met=bool(checks.get("min_prs_resolved")),
            fraction=min(n_decided / SHADOW_MIN_PRS, 1.0),
            detail=(
                ""
                if checks.get("min_prs_resolved")
                else f"Jev is watching and learning — {n_decided}/{SHADOW_MIN_PRS} "
                "PRs evaluated so far."
            ),
        ),
        GateRow(
            key="min_agreement",
            label="Agreement with your decisions",
            current=f"{rate:.1%}" if rate is not None else "n/a",
            target=f"{SHADOW_MIN_AGREEMENT:.0%}",
            met=bool(checks.get("min_agreement")),
            fraction=min(rate / SHADOW_MIN_AGREEMENT, 1.0) if rate is not None else 0.0,
            detail="" if checks.get("min_agreement") else _agreement_teaching(records),
        ),
        GateRow(
            key="zero_bad_merge_calls",
            label="Bad merge calls",
            current=str(bad),
            target="0",
            met=bool(checks.get("zero_bad_merge_calls")),
            fraction=1.0 if bad == 0 else 0.0,
            detail=(
                ""
                if checks.get("zero_bad_merge_calls")
                else f"{bad} PR(s) Jev called 'merge' on later needed repair, "
                "rollback, or human revert."
            ),
        ),
        GateRow(
            key="watchdog_armed_monitor_only",
            label="Watchdog",
            current="armed (monitor-only)" if watchdog_met else "not armed",
            target="armed",
            met=watchdog_met,
            fraction=None,
            detail=(
                ""
                if watchdog_met
                else "Arm the watchdog in monitor-only mode to unlock this gate."
            ),
        ),
        GateRow(
            key="all_shadow_signals_complete",
            label="Shadow signals complete",
            current=f"{len(signals)} emitted",
            target="all complete",
            met=signals_met,
            fraction=None,
            detail=(
                ""
                if signals_met
                else "Some shadow signals are incomplete — the observer only "
                "counts complete audit signals."
            ),
        ),
    ]


def _allowed_newest_first(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = [d for d in decisions if d.get("decision") == DECISION_ALLOWED]
    allowed.sort(key=lambda d: str(d.get("timestamp", "")))
    return list(reversed(allowed))


def _phase1_rows(evidence: dict[str, Any], result: ExitResult) -> list[GateRow]:
    decisions = evidence.get("auto_merge_decisions", []) or []
    streak = _allowed_newest_first(decisions)[:PHASE1_CONSECUTIVE_MERGES]
    clean = sum(1 for d in streak if d.get("outcome") == "clean")
    rolled_back = [d for d in streak if d.get("outcome") == OUTCOME_ROLLBACK]
    checks = result.checks

    streak_met = bool(checks.get("twenty_merges_exist")) and bool(
        checks.get("streak_all_clean")
    )
    rb_met = bool(checks.get("zero_rollbacks_in_streak"))

    if rb_met:
        rb_detail = ""
    elif rolled_back:
        named = []
        for d in rolled_back[:3]:
            ts = d.get("timestamp", "?")
            named.append(f"#{_pr_ref(d)} at {ts}")
        rb_detail = (
            "The streak broke here: "
            + ", ".join(f"merge {n} was rolled back" for n in named)
            + ". A rollback always breaks the streak; a refusal does not."
        )
    else:
        rb_detail = "A rollback broke the streak."

    return [
        GateRow(
            key="consecutive_clean_merges",
            label="Consecutive clean auto-merges",
            current=f"{clean}/{PHASE1_CONSECUTIVE_MERGES}",
            target=str(PHASE1_CONSECUTIVE_MERGES),
            met=streak_met,
            fraction=min(clean / PHASE1_CONSECUTIVE_MERGES, 1.0),
            detail=(
                ""
                if streak_met
                else f"{clean}/{PHASE1_CONSECUTIVE_MERGES} clean merges in the "
                "current streak."
            ),
        ),
        GateRow(
            key="zero_rollbacks_in_streak",
            label="Rollbacks in streak",
            current=str(len(rolled_back)),
            target="0",
            met=rb_met,
            fraction=1.0 if not rolled_back else 0.0,
            detail=rb_detail,
        ),
    ]


def _phase2_rows(
    evidence: dict[str, Any],
    result: ExitResult,
    now: datetime | None = None,
) -> list[GateRow]:
    now = now or datetime.now(timezone.utc)
    window_start = now.timestamp() - PHASE2_WINDOW_DAYS * 86400

    in_window: list[dict[str, Any]] = []
    for d in evidence.get("auto_merge_decisions", []) or []:
        ts = _parse_ts(d.get("timestamp"))
        if (
            d.get("decision") == DECISION_ALLOWED
            and ts is not None
            and ts.timestamp() >= window_start
        ):
            in_window.append(d)
    merges = len(in_window)
    rollbacks = sum(1 for d in in_window if d.get("outcome") == OUTCOME_ROLLBACK)
    rate = (rollbacks / merges) if merges else None
    days_active = len(
        {
            _parse_ts(d.get("timestamp")).date().isoformat()  # type: ignore[union-attr]
            for d in in_window
            if _parse_ts(d.get("timestamp")) is not None
        }
    )

    reports = evidence.get("weekly_reports", []) or []
    justified: set[str] = set()
    for report in reports:
        entries = (report or {}).get("novelty_pages_justified", []) or []
        for entry in entries:
            if (
                isinstance(entry, dict)
                and entry.get("id")
                and str(entry.get("justification", "")).strip()
            ):
                justified.add(str(entry["id"]))
    pages = [str(p) for p in evidence.get("novelty_pages", []) or []]

    checks = result.checks
    window_met = bool(checks.get("window_has_merges"))
    rate_met = bool(checks.get("rollback_rate_below_2pct"))
    pages_met = bool(checks.get("every_novelty_page_justified"))

    if rate_met:
        rate_detail = ""
    elif rate is None:
        rate_detail = "No merges in the window, so no rollback rate yet."
    else:
        rate_detail = (
            f"{merges} merges, {rollbacks} rollbacks in the trailing "
            f"{PHASE2_WINDOW_DAYS} days = {rate:.2%} "
            f"(target < {PHASE2_MAX_ROLLBACK_RATE:.0%})."
        )

    return [
        GateRow(
            key="window_has_merges",
            label="Days in window",
            current=f"{days_active}/{PHASE2_WINDOW_DAYS}",
            target=str(PHASE2_WINDOW_DAYS),
            met=window_met,
            fraction=min(days_active / PHASE2_WINDOW_DAYS, 1.0),
            detail=(
                ""
                if window_met
                else "No auto-merges in the trailing "
                f"{PHASE2_WINDOW_DAYS}-day window yet (the gate needs at "
                "least one)."
            ),
        ),
        GateRow(
            key="rollback_rate_below_2pct",
            label="Rollback rate",
            current=f"{rate:.2%}" if rate is not None else "n/a",
            target=f"< {PHASE2_MAX_ROLLBACK_RATE:.0%}",
            met=rate_met,
            fraction=(
                max(0.0, 1.0 - rate / PHASE2_MAX_ROLLBACK_RATE)
                if rate is not None
                else 0.0
            ),
            detail=rate_detail,
        ),
        GateRow(
            key="every_novelty_page_justified",
            label="Novelty pages justified",
            current=f"{len(justified & set(pages))}/{len(pages)}",
            target="all",
            met=pages_met,
            fraction=(len(justified & set(pages)) / len(pages)) if pages else 1.0,
            detail=(
                ""
                if pages_met
                else f"{len(pages)} novelty page(s) in the window, "
                f"{len(justified & set(pages))} justified in weekly reports."
            ),
        ),
    ]


_ROW_BUILDERS = {
    0: _phase0_rows,
    1: _phase1_rows,
    2: _phase2_rows,
}


# ─────────────────────────────────────────────────────────────────────
# Status assembly (pure)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StatusReport:
    """Everything the renderers need; assembled without side effects."""

    phase: int | None
    advancements_enabled: bool
    rows: tuple[GateRow, ...] = ()
    ready: bool = False
    fresh_install: bool = False
    policy_error: str | None = None
    detail: str = ""
    agreement_lines: tuple[str, ...] = ()


def build_report(
    phase: int | None,
    advancements_enabled: bool,
    evidence: dict[str, Any],
    policy_error: str | None = None,
    now: datetime | None = None,
) -> StatusReport:
    """Assemble the meter state from policy + evidence + ExitResult.

    Pure: the ExitResult comes from ``evaluate_exit_criteria`` (the
    single source of truth); this only organizes it for rendering.
    """
    fresh = phase in (None, 0) and _is_fresh_evidence(evidence)
    if phase is None or phase >= MAX_PHASE:
        return StatusReport(
            phase=phase,
            advancements_enabled=advancements_enabled,
            rows=(),
            ready=False,
            fresh_install=fresh,
            policy_error=policy_error,
        )
    to_phase = phase + 1
    result = evaluate_exit_criteria(phase, to_phase, evidence)
    builder = _ROW_BUILDERS.get(phase)
    if builder is None:
        rows: tuple[GateRow, ...] = ()
    elif phase == 2:
        rows = tuple(builder(evidence, result, now))
    else:
        rows = tuple(builder(evidence, result))
    agreement_lines: tuple[str, ...] = ()
    if rows:
        # Display-only v2 preview. The phase gates above still use the
        # policy-selected metric; this changes no activation state.
        agreement_lines = tuple(
            agreement_section(
                evidence,
                now=now,
                active_metric=str(evidence.get("agreement_metric", "v1")),
            )
        )
    return StatusReport(
        phase=phase,
        advancements_enabled=advancements_enabled,
        rows=rows,
        ready=bool(result.met),
        fresh_install=fresh,
        policy_error=policy_error,
        detail=result.detail,
        agreement_lines=agreement_lines,
    )


def build_status(
    audit_dir: str | Path | None = None,
    spec_dir: str | Path | None = None,
    now: datetime | None = None,
) -> StatusReport:
    """Impure orchestrator: discover policy, load evidence, build the report.

    Read-only: discovers the active phase policy and reads evidence files.
    Never writes, never advances, never arms.
    """
    phase, enabled, error = current_phase_info(spec_dir)
    evidence = load_status_evidence(audit_dir)
    return build_report(phase, enabled, evidence, policy_error=error, now=now)


# ─────────────────────────────────────────────────────────────────────
# Rendering (pure)
# ─────────────────────────────────────────────────────────────────────


def _bar(fraction: float | None, width: int = 20) -> str:
    if fraction is None:
        return ""
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _mark(met: bool) -> str:
    return "✓" if met else "✗"


def render_status(report: StatusReport) -> str:
    """Render the human-readable progress meter. Pure."""
    phase = report.phase if report.phase is not None else 0
    level_name = LEVEL_NAMES.get(phase, "Unknown")
    lines: list[str] = []

    if report.phase is not None and report.phase >= MAX_PHASE:
        lines.append(f'Jev — Lv {phase} "{level_name}" — max level reached.')
        lines.append("Tier 2/3 stay human-only by policy.")
        return "\n".join(lines) + "\n"

    to_phase = phase + 1
    to_name = LEVEL_NAMES.get(to_phase, "Unknown")
    lines.append(
        f'Jev progress — Lv {phase} "{level_name}" → Lv {to_phase} "{to_name}"'
    )
    lines.append("")

    if report.fresh_install and not report.rows:
        lines.append(FRESH_INSTALL_LINE)
        return "\n".join(lines) + "\n"

    for row in report.rows:
        bar = _bar(row.fraction)
        lines.append(f"  {row.label:<32} {row.current:>12}  {bar} {_mark(row.met)}")
        if row.detail:
            lines.append(f"    ↳ {row.detail}")

    if report.agreement_lines:
        lines.append("")
        lines.extend(report.agreement_lines)

    closing: list[str] = []
    if report.ready and report.advancements_enabled:
        closing.append(READY_TEMPLATE.format(frm=phase, to=to_phase))
    elif report.ready:
        closing.append(MACHINERY_DISABLED_LINE)
    elif report.fresh_install:
        closing.append(FRESH_INSTALL_LINE)

    if report.policy_error:
        closing.append(f"  (phase policy unreadable: {report.policy_error})")

    if closing:
        if report.rows:
            lines.append("")
        lines.extend(closing)

    return "\n".join(lines) + "\n"


def status_json(report: StatusReport) -> dict[str, Any]:
    """Machine-readable status. Pure. Key set is the dashboard contract."""
    phase = report.phase
    return {
        "level": phase,
        "level_name": LEVEL_NAMES.get(phase) if phase is not None else None,
        "next_transition": [phase, phase + 1]
        if phase is not None and phase < MAX_PHASE
        else None,
        "gates": [
            {
                "key": row.key,
                "label": row.label,
                "current": row.current,
                "target": row.target,
                "met": row.met,
                "detail": row.detail,
            }
            for row in report.rows
        ],
        "ready": report.ready,
        "machinery_disabled": report.ready and not report.advancements_enabled,
        "fresh_install": report.fresh_install,
    }
