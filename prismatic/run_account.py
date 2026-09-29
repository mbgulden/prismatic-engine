"""Readable run accounts — "what did my agents do?"

Turns one agent run's record (:class:`prismatic.run_records.AgentRunRecord`,
as a plain dict) into a human-readable account: a header (run id, agent,
times, status), a timeline of the verification commands the run executed
(command → exit code → output excerpt), the artifacts/files it touched, and
a verification footer.

Schema note (verified against main 2026-09-28): the trust ledger
(:mod:`prismatic.review_factory.trust`) records merge/autonomy events
(merge_completed, tier_promoted, brake_pulled, ...), NOT per-agent tool
calls. The per-run activity record is the agent run record plus its embedded
execution evidence (:mod:`prismatic.execution_evidence`). This module renders
from the run record — it does not conflate the two ledgers.

Design rules:
- Every rendered timeline entry cites its source as ``<run_id>#cmd-<index>``.
  The account is a *view* over the record, never a retelling: no dropped
  commands, no invented lines.
- Missing data renders as "not recorded" — never fabricated.
- Pure + deterministic: same record in → byte-identical account out.
- Read-only: this module never touches the run store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

NOT_RECORDED = "not recorded"


def _as_str(value: Any) -> str:
    if value is None:
        return NOT_RECORDED
    text = str(value)
    return text if text.strip() else NOT_RECORDED


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _duration_text(started_at: Any, completed_at: Any) -> str | None:
    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    if seconds < 0:
        return None
    if seconds < 60:
        return "%.1fs" % seconds
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return "%dm %ds" % (minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return "%dh %dm" % (hours, minutes)


def _command_outcome(exit_code: Any) -> str:
    if exit_code is None:
        return NOT_RECORDED
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return str(exit_code)
    return "ok" if code == 0 else "failed (exit %d)" % code


def _shorten(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def render_run_account(record: Mapping[str, Any]) -> dict[str, Any]:
    """Render the structured account for one agent run record (as a dict).

    ``record`` matches ``AgentRunRecord`` serialized form: run_id, agent_name,
    issue_id, status, started_at, completed_at, error_message, evidence (dict
    with commands/artifacts/files_changed/...), verification_status,
    verification_scope, failure_category, cleanup_status, done_gate_result,
    done_gate_errors.
    """
    record = dict(record)
    run_id = _as_str(record.get("run_id"))
    evidence = record.get("evidence") or {}
    if not isinstance(evidence, Mapping):
        evidence = {}
    commands: Sequence[Mapping[str, Any]] = evidence.get("commands") or []

    timeline = []
    for i, cmd in enumerate(commands):
        cmd = dict(cmd)
        timeline.append(
            {
                "source": "%s#cmd-%d" % (run_id, i),
                "command": _as_str(cmd.get("command")),
                "outcome": _command_outcome(cmd.get("exit_code")),
                "exit_code": cmd.get("exit_code"),
                "output_excerpt": _shorten(_as_str(cmd.get("output_excerpt"))),
                "scope": _as_str(cmd.get("scope")),
            }
        )

    return {
        "run_id": run_id,
        "agent_name": _as_str(record.get("agent_name")),
        "issue_id": _as_str(record.get("issue_id")),
        "status": _as_str(record.get("status")),
        "started_at": _as_str(record.get("started_at")),
        "completed_at": _as_str(record.get("completed_at")),
        "duration": _duration_text(record.get("started_at"), record.get("completed_at"))
        or NOT_RECORDED,
        "evidence_summary": _as_str(evidence.get("summary")),
        "command_count": len(timeline),
        "timeline": timeline,
        "artifacts": [str(a) for a in (evidence.get("artifacts") or [])],
        "files_changed": [str(f) for f in (evidence.get("files_changed") or [])],
        "external_side_effects": [
            str(s) for s in (evidence.get("external_side_effects") or [])
        ],
        "verification_status": _as_str(record.get("verification_status")),
        "verification_scope": _as_str(record.get("verification_scope")),
        "failure_category": _as_str(record.get("failure_category")),
        "blocker": _as_str(evidence.get("blocker")),
        "cleanup_status": _as_str(record.get("cleanup_status")),
        "done_gate_result": _as_str(record.get("done_gate_result")),
        "done_gate_errors": [str(e) for e in (record.get("done_gate_errors") or [])],
        "error_message": _as_str(record.get("error_message")),
        "has_evidence": bool(evidence),
    }


def to_text(account: Mapping[str, Any]) -> str:
    """Render the coffee-readable narrative form of a structured account."""
    lines: list[str] = []
    lines.append("Run account: %s" % account.get("run_id", NOT_RECORDED))
    lines.append(
        "Agent: %s   Task: %s   Status: %s"
        % (
            account.get("agent_name", NOT_RECORDED),
            account.get("issue_id", NOT_RECORDED),
            account.get("status", NOT_RECORDED),
        )
    )
    lines.append(
        "Started: %s   Finished: %s   Duration: %s"
        % (
            account.get("started_at", NOT_RECORDED),
            account.get("completed_at", NOT_RECORDED),
            account.get("duration", NOT_RECORDED),
        )
    )
    summary = account.get("evidence_summary", NOT_RECORDED)
    if summary != NOT_RECORDED:
        lines.append("Summary: %s" % _shorten(summary, 200))
    lines.append("")

    timeline = account.get("timeline") or []
    if not account.get("has_evidence"):
        lines.append("No evidence recorded for this run.")
    elif not timeline:
        lines.append("Evidence recorded, but no verification commands listed.")
    else:
        lines.append("Verification commands (%d):" % len(timeline))
        for entry in timeline:
            lines.append(
                "  $ %s → %s  [%s]"
                % (
                    entry.get("command", NOT_RECORDED),
                    entry.get("outcome", NOT_RECORDED),
                    entry.get("source", "?"),
                )
            )
            excerpt = entry.get("output_excerpt", NOT_RECORDED)
            if excerpt != NOT_RECORDED:
                lines.append("    ↳ %s" % excerpt)

    for label, key in (
        ("Artifacts", "artifacts"),
        ("Files changed", "files_changed"),
        ("External side effects", "external_side_effects"),
    ):
        items = account.get(key) or []
        if items:
            lines.append("")
            lines.append("%s:" % label)
            for item in items:
                lines.append("  - %s" % item)

    lines.append("")
    lines.append(
        "Verification: %s (scope: %s)   Failure: %s   Done gate: %s"
        % (
            account.get("verification_status", NOT_RECORDED),
            account.get("verification_scope", NOT_RECORDED),
            account.get("failure_category", NOT_RECORDED),
            account.get("done_gate_result", NOT_RECORDED),
        )
    )
    blocker = account.get("blocker", NOT_RECORDED)
    if blocker != NOT_RECORDED:
        lines.append("Blocker: %s" % blocker)
    errors = account.get("done_gate_errors") or []
    for err in errors:
        lines.append("Done-gate error: %s" % err)
    error_message = account.get("error_message", NOT_RECORDED)
    if error_message != NOT_RECORDED:
        lines.append("Error: %s" % _shorten(error_message, 200))
    return "\n".join(lines) + "\n"
