#!/usr/bin/env python3
"""lane_visibility_probe.py — read-only dispatcher lane-visibility check (WI-6).

What it guards: box-side label drift. The dispatcher's lane scans match
issues by Linear label (``agent:<name>``); when a lane label is renamed or
deleted in the Linear UI, that lane's scans silently match nothing and no
liveness check can see it — the process is healthy, the *logic* is blind.
(The 30s watchdog covers process-down; this probe covers logic-blind from
the box side.)

What it does, per dispatcher lane:
  1. builds the lane label with the same construction the dispatcher's
     label scans use (``f"agent:{agent_name}"`` — see
     ``prismatic/dispatcher.py`` lane scan loop and label snapshot);
  2. asserts the built label is canonical single-colon form — a spec
     assertion on the probe's own construction contract;
  3. asserts the label matches a known label on the box (the team's Linear
     label list, fetched with the same read-only ``GetTeamLabels`` query the
     dispatcher uses for lookups) — this is the drift check.

What it does NOT guard: a dispatcher *code* regression (e.g. the July
incident, where the dispatcher's code built ``agent::<name>`` double-colon
labels while the box had ``agent:<name>``). This probe mirrors the
construction with its own hardcoded f-string, so fed that scenario it would
still exit 0 while lanes go blind. Closing that gap needs a shared label
constructor or a source-bound check against the dispatcher's own code —
follow-up work, not this probe.

On any mismatch it prints an alert, best-effort emits an audit signal, and
exits non-zero.

READ-ONLY CONTRACT: this script never creates issues, never dispatches, never
creates labels (unlike ``dispatcher.get_label_id``, which auto-creates missing
labels — the probe deliberately uses only the lookup query), and never writes
to Linear. The only network call is a GraphQL label-list read.

Exit codes:
  0 — every lane's label is canonical and present on the box (lane visible)
  1 — at least one lane is blind (non-canonical label or label missing on box)
  2 — the check could not run (no Linear credentials, dispatcher not
      importable, API error, zero lanes resolved)

Usage:
  python3 scripts/lane_visibility_probe.py [--json] [--no-signal]
      [--team-id TEAM_ID]

Scheduled hourly via the engine-health cron seed set (WI-5).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Canonical dispatcher lane label: exactly one colon separating the "agent"
# namespace from the lane name, no whitespace, non-empty lane name.
CANONICAL_LANE_LABEL_RE = re.compile(r"^agent:[^:\s]+$")

# The same team-label lookup query the dispatcher uses in get_label_id()
# (prismatic/dispatcher.py) — the *read* half only. get_label_id() goes on to
# run an issueLabelCreate mutation when the label is missing; the probe must
# never do that, so the mutation is deliberately absent here.
TEAM_LABELS_QUERY = """
query GetTeamLabels($teamId: String!) {
    team(id: $teamId) {
        labels {
            nodes {
                id
                name
            }
        }
    }
}
"""

EXIT_VISIBLE = 0
EXIT_BLIND = 1
EXIT_CANNOT_CHECK = 2


class ProbeSkipped(Exception):
    """The check cannot run in this environment (missing credentials, etc.)."""


class ProbeError(Exception):
    """The check failed for an infrastructure reason (import/API error)."""


@dataclass
class LaneCheck:
    agent: str
    label: str
    canonical: bool
    known_on_box: bool
    ok: bool
    reason: str = ""


# ── Label construction (mirrors the dispatcher) ────────────────────────────


def dispatcher_lane_label(agent_name: str) -> str:
    """Build the lane label exactly as the dispatcher's label scans do.

    Mirror of ``prismatic/dispatcher.py``:
      * lane scan loop: ``label = f"agent:{agent_name}"``
      * label snapshot: ``agent_labels = [f"agent:{name}" for name in AGENT_CONFIG]``

    Canonical Linear labels are single-colon
    (docs/proof-loop-demo-wedge.md). Kept as an explicit function (rather than
    an inline f-string at each call site) so the construction is injectable in
    tests and reviewable in one place.

    Hardcoded mirror, not bound to the dispatcher source: a code regression
    in the dispatcher would not be reflected here (see the module docstring's
    residual-gap note).
    """
    return f"agent:{agent_name}"


def is_canonical_lane_label(label: str) -> bool:
    """True when *label* is canonical single-colon dispatcher-lane form.

    Rejects the double-colon form (``agent::fred``) as well as empty lane
    names, whitespace, and extra colons. This is a spec assertion on the
    label string itself — it fires on whatever construction feeds the probe.
    """
    return bool(CANONICAL_LANE_LABEL_RE.match(label or ""))


# ── Box side: known labels (read-only) ─────────────────────────────────────


def fetch_team_label_names(gql_fn, team_id: str) -> list[str]:
    """Return the team's Linear label names via a read-only label-list query.

    Uses the same ``GetTeamLabels`` query the dispatcher uses for lookups.
    Never creates labels — there is no mutation in this path.

    Raises ProbeError on an error-shaped payload (a GraphQL ``errors``
    envelope passed through by a non-raising wrapper): treating that as an
    empty label list would be a false "blind lanes" alarm (exit 1) for a
    check that never ran — exit 2 is the honest code.
    """
    data = gql_fn(
        TEAM_LABELS_QUERY,
        {"teamId": team_id},
        source="lane_visibility_probe.team_labels",
    )
    if isinstance(data, dict) and data.get("errors"):
        raise ProbeError(
            f"Linear label lookup returned GraphQL errors: {data['errors']}"
        )
    nodes = data.get("team", {}).get("labels", {}).get("nodes", [])
    return [node.get("name", "") for node in nodes if node.get("name")]


# ── Core probe (pure; fully testable without Linear or the dispatcher) ─────


def check_lane(agent_name: str, label_for, known_labels: list[str]) -> LaneCheck:
    label = label_for(agent_name)
    canonical = is_canonical_lane_label(label)
    known_on_box = label in known_labels
    if not canonical:
        return LaneCheck(
            agent=agent_name,
            label=label,
            canonical=False,
            known_on_box=known_on_box,
            ok=False,
            reason=f"label {label!r} is not canonical single-colon form",
        )
    if not known_on_box:
        return LaneCheck(
            agent=agent_name,
            label=label,
            canonical=True,
            known_on_box=False,
            ok=False,
            reason=f"label {label!r} not found among team labels on the box",
        )
    return LaneCheck(
        agent=agent_name,
        label=label,
        canonical=True,
        known_on_box=True,
        ok=True,
    )


def probe_lanes(
    lanes: list[str], label_for, known_labels: list[str]
) -> list[LaneCheck]:
    return [check_lane(name, label_for, known_labels) for name in lanes]


def summarize(checks: list[LaneCheck]) -> dict:
    blind = [c.agent for c in checks if not c.ok]
    return {
        "ok": not blind,
        "lanes_checked": len(checks),
        "blind_lanes": blind,
        "lanes": [asdict(c) for c in checks],
    }


def evaluate(
    lanes: list[str],
    label_for,
    known_labels: list[str],
    *,
    emit_signal_fn=None,
) -> tuple[dict, int]:
    """Run the probe and return (report, exit_code). Emits one audit signal
    when (and only when) at least one lane is blind."""
    if not lanes:
        report = {
            "ok": False,
            "lanes_checked": 0,
            "blind_lanes": [],
            "lanes": [],
            "error": "zero lanes resolved from dispatcher AGENT_CONFIG",
        }
        return report, EXIT_CANNOT_CHECK
    checks = probe_lanes(lanes, label_for, known_labels)
    report = summarize(checks)
    if not report["ok"]:
        alert = (
            "[lane-visibility-probe] BLIND LANE(S): "
            + ", ".join(
                f"{c.agent} (label {c.label!r}: {c.reason})"
                for c in checks
                if not c.ok
            )
        )
        report["alert"] = alert
        if emit_signal_fn is not None:
            emit_signal_fn(alert)
        return report, EXIT_BLIND
    return report, EXIT_VISIBLE


# ── Audit signal (best-effort; never fails the probe) ──────────────────────


def _find_emit_tool() -> str | None:
    found = shutil.which("emit")
    if found:
        return found
    candidate = Path.home() / "workspace" / "skills" / "prismatic-audit" / "bin" / "emit"
    if candidate.is_file():
        return str(candidate)
    return None


def emit_signal(message: str, *, severity: str = "error") -> bool:
    """Best-effort audit signal via the prismatic-audit emit tool.

    Returns True when the signal was handed off, False otherwise. Never
    raises — signal delivery must not fail the probe itself.
    """
    try:
        tool = _find_emit_tool()
        if not tool:
            return False
        subprocess.run(
            [
                tool,
                "--type", "error",
                "--message", message[:500],
                "--tool", "lane_visibility_probe",
                "--status", "failed",
                "--severity", severity,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return True
    except Exception:
        return False


# ── Live wiring (imports the dispatcher; read-only) ────────────────────────


def resolve_live_context():
    """Import the dispatcher and resolve lanes + team id.

    Raises ProbeSkipped when Linear credentials are absent, ProbeError when
    the dispatcher cannot be imported or resolves zero lanes.
    """
    api_key = os.environ.get("LINEAR_API_KEY")
    team_id = os.environ.get("PRISMATIC_TEAM_ID")
    missing = [
        name
        for name, value in (
            ("LINEAR_API_KEY", api_key),
            ("PRISMATIC_TEAM_ID", team_id),
        )
        if not value
    ]
    if missing:
        raise ProbeSkipped(
            f"missing {', '.join(missing)} — cannot query Linear; skipping probe"
        )
    try:
        from prismatic import dispatcher
    except Exception as exc:
        raise ProbeError(f"cannot import prismatic.dispatcher: {exc}") from exc
    lanes = list(getattr(dispatcher, "AGENT_CONFIG", {}) or {})
    if not lanes:
        raise ProbeError("dispatcher AGENT_CONFIG resolved to zero lanes")
    return dispatcher, lanes, team_id


def run_live(*, emit: bool) -> tuple[dict, int]:
    dispatcher, lanes, team_id = resolve_live_context()
    try:
        known_labels = fetch_team_label_names(dispatcher.gql, team_id)
    except Exception as exc:
        raise ProbeError(f"Linear label lookup failed: {exc}") from exc
    emit_fn = emit_signal if emit else None
    return evaluate(lanes, dispatcher_lane_label, known_labels, emit_signal_fn=emit_fn)


# ── CLI ────────────────────────────────────────────────────────────────────


def print_human_report(report: dict) -> None:
    for lane in report.get("lanes", []):
        status = "VISIBLE" if lane["ok"] else "BLIND"
        extra = "" if lane["ok"] else f" — {lane['reason']}"
        print(f"[lane-visibility-probe] {status:7} {lane['agent']}: {lane['label']}{extra}")
    if report.get("ok"):
        print(
            f"[lane-visibility-probe] OK: {report['lanes_checked']} lane(s) visible"
        )
    else:
        print(f"[lane-visibility-probe] ALERT: {report.get('alert', 'blind lanes detected')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dispatcher lane-visibility probe (WI-6)."
    )
    parser.add_argument("--json", action="store_true",
                        help="print the full report as JSON")
    parser.add_argument("--no-signal", action="store_true",
                        help="do not emit an audit signal on mismatch")
    parser.add_argument("--team-id", default="",
                        help="override PRISMATIC_TEAM_ID")
    args = parser.parse_args(argv)

    if args.team_id:
        os.environ["PRISMATIC_TEAM_ID"] = args.team_id

    try:
        report, exit_code = run_live(emit=not args.no_signal)
    except ProbeSkipped as exc:
        print(f"[lane-visibility-probe] SKIP: {exc}", file=sys.stderr)
        return EXIT_CANNOT_CHECK
    except ProbeError as exc:
        print(f"[lane-visibility-probe] ERROR: {exc}", file=sys.stderr)
        return EXIT_CANNOT_CHECK

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human_report(report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
