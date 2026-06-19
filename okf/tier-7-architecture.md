---
type: Project
title: Tier 7 Dispatch Architecture — How It All Fits Together
description: System diagram + data flow for the Prismatic Engine dispatch path. Read this if you're debugging dispatch or adding a new endpoint.
resource: okf/projects/prismatic-engine/tier-7-architecture.md
tags: [project, prismatic-engine, architecture, dispatch, data-flow]
timestamp: 2026-06-19T20:00:00Z
linear_issue: GRO-2047
---

# Tier 7 Dispatch Architecture — How It All Fits Together

This document shows the runtime architecture: every component, every data flow, every failure mode. For the *why*, see `tier-7-journey.md`. For the *requirements*, see `dispatch-production-grade.md`.

## High-level system

```
                       ┌─────────────────────────────────────────────────┐
                       │            Linear / GitHub / WebSocket          │
                       │              (external systems)                │
                       └────────────────────────┬────────────────────────┘
                                                │
                       ┌────────────────────────▼────────────────────────┐
                       │     Linear / GitHub Webhook (HTTPS POST)        │
                       └────────────────────────┬────────────────────────┘
                                                │
        ┌───────────────────────────────────────▼────────────────────────┐
        │              Prismatic Engine Gateway (FastAPI :9000)          │
        │  ┌──────────────────────────────────────────────────────────┐  │
        │  │ Middleware stack (in order):                             │  │
        │  │   1. request_id    — generate UUID4, echo X-Request-ID   │  │
        │  │   2. rate_limit    — per-IP sliding window (60/60s)      │  │
        │  │   3. limit_body    — 1MB cap, reject chunked / missing  │  │
        │  │   4. ip_allowlist  — localhost + trusted proxies        │  │
        │  └──────────────────────────────────────────────────────────┘  │
        │  ┌──────────────────────────────────────────────────────────┐  │
        │  │ /api/gateway/linear    — HMAC, replay, dispatch          │  │
        │  │ /api/gateway/github    — HMAC, queue to SQLite           │  │
        │  │ /ws                    — Bearer/HMAC, broadcast events   │  │
        │  │ /health, /locks, /runs, /schedules, /chat/sessions       │  │
        │  └──────────────────────────────────────────────────────────┘  │
        └────────────────┬───────────────────────────────────────┬───────┘
                         │                                       │
                         │ dispatch                              │ broadcast
                         ▼                                       ▼
        ┌─────────────────────────────────┐    ┌────────────────────────────┐
        │  LinearBudget.check_and_consume │    │  WebSocket clients (N)     │
        │  (gatekeeper: 2500 req/hour)    │    └────────────────────────────┘
        └────────────────┬────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────────────────┐
        │        prismatic/dispatcher.py                  │
        │  ┌─────────────────────────────────────────────┐│
        │  │ Linear API access via gql()                ││
        │  │ (single GraphQL helper, LinearBudget-gated)││
        │  └────────────────┬────────────────────────────┘│
        │                   ▼                              │
        │  ┌─────────────────────────────────────────────┐│
        │  │ dispatch_issue_by_identifier(identifier)  ││
        │  │   1. dedup check                            ││
        │  │   2. agy_stall_tracker check               ││
        │  │   3. evaluate_transition_approval          ││
        │  │   4. evaluate_agent_launch (credit policy) ││
        │  │   5. telemetry: record_credit              ││
        │  │   6. AGENT_LAUNCHERS[agent](issue_id, ...)  ││
        │  │   7. telemetry + IPC event + Linear comment││
        │  └────────────────┬────────────────────────────┘│
        └────────────────────┼─────────────────────────────┘
                             │
                             ▼
        ┌─────────────────────────────────────────────────┐
        │              Agent processes (N)                │
        │   AGY │ Jules CLI (jules.google.com) │ Codex     │
        │   Kai │ Ned │ ...                                │
        └────────────────────┬────────────────────────────┘
                             │
                             ▼
        ┌─────────────────────────────────────────────────┐
        │         prismatic_state/ (SQLite)               │
        │  ├─ event_router.db    — dedup + stall tracker  │
        │  ├─ webhook_audit.log  — JSONL append-only      │
        │  ├─ budget_logs        — LinearBudget state     │
        │  └─ linear_webhook_queue.db — fallback queue    │
        └─────────────────────────────────────────────────┘
                             │
                             │ daily safety-net sweep
                             ▼
        ┌─────────────────────────────────────────────────┐
        │  Cron: agent_dispatcher.py (daily at 08:00 UTC) │
        │  ──────────────────────────────────────────────│
        │  Catches missed webhooks (Linear 24h retry).   │
        │  Logs "AGY idle" if nothing to dispatch.        │
        └─────────────────────────────────────────────────┘
```

