# scripts/ops

Operational probes, reports, and recovery utilities that Ned can run without mutating product/content lanes.

## Honeybadger readiness (GRO-149)

`honeybadger_infra_readiness.py` is a non-mutating day-1 pre-flight probe for the Honeybadger infrastructure epic. It checks:

- 40G/RDMA design evidence (`network/latency_report.md`, with a stable-checkout fallback noted in output)
- GrowthWeb Cloudflare Tunnel credential *presence* (names and sources only; values are never emitted)
- `cloudflared` binary availability
- local LLM/vLLM reference docs used by the Prismatic testbed
- whether the private Honeybadger repo is mounted at `/home/ubuntu/work/honeybadger`
- Tailscale command availability/status

Run it from a Prismatic checkout:

```bash
python3 scripts/ops/honeybadger_infra_readiness.py --json
```

A `warn` status is still useful evidence: it means the testbed prerequisites are partially present but the private Honeybadger repo or a host-level binary may still need human/orchestrator follow-up before the 14-week buildout is split into executable subtasks.

## Label debt and stale queue drift

`label_debt_queue_drift.py` measures Linear queue hygiene:

- quantifies label debt by reason (`multiple_agent_labels`, `dispatch_ready_without_agent`, stale dispatchable work, terminal-state agent labels, and blocked dispatch labels),
- reports open issue counts by state and active agent label,
- appends JSONL snapshots with `--append-history` so repeated runs show drift from the previous snapshot.

Example:

```bash
python3 scripts/ops/label_debt_queue_drift.py --append-history
python3 scripts/ops/label_debt_queue_drift.py --json --history-path /tmp/prismatic_state/label_debt_queue_drift.jsonl
```

The default history path is `$PRISMATIC_STATE_DIR/label_debt_queue_drift.jsonl`, falling back to `/tmp/prismatic_state/label_debt_queue_drift.jsonl`.

## GRO-3617 dispatcher process observer

`prismatic.dispatcher` registers every `subprocess.Popen` returned by AGY/Jules/Codex launchers with a daemon process observer. The observer updates `telemetry_agent_runs` through `TelemetryCollector.update_agent_run()` when the child exits, closing the prior `status='dispatched'` / `end_time=NULL` gap that blocked the GRO-2978 acceptance query.

Regression coverage lives in `prismatic/tests/test_dispatch_observer_gro3617.py` and exercises both closure updates and the GRO-2979 dispatch-cap helpers.
