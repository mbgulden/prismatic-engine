# prismatic-engine

**Event-driven agent factory for the Prismatic Engine.**

A FastAPI-based gateway that consumes Linear/GitHub webhooks, persists them to a SQLite bus, tags them via a curator lane, and dispatches them to bounded AGY supervisor pools with per-lane budget enforcement.

## Quick Start

```bash
# Check service health
systemctl status prismatic-gateway prismatic-consumer prismatic-curator

# View live curator state
curl -s http://localhost:9000/curator/health | python3 -m json.tool

# Read today's digest
cat /home/ubuntu/.prismatic/curator/digests/2026-06-30.md

# Run all tests
PYTHONPATH=/home/ubuntu/.prismatic/venv_stable/lib/python3.12/site-packages:. \
  /home/ubuntu/.prismatic/venv_stable/bin/python3 -m pytest \
  prismatic/curator/tests/ prismatic/supervisor/tests/
# Expected: 39 passed
```

## Architecture

```
Linear/GitHub webhooks
       │
       ▼
┌─────────────────────┐
│  prismatic-gateway  │  ← HMAC verify, /metrics, /events, /curator/health
│  (port 9000)        │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  SQLite event bus   │  ← WAL, 14-day/10k retention, durable
│  ~/.prismatic/bus/  │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ prismatic-consumer  │  ← rowid + atomic + 60s dedup
│ (dispatch_consumer) │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ SupervisorPool      │  ← bounded, MAX_CONCURRENT=8, reaps zombies
│ (recovery.py)       │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ AGY supervisors     │  ← per-lane dispatch (fred/codex/kai/jules/ned)
│ (hermes profile)    │
└─────────────────────┘

(In parallel:)
┌─────────────────────┐
│ prismatic-curator   │  ← tags events, dispatches delegates via pool
│ (curator/lane.py)   │  ← 8am daily digest, budget enforcement
└─────────────────────┘
```

See `docs/phase-d-post-publish-chain.md` for the detailed Phase D architecture.

## Modules

### `prismatic/gateway/`
- `event_bus.py` — In-process pub/sub + SQLite WAL persistence
- `server.py` — FastAPI gateway with 5 endpoints: `/health`, `/metrics`, `/events/recent`, `/events/bus-stats`, `/curator/health`

### `prismatic/curator/`
- **[SPEC.md](prismatic/curator/SPEC.md)** — Doc #4: the canonical spec for the curator lane (14KB, 320 lines)
- `lane.py` — Curator implementation (18KB, 575 lines)
- `dispatcher.py` — Lane budget + Sonnet/Opus routing (8KB, 236 lines)
- `tests/test_lane.py` — 15 unit tests
- `tests/test_dispatcher.py` — 13 unit tests

### `prismatic/supervisor/`
- `recovery.py` — Bounded supervisor pool with reaping + DLQ (8.5KB, 271 lines)
- `tests/test_recovery.py` — 11 unit tests

### `scripts/`
- `linear_relabel.py` — Bulk-label Linear issues for engine consumption (14KB)
- `linear_relabel.py --dry-run` — preview changes
- `linear_relabel.py --apply --yes` — apply idempotently

## Systemd Units

| Unit | Purpose |
|---|---|
| `prismatic-gateway.service` | HTTP gateway on port 9000 |
| `prismatic-consumer.service` | bus consumer, dispatch via bounded pool |
| `prismatic-curator.service` | tags events, dispatches delegates, runs continuously |
| `prismatic-curator-digest.service` | one-shot, emits daily digest |
| `prismatic-curator-digest.timer` | fires at 8am America/Denver daily |

## Configuration

Environment variables (set in `/etc/systemd/system/prismatic-*.service`):

