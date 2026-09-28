# Prismatic Engine scripts

## Pipeline metrics dashboard

Run `python3 scripts/pipeline_dashboard.py` for the human-readable pipeline health view, `--summary` for a one-line status, or `--json` for machine-readable output.

The human dashboard panes are intentionally operator-facing. Each pane must explain:

- **Shows** — the data being summarized.
- **Why** — why that signal matters for pipeline health or governance.
- **Next** — the default action an operator should take when the pane is weak, empty, or red/yellow.

The dashboard includes a **Recovery / Watchdog State** pane before the normal task metrics. It surfaces the operator-critical recovery signals in one place:

- consumer/watchdog heartbeat freshness from `PRISMATIC_RECOVERY_STATE`, `PRISMATIC_SUPERVISOR_HEARTBEAT`, or `~/.prismatic/supervisor/{recovery_state,heartbeat}.json`;
- bounded supervisor pool live capacity from `prismatic.supervisor.recovery.get_pool().stats()`;
- DLQ backlog and recent records from `PRISMATIC_SUPERVISOR_DLQ` or `~/.prismatic/supervisor/dlq.jsonl`.

The pane uses the same operator contract as the rest of the dashboard. `--json` includes the same data under `recovery`; `--summary` appends a `recovery:<status>` badge.

## Lane visibility probe

Run `python3 scripts/lane_visibility_probe.py` (or `--json` for machine-readable
output) to check that every dispatcher lane's label still exists on the box —
the guard against box-side label drift (a lane label renamed or deleted in the
Linear UI makes that lane's scans silently match nothing; the 30s watchdog
covers process-down, this probe covers logic-blind from the box side).

For each lane in the dispatcher's `AGENT_CONFIG` it builds the lane label with
the same construction the dispatcher's label scans use, asserts the label is
canonical single-colon form (a spec assertion on the probe's own construction
contract), and asserts it matches a known label on the box (read-only
`GetTeamLabels` query — never creates labels, issues, or dispatches). It does
not guard against a dispatcher *code* regression: the construction is a
hardcoded mirror, so a code-side repeat of the July `agent::<name>` incident
would still exit 0 here — that gap needs a shared constructor or a
source-bound check (follow-up). Exit codes: 0 = all lanes visible, 1 = blind
lane(s) found (alert printed, audit signal emitted best-effort), 2 = could not
run (e.g. no Linear credentials). Intended to run hourly via the engine-health
cron seed set.