## Data flows

### Flow 1: Linear webhook → dispatch (the primary path)

```
Linear
  │ POST /api/gateway/linear
  │ Headers: Linear-Signature: <hmac>
  │ Body: {"type": "Issue", "action": "update",
  │        "data": {"identifier": "GRO-2051", "labels": [...]},
  │        "createdAt": "2026-06-19T19:30:00Z"}
  ▼
[1. request_id middleware]
  │ Generate UUID4, set X-Request-ID
  ▼
[2. rate_limit middleware]
  │ Check 60/60s window for client IP
  │ On hit: 429 with Retry-After
  ▼
[3. limit_body_size middleware]
  │ Check Content-Length ≤ 1MB
  │ Reject chunked (411) or missing CL (413)
  ▼
[4. ip_allowlist middleware]
  │ Webhook endpoints exempt (HMAC-authenticated)
  ▼
linear_webhook handler
  │ 1. Verify HMAC (constant-time, dual-secret OK)
  │ 2. Verify createdAt freshness (5min window)
  │ 3. Parse JSON
  │ 4. Check for agent:* label
  │
  ├─ No agent label → queue to linear_webhook_queue.db
  │
  └─ Has agent label → dispatch_issue_by_identifier("GRO-2051")
                       │
                       ▼
                     [LinearBudget.check_and_consume]
                       │ Decrement token (costs 1-2)
                       │ On exhaustion: log + skip
                       ▼
                     [dispatch_once-style gate stack, 7 gates]
                       │
                       ├─ Gate 1: dedup.is_processed → skip if yes
                       ├─ Gate 2: agy_stall_tracker → skip if escalated
                       ├─ Gate 3: evaluate_transition_approval → skip if paused
                       ├─ Gate 4: evaluate_agent_launch
                       │           ├─ DENY: log + comment + skip
                       │           ├─ ASK_USER: log + skip + mark processed
                       │           └─ WARN/ALLOW: log + continue
                       ├─ Gate 5: collector.record_credit (best-effort)
                       ├─ Gate 6: AGENT_LAUNCHERS[agent](issue_id, title)
                       │           ├─ Success: continue
                       │           └─ Failure: return False
                       └─ Gate 7: post-launch
                                  ├─ collector.record_agent_run
                                  ├─ _emit_agent_event("agent_launched", ...)
                                  └─ add_comment("🤖 **AGY** picked up...")
                       │
                       ▼
                     Agent subprocess running (AGY/Jules/Codex/...)
                       │
                       ▼
                     Linear: issue gets comment + status change
                     Audit: append JSON line to webhook_audit.log
                     Telemetry: events flow to collector → dashboards
```

### Flow 2: Cron safety-net sweep (daily 08:00 UTC)

```
Cron
  │ Run agent_dispatcher.py
  ▼
[LinearBudget.check_and_consume] (1 token for cycle setup)
  │
  ▼
dispatch_once()
  │ For each agent in AGENT_CONFIG:
  │   For each issue with agent:* label:
  │     Apply full 7-gate stack
  │
  ├─ Most issues are deduped (already dispatched today)
  │  → counts["deduped"] += 1
  │
  ├─ A few new issues dispatch (counts["dispatched"] += 1)
  │
  └─ Some issues fail gates (counts["blocked"/"skipped"] += 1)
  │
  ▼
Log summary to local file (deliver=local)
  │ "🤖 Morning sweep: 0 dispatched, 142 deduped, 5 blocked"
```

### Flow 3: WebSocket event broadcast

```
[Some component in engine calls _emit_agent_event]
  │ _emit_agent_event("agent_launched", "agy", "GRO-2051", cycle_id=...)
  ▼
_broadcast_event_to_ws()
  │ For each connected WS client:
  │   Send JSON event
  ▼
Client receives
  │ Display in real-time dashboard
```

