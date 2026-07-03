# Curator Lane Spec

**Status:** Draft v0.1 — 2026-06-30
**Owner:** Fred (orchestrator), Opus review
**Epic:** [EPIC 1] Curator Lane + Service Reliability (GRO-3022)
**Story:** 1.3 — Curator Lane Spec

## 1. Purpose

The **curator lane** is a thin supervisor that consumes every event emitted by the Prismatic event bus, tags it per a fixed taxonomy, and emits a daily digest at 8am local time. It is the **missing link** between "the engine runs" and "the engine runs autonomously for the right reasons."

The curator is what enables the North Star principle *"runs without you"*. Without it, the engine produces 50+ Linear events/day but Michael has to triage every one of them. With it, the engine triages itself and pages Michael only when something actually needs him.

## 2. Scope

### In scope

- Consuming every event from the SQLite event bus
- Tagging each event with one of 4 taxonomy buckets
- Aggregating per-lane stats (count, last seen, p95 latency, error rate)
- Emitting a daily markdown digest to `~/.prismatic/curator/digests/YYYY-MM-DD.md`
- Pinging Michael only when `escalate > 0` for the day
- Per-tenant + per-lane budget caps (warns 80%, hard-stops 100%)
- Reversibility tagging on every lane action (Story 5.2 from Epic 5)

### Out of scope (for v0.1)