| Var | Default | Purpose |
|---|---|---|
| `PRISMATIC_HOME` | `/home/ubuntu` | base path |
| `PRISMATIC_BUS_DB` | `~/.prismatic/bus/event_log.sqlite` | bus location |
| `PRISMATIC_CURATOR_DB` | `~/.prismatic/curator/state.sqlite` | curator state |
| `PRISMATIC_DIGEST_DIR` | `~/.prismatic/curator/digests` | digest output dir |
| `PRISMATIC_DIGEST_HOUR` | `8` | daily digest hour |
| `PRISMATIC_METRICS_TOKEN` | (empty) | bearer token for /metrics auth |
| `PRISMATIC_ALLOWED_IPS` | `127.0.0.1,::1` | IP allowlist for observability endpoints |
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` | (required) | HMAC secret for Linear webhooks |
| `PRISMATIC_LINEAR_WEBHOOK_SECRET_SECONDARY` | (optional) | 2nd slot for rotation |
| `PRISMATIC_GITHUB_WEBHOOK_SECRET` | (required) | HMAC secret for GitHub webhooks |
| `PRISMATIC_SUPERVISOR_MAX` | `8` | max concurrent supervisors |
| `PRISMATIC_SUPERVISOR_REAP_INTERVAL` | `30` | seconds between reap sweeps |
| `PRISMATIC_BUDGET_FRED` | `5.00` | USD/day cap for fred lane (opus) |
| `PRISMATIC_BUDGET_CODEX` | `10.00` | USD/day cap for codex lane |
| `PRISMATIC_BUDGET_KAI` | `3.00` | USD/day cap for kai lane |
| `PRISMATIC_BUDGET_JULES` | `3.00` | USD/day cap for jules lane |
| `PRISMATIC_BUDGET_NED` | `5.00` | USD/day cap for ned lane |
| `PRISMATIC_BUDGET_TRIAGE` | `1.00` | USD/day cap for triage lane |

## Lane Policy

Per `PRISMATIC_ENGINE.yaml`:
- **Fred** owns the entire repo (orchestrator) — `lanes.owner: ["*"]`
- **Ned** owns `scripts/` + `plugins/` — code execution & task agent
- **Kai** owns `content/` + `active-oahu/` — content writer
- **AGY** owns `assets/` + `designs/` + `research/` — designer & researcher
- **Jules** has no direct edits, PR-only — PR agent & code reviewer

The lane policy is enforced by `scripts/pre-push-hook.py`. Use `feature/*`, `content/*`, `design/*`, `fix/*`, or `ned/*` branch prefixes.

## Tests

```bash
# Run all tests
PYTHONPATH=/home/ubuntu/.prismatic/venv_stable/lib/python3.12/site-packages:. \
  /home/ubuntu/.prismatic/venv_stable/bin/python3 -m pytest prismatic/

# Run specific suite
PYTHONPATH=/home/ubuntu/.prismatic/venv_stable/lib/python3.12/site-packages:. \
  /home/ubuntu/.prismatic/venv_stable/bin/python3 -m pytest \
  prismatic/curator/tests/ prismatic/supervisor/tests/
```

**Current status:** 39/39 passing in ~1.2s.

## Documentation

- [Curator Lane Spec (Doc #4)](prismatic/curator/SPEC.md) — The canonical spec
- [Phase D Post-Publish Chain](docs/phase-d-post-publish-chain.md) — Architecture diagram
- `/home/ubuntu/work/okf/operations/INDEX.md` — Top-level OKF docs index
- `/home/ubuntu/work/okf/operations/API-REFERENCE.md` — HTTP API reference
- `/home/ubuntu/work/okf/operations/2026-06-30-session-documentation.md` — What we built today

## Linear

- Epic 1: GRO-3022 (In Progress, 6/8 stories done) — [Curator Lane + Service Reliability](https://linear.app/growthwebdev/issue/GRO-3022)
- Epics 2-7: GRO-3023..3028 (Backlog) — 3-month roadmap

## License

Internal to GrowthWebDev. See `LICENSE` (TBD).
