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
output) to check that every dispatcher lane's label scan can actually see
work — the check that would have caught the July blind-lane incident, when the
dispatcher built `agent::<name>` (double-colon) labels while Linear only had
`agent:<name>` (single-colon), so every lane silently matched nothing for ~2
months. The 30s watchdog covers process-down; this probe covers logic-blind.

For each lane in the dispatcher's `AGENT_CONFIG` it builds the lane label with
the same construction the dispatcher's label scans use, asserts the label is
canonical single-colon form, and asserts it matches a known label on the box
(read-only `GetTeamLabels` query — never creates labels, issues, or
dispatches). Exit codes: 0 = all lanes visible, 1 = blind lane(s) found (alert
printed, audit signal emitted best-effort), 2 = could not run (e.g. no Linear
credentials). Intended to run hourly via the engine-health cron seed set.