- Calling Sonnet/Opus directly to make decisions (that's the *agent* lane; curator is the *router*)
- Image/video/text generation (Creative horizon, Epic 4)
- Tenant billing (Business horizon, Epic 3)
- Auto-creating Linear issues (curator tags; the agent lane consumes the tags and decides)

## 3. Architecture

```
                                    ┌──────────────────────────────────┐
                                    │                                  │
                                    │      Curator Lane (this spec)   │
                                    │                                  │
                                    │  ┌─────────────┐ ┌────────────┐  │
                                    │  │ tagger.py   │ │ digest.py  │  │
Linear/GitHub ─► bus ─► dispatcher ─►│  │             │ │            │  │
                                    │  │ - subscribe │ │ - aggregate│  │
                                    │  │ - tag       │ │ - render   │  │
                                    │  │ - persist   │ │ - publish  │  │
                                    │  └─────────────┘ └────────────┘  │
                                    │           │             │         │
                                    │           ▼             ▼         │
                                    │     state.sqlite    digests/    │
                                    └──────────────────────────────────┘
                                              │             │
                                              ▼             ▼
                                       8am cron fires   Telegram
                                       digest emit     page if escalate
```

The curator reads from the same SQLite bus the dispatch_consumer reads from, but **after** the dispatch_consumer has marked events `processed=1`. This means:
- The curator never competes with the dispatcher for an event
- Curator processing lag doesn't block dispatch
- If the curator dies, dispatch continues normally; curator catches up on restart

## 4. Event taxonomy

Every event from the bus is tagged with **exactly one** of four values:

| Tag | Meaning | Action |
|---|---|---|
| `auto-pick` | Trivial, no judgment needed. Routine status updates, label changes, comment adds. | Curator logs it. No downstream action. |
| `delegate` | Real work, but no human input needed. Code change requests, doc scans, telemetry investigations. | Curator emits a `delegate.requested` event with the target lane. Sonnet/Opus via `delegate_task` picks it up. |
| `escalate` | Needs Michael. Revenue blockers, security incidents, decisions only a human can make, anything that already paged through other lanes and failed. | Curator logs it. At 8am digest emit, if `escalate > 0`, curator pings Michael via Telegram. |
| `drop` | Spam, duplicate, or out-of-scope. The same event arriving twice (Linear retry), test events, retired webhook payloads. | Curator logs the drop reason. No downstream action. |

### Tagging rules (initial)

| Event type | Source | Tag | Why |
|---|---|---|---|
| `Issue.update` (label added) | Linear | depends on label | `dispatch:ready` → delegate; otherwise auto-pick |
| `Issue.update` (state change) | Linear | auto-pick | Informational |
| `Issue.create` | Linear | delegate | New work, needs a lane assignment |
| `Comment.create` | Linear | drop | Conversation noise |
| `webhook.ping` | GitHub/Linear | drop | Test payload |
| `webhook.delivery.failed` | GitHub/Linear | escalate | Real problem |
| `agent.heartbeat` | internal | drop | Self-monitoring, not actionable |
| `agent.completed` | internal | auto-pick | Informational |
| `agent.failed` | internal | escalate | Real problem |
| `bus.budget.exceeded` | governance | escalate | Hard cap hit |
| `digest.scheduled` | internal | drop | Self-triggered |

These rules live in `prismatic/curator/rules.py` as a tagged union:
```python
@dataclass
class TagRule:
    predicate: Callable[[SwarmEvent], bool]
    tag: Literal["auto-pick", "delegate", "escalate", "drop"]
    lane_hint: str | None = None  # for `delegate` events

TAG_RULES: list[TagRule] = [
    TagRule(lambda e: e.source == "linear" and e.payload.get("action") == "create", "delegate", lane_hint="codex"),
    TagRule(lambda e: e.source == "linear" and e.payload.get("action") == "update" and "dispatch:ready" in labels(e), "delegate"),
    # ... etc
]
```

## 5. State storage

`~/.prismatic/curator/state.sqlite` (SQLite, WAL mode, append-only):

```sql
CREATE TABLE tagged_events (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    event_rowid INTEGER NOT NULL,  -- references bus events.rowid
    tag TEXT NOT NULL CHECK(tag IN ('auto-pick','delegate','escalate','drop')),
    lane_hint TEXT,
    tagged_at REAL NOT NULL,
    reason TEXT  -- why this tag (rule name + payload excerpt for debugging)
);

CREATE TABLE lane_stats (
    lane TEXT PRIMARY KEY,
    count_total INTEGER DEFAULT 0,
    count_delegate INTEGER DEFAULT 0,
    count_escalate INTEGER DEFAULT 0,
    count_drop INTEGER DEFAULT 0,
    last_seen_ts REAL,
    p95_tag_latency_ms REAL
);

CREATE TABLE digest_runs (
    date TEXT PRIMARY KEY,  -- YYYY-MM-DD local
    ran_at REAL NOT NULL,
    auto_pick_count INTEGER,
    delegate_count INTEGER,
    escalate_count INTEGER,
    drop_count INTEGER,
    paged_michael INTEGER DEFAULT 0,
    digest_path TEXT NOT NULL
);

CREATE INDEX idx_tagged_event_rowid ON tagged_events(event_rowid);
CREATE INDEX idx_tagged_tag ON tagged_events(tag);
```

The `event_rowid` foreign-key relationship means we can always reconstruct what was tagged by joining with the bus events table. The state is durable; restarting the curator does not lose tag history.

## 6. Digest format

The 8am digest is a Markdown file at `~/.prismatic/curator/digests/YYYY-MM-DD.md`:

```markdown
# Curator Digest — 2026-06-30

Generated: 08:00:00 local
Bus events processed: 127
Tagged: 127 (4 buckets)

## Counts
| Tag | Count | Notes |
|---|---|---|
| auto-pick | 89 | Routine status updates |
| delegate | 21 | 14 to codex, 4 to kai, 2 to ned, 1 to jules |
| escalate | 3 | ⚠️ See below |
| drop | 14 | 8 retries, 4 test events, 2 conversation |

## Escalations (needs your attention)
1. **GRO-3023** — HD Engine vertical slice blocked on per-tenant budget cap. 18 hours since last progress. **Action:** review budget config or deprioritize.
2. **agent.failed** (lane=codex, 4×) — repeated failures on `tests/test_phase_d_e2e_smoke.py`. **Action:** check stderr at `/home/ubuntu/.prismatic/logs/agent-failures.log`.
3. **bus.budget.exceeded** (lane=creative, daily=$5) — hard cap hit. **Action:** either raise the cap or extend the deadline.

## Lane health
| Lane | Processed | Errors | p95 tag latency | Last seen |
|---|---|---|---|---|
| codex | 14 | 2 | 87ms | 2026-06-29 23:14Z |
| kai | 4 | 0 | 12ms | 2026-06-29 18:02Z |
| ned | 2 | 0 | 24ms | 2026-06-29 14:55Z |
| jules | 1 | 0 | 41ms | 2026-06-29 11:20Z |

## What ran overnight
- Codex: 14 dispatch requests, 12 completed, 2 escalated
- Kai: 4 link-fix sweeps, all clean
- Ned: 2 cron checks, both clean

## Tomorrow's queue
5 issues with `dispatch:ready` label not yet picked up.
```

The digest is **the** daily review artifact. If Michael reads nothing else, he reads this.

## 7. SLOs

| SLO | Target | Measurement |
|---|---|---|
| **Tag latency** p95 | < 50ms per event | time between event published to bus → tagged in `tagged_events` |
| **Tag latency** p99 | < 200ms | same |
| **Digest emit time** | 08:00:00 local ± 5 min | `digest_runs.ran_at - scheduled_ts` |
| **Michael pages** | ≤ 1/day on a normal day; 0/day on a quiet day | `digest_runs.paged_michael` |
| **Missed events** | 0 | events in bus not in `tagged_events` after 1 hour |
| **State durability** | restart-safe | kill -9 then restart; tagged count survives |

A breach of any SLO escalates automatically (the curator pings itself).

## 8. Failure modes

| Failure | Curator behavior |
|---|---|
| Bus SQLite missing/corrupt | Log error, emit empty digest at 8am, page Michael |
| Rule predicate throws | Catch, tag as `escalate` with reason=`rule_error:<rule_name>`, continue |
| Tag insert fails (disk full) | Retry 3× with backoff; on final failure, escalate + page Michael |
| Digest render fails | Escalate; do NOT block bus consumption |
| Telegram ping fails | Log error; do NOT retry (Michael will read the digest file directly anyway) |
| Curator process dies | systemd Restart=always; on restart, replay missed events from `last_processed_rowid` |

## 9. Implementation contract

### Public API

```python
# prismatic/curator/__init__.py
from .tagger import tag_event, TagRule, TAG_RULES
from .digest import render_digest, write_digest, DigestCounts
from .store import CuratorStore, TagRecord, DigestRecord

__all__ = [
    "tag_event", "TagRule", "TAG_RULES",
    "render_digest", "write_digest", "DigestCounts",
    "CuratorStore", "TagRecord", "DigestRecord",
]

# prismatic/curator/lane.py
class CuratorLane:
    """The curator lane supervisor. One instance, runs continuously."""

    def __init__(self, db_path: str = "~/.prismatic/curator/state.sqlite",
                 bus_path: str = "~/.prismatic/bus/event_log.sqlite"):
        ...

    async def run(self) -> None:
        """Main loop. Polls bus for new events (processed=1), tags them, persists."""
        ...

    async def emit_daily_digest(self) -> Path:
        """Idempotent: writes today's digest and returns the path."""
        ...
```

### systemd unit

`/etc/systemd/system/prismatic-curator.service`:

```ini
[Unit]
Description=Prismatic Curator Lane — tags bus events, emits 8am digest
After=prismatic-gateway.service prismatic-consumer.service
Wants=prismatic-gateway.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/work/prismatic-engine
ExecStart=/home/ubuntu/.prismatic/venv_stable/bin/python3 -m prismatic.curator.lane
EnvironmentFile=/home/ubuntu/.prismatic/env.d/linear_oauth.env
Environment=PRISMATIC_HOME=/home/ubuntu
Environment=PRISMATIC_BUS_DB=/home/ubuntu/.prismatic/bus/event_log.sqlite
Environment=PRISMATIC_CURATOR_DB=/home/ubuntu/.prismatic/curator/state.sqlite
Environment=PRISMATIC_DIGEST_HOUR=8
Restart=always
RestartSec=5
StandardOutput=append:/home/ubuntu/.prismatic/logs/curator.log
StandardError=append:/home/ubuntu/.prismatic/logs/curator.log

[Install]
WantedBy=multi-user.target
```

### Timer for 8am digest

`/etc/systemd/system/prismatic-curator-digest.timer`:

```ini
[Unit]
Description=Prismatic Curator — 8am daily digest

[Timer]
OnCalendar=*-*-* 08:00:00 America/Denver
Persistent=true

[Install]
WantedBy=timers.target
```

## 10. Acceptance criteria

This spec is **done** when:

- [ ] `prismatic/curator/{__init__.py, lane.py, tagger.py, digest.py, store.py, rules.py}` exists
- [ ] All 11 tag rules in §4 are implemented in `rules.py`
- [ ] `CuratorLane.run()` consumes bus events and persists tags
- [ ] `CuratorLane.emit_daily_digest()` produces the §6 Markdown
- [ ] systemd unit `prismatic-curator.service` runs continuously
- [ ] systemd timer `prismatic-curator-digest.timer` fires at 8am
- [ ] Live test: 7 consecutive days with a digest in `~/.prismatic/curator/digests/`
- [ ] All SLOs in §7 hold for the test period

## 11. Open questions (for Opus review)

1. Should the curator be one process or two (tagger + digester)?
2. Should `escalate` events also push to Telegram immediately, or only at digest time?
3. What's the right retention policy for `tagged_events`? Forever? 30 days?
4. How do we handle a bus event whose `payload` doesn't match any rule? Default to `escalate`?

## 12. References

- North Star audit: `/home/ubuntu/work/okf/operations/2026-06-30-north-star-audit.md` §E
- Epic roadmap: `/home/ubuntu/work/okf/operations/2026-06-30-prismatic-engine-epic-roadmap.md` §Epic 1
- Phase D post-publish chain: `/home/ubuntu/work/prismatic-engine/docs/phase-d-post-publish-chain.md`
- Existing event_bus: `/home/ubuntu/work/prismatic-engine/prismatic/gateway/event_bus.py`
- Existing dispatch consumer: `/archive/agy_sandboxes/GRO-2308/prismatic/gateway/event_handlers/dispatch_consumer_v3.py`

---

*This spec is the contract Epic 1.4 (curator lane implementation) will cite. Changes to this file require Opus review.*