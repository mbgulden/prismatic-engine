# Prismatic Engine scripts

## Pipeline metrics dashboard

Run `python3 scripts/pipeline_dashboard.py` for the human-readable pipeline health view, `--summary` for a one-line status, or `--json` for machine-readable output.

The dashboard now includes a **Recovery / Watchdog State** pane before the normal task metrics. It surfaces the operator-critical recovery signals in one place:

- consumer/watchdog heartbeat freshness from `PRISMATIC_RECOVERY_STATE`, `PRISMATIC_SUPERVISOR_HEARTBEAT`, or `~/.prismatic/supervisor/{recovery_state,heartbeat}.json`;
- bounded supervisor pool live capacity from `prismatic.supervisor.recovery.get_pool().stats()`;
- DLQ backlog and recent records from `PRISMATIC_SUPERVISOR_DLQ` or `~/.prismatic/supervisor/dlq.jsonl`.

The pane uses the same operator contract as the rest of the dashboard: **Shows** what is being summarized, **Why** it matters, and **Next** gives the default recovery action. `--json` includes the same data under `recovery`; `--summary` appends a `recovery:<status>` badge.
