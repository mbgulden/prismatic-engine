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
