# Phase D: Event-Driven Workflow — Post-Publish Chain Documentation

**Status:** Shipped (Phase D.1-D.6)  
**Date:** 2026-06-30  
**Authors:** Fred (orchestrator), Opus (architecture review)

## What is the post-publish chain?

When an event hits the gateway bus (via webhook or IPC), it flows through
this chain:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Linear/GitHub webhook                                                  │
│        │                                                                │
│        ▼                                                                │
│  HTTP handler (/api/gateway/linear, /api/gateway/github)                │
│        │                                                                │
│        ├─ HMAC verify (2-slot rotation: PRIMARY + SECONDARY)            │
│        │   ├─ fail → 401, increment auth_failed counter                 │
│        │   └─ pass → increment published counter                        │
│        │                                                                │
│        ▼                                                                │
│  bus.publish(event_type, source, payload)                               │
│        │                                                                │
│        ├─ Schema validation (D.3)                                       │
│        │   ├─ fail → drop, log error, increment total_failures          │
│        │   └─ pass → continue                                           │
│        │                                                                │
│        ├─ In-memory ring buffer (max 200)                               │
│        │                                                                │
│        ├─ SQLite bus persist (D.2a)                                     │
│        │   ├─ WAL mode for concurrent read/write                        │
│        │   ├─ 14-day retention prune                                    │
│        │   └─ 10k-event overflow trim                                  │
│        │                                                                │
│        └─ Fan-out to in-process handlers (asyncio.gather)               │
│           └─ ipc_bridge, ws_broadcaster, etc.                           │
│                                                                         │
│  ─────── durable side starts here ───────                               │
│                                                                         │
│  dispatch_consumer_v3.py (systemd: prismatic-consumer.service)          │
│        │                                                                │
│        ├─ Polls SQLite bus every 3s for `rowid > last_rowid AND         │
│        │   processed = 0`                                              │
│        │                                                                │
│        ├─ Cold-start guard: rowids with ts older than 5 min skipped    │
│        │                                                                │
│        ├─ For each new event:                                           │
│        │   ├─ Filter: topic='update', type='Issue'                      │
│        │   ├─ 60s dedup window per issue_id                             │
│        │   ├─ Linear API: fetch_issue()                                 │
│        │   ├─ should_dispatch: not Done/Cancelled, has dispatch:* label │
│        │   └─ Popen supervisor (agy_sandbox_event_supervisor.py)        │
│        │       ├─ args: --issue ISSUE_ID --from-linear                  │
│        │       ├─ max-concurrent=2, watchdog=30s                       │
│        │       └─ lifecycle: spawn → execute → exit (no zombies)        │
│        │                                                                │
│        ├─ Atomic: UPDATE events SET processed=1 WHERE rowid=?           │
│        │   (next consumer iteration skips this row)                     │
│        │                                                                │
│        └─ Vacuum: every 5 min, delete processed rows older than 1 day   │
│                                                                         │
│  supervisor:                                                            │
│        ├─ Reads Linear issue via --issue flag                           │
│        ├─ Picks lane (sonnet / opus / gemini-fast) per issue priority   │
│        ├─ Spawns AGY CLI with appropriate model                         │
│        ├─ Watches AGY output, transitions issue to Done on success      │
│        ├─ Adds labels: agent:done, agent:peer-review                    │
│        └─ Emits "agent_launched" event back to bus (loop closure)       │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

### Gateway (`prismatic.gateway.server`)
- **Owner of:** HTTP webhook ingestion, HMAC verification, bus publishing
- **Lifecycle:** systemd `prismatic-gateway.service`
- **Counters:** `linear_received`, `linear_auth_failed`, `linear_published`, same for github
- **Observability:** `GET /metrics`, `GET /events/recent`, `GET /events/bus-stats`

### Event Bus (`prismatic.gateway.event_bus`)
- **Owner of:** in-process pub/sub (asyncio) + durable SQLite log
- **Schema:** `events(rowid PK, dedup_key UNIQUE, topic, payload_json, ts, processed)`
- **Retention:** 14 days OR 10k events (whichever hits first)
- **Concurrency:** WAL mode, `_sqlite_lock` threading lock

