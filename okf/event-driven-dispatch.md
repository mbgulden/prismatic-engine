---
type: Decision
title: Event-Driven Dispatch (Tier 6 part 1)
description: Replace poll-only dispatcher with webhook-first event-driven path. HMAC-validated receiver at prismatic/gateway/server.py, direct dispatch on agent:* labels, SQLite queue fallback.
resource: okf/decisions/event-driven-dispatch.md
tags: [decision, adr, dispatch, webhook, linear, prismatic-engine, tier-6]
timestamp: 2026-06-19T12:30:00Z
linear_issue: GRO-2042
git_repo: mbgulden/prismatic-engine
git_path: prismatic/gateway/server.py
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Event-Driven Dispatch (Tier 6, part 1)

## Status

Accepted (Jun 19 2026). Implementation in progress.

## Context

The Prismatic Engine dispatcher was poll-only: a cron tick every 5 minutes queried Linear for issues with `agent:*` labels and dispatched them. This worked but had two costs:

1. **Latency.** Worst-case 5-minute delay between label change and dispatch start.
2. **Budget.** ~336 req/hour baseline from the cron alone (~18% of the 2500/hour Linear API ceiling).

Linear supports webhook delivery, but the engine's webhook handler at `prismatic/gateway/server.py::linear_webhook` was a stub — it logged body size and returned OK without action.

## Decision

Replace the webhook stub with a real handler that:

1. **Validates HMAC** signature against `PRISMATIC_LINEAR_WEBHOOK_SECRET`.
2. **Parses the event payload** (type, action, data.labels).
3. **Dispatches directly** for Issue events with `agent:*` labels (event-driven path).
4. **Queues to SQLite** for everything else (catch-up sweep).
5. **Never 500s** — dispatch failures queue the event so it's never lost.

Cron becomes a daily safety-net sweep instead of a 5-minute poll.

## Consequences

### Positive

- **Latency.** Event → dispatch in seconds (was: up to 5 min).
- **Budget.** Post-GRO-2050: ~20 req/hour baseline (was: ~456 req/hour). 23x reduction.
- **Reliability.** Webhook retries + queue fallback mean no event is ever silently lost.
- **Testability.** `tests/test_webhook_handler.py` covers 9 categories: happy path, queue path, auth, input validation, dispatch-failure resilience, filtering, dev mode, idempotency.

### Negative

- **Webhook security.** HMAC secret management becomes critical. Production deployments must set `PRISMATIC_LINEAR_WEBHOOK_SECRET` to a 32+ byte random value stored in vault.
- **Queue growth.** The catch-up SQLite queue can grow unbounded if Linear rate-limits for long periods. Need a periodic prune (not yet implemented).
- **Two dispatchers coexist** during migration: profile `agent_dispatcher.py` (cron path) + engine `prismatic/dispatcher.py` (webhook path). Tier 2 wrapper covers the seam; full unification deferred.

## Alternatives considered

### A. Poll-only with shorter interval

Reduce cron to 1 minute. Pros: simpler. Cons: still poll; latency still 1 min worst case; budget usage doubles.

### B. Webhook-only (no cron)

Webhook handler does everything. Cons: if the engine is down during a Linear event, the event is lost forever. Linear does retry, but eventual-loss is real.

### C. Webhook + event sourcing (rejected for now)

Persist every webhook event to a journal, dispatch from journal. Pros: perfect audit trail. Cons: significant complexity; not needed for current scale.

**Chosen: B + safety-net cron.** Webhook handles the fast path; daily cron catches missed events. Path forward is event sourcing (Phase 2).

## Implementation

| GRO | Piece | Status |
|---|---|---|
| GRO-2047 | Real webhook handler | ✅ Shipped (Jun 19 2026) |
| GRO-2048 | Event-driven dispatch path | ✅ Shipped (Jun 19 2026) |
| GRO-2049 | AGY chat → Linear comment adapter | 🚧 Pending (Tier 6B) |
| GRO-2050 | Cron polling reduction | 🚧 Pending (after GRO-2049) |

## Related docs

- [`../standards/dispatch-architecture.md`](../standards/dispatch-architecture.md) — full architecture doc
- [`../standards/agy-peer-review.md`](../standards/agy-peer-review.md) — peer-review loop that caught GRO-2034 bugs
- [`../integrations/webhook-handler-test-pattern.md`](../integrations/webhook-handler-test-pattern.md) — 9-test pattern

## Verification

- `tests/test_webhook_handler.py` — 9 tests, all passing in 1.64s
- `tests/test_dispatcher_stress.py` — 4 stress tests (DEFAULT_DB_PATH isolation applied; rerun needed)
- Smoke test: synthetic HMAC-signed event returned HTTP 200 `{status:dispatched, identifier:GRO-2051}` and ran a full dispatch cycle (11 transition comments posted to Linear)

## Sign-off

- Fred (orchestrator): approved 2026-06-19
- AGY peer-review (GRO-2064): pending
- AGY peer-review (GRO-2065): pending
- AGY peer-review (GRO-2066): pending