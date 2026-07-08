# scripts/ops

Operational probes and recovery utilities for Prismatic-owned infrastructure.

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