### Dispatch Consumer (`prismatic/gateway/event_handlers/dispatch_consumer_v3.py`)
- **Owner of:** SQLite bus draining, supervisor spawning
- **Lifecycle:** systemd `prismatic-consumer.service` (enabled, Restart=always)
- **Poll interval:** 3s
- **Dedup window:** 60s per issue_id
- **Cold-start backoff:** skip events older than 5 min
- **State file:** `/home/ubuntu/.prismatic/bus/dispatch_consumer.rowid`

### Supervisor (`scripts/agy_sandbox_event_supervisor.py`)
- **Owner of:** AGY worker orchestration for a single issue (or batch)
- **Spawn args:** `--issue ISSUE_ID --from-linear --max-concurrent 2 --watchdog --watchdog-interval 30`
- **Lane routing:** auto/sonnet/opus/gemini-flash
- **Labels added on completion:** `agent:done`, `agent:peer-review`

## Failure Modes & Recovery

| Failure | Recovery |
|---|---|
| Gateway dies | systemd Restart=always → cold restart in <5s. Webhooks during outage lost (Linear retries 3x). |
| Consumer dies | systemd Restart=always → cold restart. State file preserved. SQLite events remain unprocessed. |
| Supervisor crashes | watchdog interval=30s detects and respawns. Issue stays In Progress until done. |
| SQLite corruption | WAL mode protects against most partial writes. Restore from `event_log.sqlite.bak` if available. |
| HMAC secret rotation | Both PRIMARY and SECONDARY slots are accepted for HMAC verify. Update SECONDARY first, then PRIMARY. |
| Consumer flooded by Linear retries | 60s issue_id dedup window prevents duplicate supervisor spawns. |
| Bus overflow | 10k-event cap + 14-day retention at publish time. Old rows trimmed automatically. |

## Known Caveats

1. **Single gateway SPOF** — webhook ingestion fails if gateway is down. Mitigation: Linear retries 3x; for full HA, run two gateway instances behind a load balancer with shared SQLite (WAL allows multi-writer).

2. **Single SQLite bus SPOF** — single-file durable bus. WAL mode handles concurrent reads/writes, but a disk failure could lose events. For full HA, migrate to PostgreSQL or similar.

3. **Masked secrets in env files** — `/home/ubuntu/.prismatic/env.d/linear_oauth.env` contains masked placeholder secrets (`lin_wh...D8sc`). If Linear rotates and the env isn't updated, real webhooks will 401. Verified 2-slot algorithm works; verified with manually-signed requests; not verified with real Linear webhooks (since the masked secret may not be the real one).

4. **Worker leak risk** — consumer spawned 13 supervisor processes during the initial bus replay (legacy state). Killed all. Consumer now has 60s dedup window to prevent recurrence.

## Verification Endpoints

```bash
# Health
curl http://localhost:9000/health

# Metrics (JSON)
curl http://localhost:9000/metrics

# Recent events (last 50 by default)
curl 'http://localhost:9000/events/recent?limit=10'

# SQLite bus stats
curl http://localhost:9000/events/bus-stats

# Service status
systemctl status prismatic-gateway prismatic-consumer
```

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-30 | Initial Phase D ship | Fred |
| 2026-06-30 | BUG-1 fixed (PYTHONPATH) | Fred |
| 2026-06-30 | BUG-2 fixed (consumer path) | Fred |
| 2026-06-30 | BUG-8 fixed (Linear secret name) | Fred |
| 2026-06-30 | BUG-3,4,5,6,7 fixed (consumer v3: rowid+atomic+dedup) | Fred |
| 2026-06-30 | SPOF-2 fixed (consumer → systemd unit) | Fred |
| 2026-06-30 | BUG-13 fixed (/events/recent endpoint) | Fred |
| 2026-06-30 | D.6 documentation written | Fred |