The WebSocket auth happens at upgrade time (one-time, before any events flow).

## File map

| File | Role |
|---|---|
| `prismatic/gateway/server.py` | FastAPI app, all HTTP/WS handlers, middleware stack |
| `prismatic/dispatcher.py` | Linear access, dispatch gate stack, agent launchers |
| `prismatic/linear/budget.py` | LinearBudget: token bucket per agent |
| `prismatic/credit_policy_engine.py` | evaluate_agent_launch, PolicyAction enum |
| `prismatic/mode_switch.py` | evaluate_transition_approval |
| `tests/test_webhook_security.py` | 22+ security tests |
| `tests/test_websocket_auth.py` | 11 WS auth tests |
| `tests/test_dispatch_single_issue.py` | 8 single-issue dispatch tests |
| `tests/test_dispatcher_stress.py` | 4 stress tests |
| `~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py` | Cron-driven full cycle (daily) |
| `~/.hermes/profiles/orchestrator/scripts/linear_api_compat.py` | Compat shim for legacy scripts |

## State DB layout

`prismatic_state/` (configurable via `PRISMATIC_STATE_DIR`):

```
event_router.db          — dedup + stall tracker (SQLite)
  ├─ dedup_log           (issue_id, label, cycle_id, processed_at, ttl_seconds)
  ├─ agy_stall_tracker   (issue_id, cycle_count, escalated)
  └─ agent_credit_log    (timestamp, agent, cost, action, reason)

webhook_audit.log        — JSONL append-only
  └─ One line per webhook outcome (with request_id for correlation)

linear_webhook_queue.db  — fallback queue for non-dispatchable events
  └─ linear_webhook_queue (event_id, raw_json, queued_at, processed)

budget_logs              — LinearBudget state
  └─ (timestamp, agent_name, cost, action, reason, retry_after)
```

## Configuration env vars

See `dispatch-production-grade.md` for the full list. Key ones:

- `PRISMATIC_LINEAR_WEBHOOK_SECRET` (required)
- `PRISMATIC_GITHUB_WEBHOOK_SECRET` (required)
- `PRISMATIC_WS_TOKEN` or `PRISMATIC_WS_SECRET` (one required for production)
- `PRISMATIC_STATE_DIR` (default `./prismatic_state/`)
- `PRISMATIC_ALLOWED_IPS` (default localhost)
- `PRISMATIC_TRUSTED_PROXIES` (default localhost)

## Failure modes

| Failure | Behavior |
|---|---|
| HMAC mismatch | 401, audit log entry, no dispatch |
| Replay (stale createdAt) | 401, audit log entry |
| Body > 1MB | 413 |
| Transfer-Encoding: chunked | 411 (chunked bypass blocked) |
| Rate limit hit | 429 with Retry-After |
| IP not in allowlist | 403 |
| Missing Content-Length | 413 |
| LinearBudget exhausted | Skip + log; daily cron retries |
| Credit policy DENY | Log + Linear comment + skip |
| Credit policy ASK_USER | Log + skip + mark processed |
| AGY escalated | Skip (no relaunch) |
| Mode switch paused | Skip (transition paused) |
| Launcher returns False | Return False from dispatch |
| Linear API error | Caught, logged, dispatch aborts |
| Process crash | systemd restart; daily cron catches missed events |

## What runs where

| Process | Frequency | Trigger |
|---|---|---|
| `prismatic-gateway.service` | Always | systemd (port 9000) |
| Cron `agent_dispatcher.py` | Daily 08:00 UTC | system cron |
| Cron `linear_oauth_refresh.py` | Every 45min | system cron |
| Cron `webhook_audit_rotation` | Daily 02:00 UTC | system cron |
| Agent processes | On-demand | dispatch launches them |
| AGY sandbox supervisor | Event-driven | webhook → new AGY issue |

## Related docs

- `okf/projects/prismatic-engine/tier-7-journey.md` — Chronological narrative
- `okf/standards/dispatch-production-grade.md` — Mandatory requirements
- `okf/standards/webhook-security.md` — Detailed security layer reference
- `okf/standards/dispatch-architecture.md` — Webhook-first architecture decision
- `okf/decisions/event-driven-dispatch.md` — Why webhook over poll
