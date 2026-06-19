---
type: Standard
title: Event-Driven Dispatch Architecture
description: Canonical architecture for the Prismatic Engine dispatcher. Webhook-first, cron-safety-net. Replaces the prior poll-based dispatch with event-driven path (GRO-2048) and HMAC-validated webhook receiver (GRO-2047).
resource: okf/standards/dispatch-architecture.md
tags: [standard, dispatch, webhook, event-driven, linear, prismatic-engine]
timestamp: 2026-06-19T12:30:00Z
linear_issue: GRO-2048
git_repo: mbgulden/prismatic-engine
git_path: prismatic/gateway/server.py
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Event-Driven Dispatch Architecture

**Status:** ENFORCED as of Jun 19 2026 (post Tier 6 webhook handler landing).
**Replaces:** Prior `linear_webhook` stub that logged body size and returned OK without action.
**Refs:** GRO-2047 (webhook handler), GRO-2048 (event-driven path), GRO-2050 (cron reduction).

## Why event-driven

Before Tier 6, dispatch was **poll-only**: a cron tick every 5 min queried Linear for issues with `agent:*` labels. That had two costs:

1. **Latency.** Worst-case 5 min delay between label change and dispatch start.
2. **Budget.** Every poll consumed Linear API tokens whether anything changed or not. ~336 req/hour baseline.

Tier 6 inverts this. Linear **pushes** events to the engine webhook; the engine reacts in seconds. Cron becomes a daily safety net for missed events.

## Architecture

```text
Linear Issue event
  ↓
Linear POSTs to https://<engine>/api/gateway/linear
  ↓
prismatic/gateway/server.py::linear_webhook
  ├─ 1. Validate HMAC (PRISMATIC_LINEAR_WEBHOOK_SECRET)
  ├─ 2. Parse event (type, action, data.labels)
  ├─ 3. Decide path:
  │    ├─ Issue + agent:* label + update/create → direct dispatch
  │    │    └─ dispatcher.dispatch_once()  ← event-driven path (GRO-2048)
  │    └─ Everything else → SQLite queue (catch-up sweep)
  │         └─ Daily safety-net cron (post-GRO-2050)
  └─ 4. Return JSON status to Linear
```

### Component map

| Layer | Path | Role | GRO |
|---|---|---|---|
| Webhook receiver | `prismatic/gateway/server.py::linear_webhook` | HMAC + parse + decide path | GRO-2047 |
| Dispatcher | `prismatic/dispatcher.py::dispatch_once` | Iterate agents, run gates, launch | GRO-2048 |
| Dedup | `prismatic/dispatcher.py::EventRouterDedup` | Idempotency across cycles | GRO-2024 |
| Budget gate | `prismatic/linear/budget.py::LinearBudget.check_and_consume` | Per-issue Linear API token cap | GRO-2034 |
| Review-loop gate | `prismatic/dispatcher.py::evaluate_transition_approval` | Mode-switch approval | GRO-2024 |
| Bypass detection | `prismatic/dispatcher.py` (next_label logic) | Block non-Fred transitions to Done | GRO-2024 |
| Webhook queue | `prismatic_state/linear_webhook_queue.db` | Catch-up for non-direct events | GRO-2050 |
| Safety-net cron | `agent_dispatcher.py` (orchestrator profile) | Daily sweep of missed events | GRO-2050 |

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` | unset (HMAC skipped) | Linear webhook signing secret. **Set in production.** |
| `PRISMATIC_STATE_DIR` | `./prismatic_state` | Where webhook queue DB and dedup live |
| `PRISMATIC_CURRENT_AGENT_NAME` | unset | Per-call agent context for budget gate |
| `PRISMATIC_POLL_INTERVAL` | 30 | Cron poll interval (deprecated path; not used by webhook) |

## Path decision logic

The webhook handler decides per-event whether to dispatch directly or queue:

```python
# pseudocode at prismatic/gateway/server.py
has_agent_label = any(label.startswith("agent:") for issue in event.data.labels)
if event.type == "Issue" and event.action in ("update", "create") and has_agent_label:
    # Event-driven path: dispatch now
    dispatcher.dispatch_once(dedup=dedup)
    return {"status": "dispatched", "identifier": identifier}
else:
    # Catch-up queue
    queue_to_sqlite(event)
    return {"status": "queued"}
```

Why this rule? Only Issue events with agent labels are dispatch-relevant. Comment events, project events, label-only changes on non-Issue resources — all queued for the daily sweep.

## Failure modes

| Failure | Behavior |
|---|---|
| Bad HMAC signature | 401, no dispatch, no queue write |
| Missing signature header | 401 |
| Malformed JSON payload | 400 |
| `dispatch_once()` raises | Event queued to SQLite (don't lose it) |
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` unset | HMAC skipped; **dev only**, never deploy this way |
| Linear rate-limited upstream | Local budget gate stops new dispatches; webhook still returns 200 (queue absorbs the work) |
| Engine down / 500 | Linear retries with exponential backoff |

## Linear API budget impact

Pre-Tier 6 baseline:
- Dispatcher cron every 5 min: ~336 req/hour
- Webhook trigger cron every 2 min: ~120 req/hour
- **Total: ~456 req/hour baseline, 2500/hour ceiling → 18% of capacity**

Post-Tier 6 target (after GRO-2050 lands):
- Webhook handler: ~5-10 req/hour (each event = 1 Linear lookup for state)
- Daily safety-net cron: ~10 req/hour
- **Total: ~20 req/hour baseline → 0.8% of capacity**

That's a **23x reduction** in Linear API usage. We go from 18% capacity baseline → 0.8% capacity baseline. The remaining 99% is headroom for actual work.

## Tier 6 status

| Tier 6 piece | Status | GRO | ETA |
|---|---|---|---|
| Real webhook handler | **DONE** | GRO-2047 | shipped Jun 19 |
| Event-driven path | **DONE** | GRO-2048 | shipped Jun 19 |
| AGY chat → Linear comment adapter | pending | GRO-2049 | Tier 6B |
| Cron polling reduction | pending | GRO-2050 | after GRO-2049 |
| Standalone install test | pending | GRO-2043 | Tier 6A |
| Path portability | pending | GRO-2044 | Tier 6A |
| Journal.py Hermes fallback | pending | GRO-2045 | Tier 6A |
| Standalone launcher per lane | pending | GRO-2046 | Tier 6A |

## Verification

End-to-end smoke test (`tests/test_webhook_handler.py`):

```bash
PYTHONPATH=. python3 -m pytest tests/test_webhook_handler.py -v
# 9 passed in 1.64s
```

Coverage:

1. `test_dispatches_with_agent_label` — agent:* on Issue/update → direct dispatch
2. `test_queues_without_agent_label` — no agent label → SQLite queue
3. `test_rejects_bad_signature` — 401 on wrong HMAC
4. `test_rejects_missing_signature` — 401 on missing Linear-Signature header
5. `test_rejects_bad_json` — 400 on malformed payload
6. `test_queues_on_dispatch_failure` — dispatch raise → still queued (no loss)
7. `test_non_issue_events_are_queued` — Comment events bypass dispatch
8. `test_no_secret_skips_hmac_validation` — empty secret → skip
9. `test_duplicate_event_is_idempotent` — same body twice → 1 queue row (event_id dedup)

Stress test (`tests/test_dispatcher_stress.py`): 4 tests verify dispatch scaling, dedup idempotency, budget gate per issue, and the GRO-2034 follow-up (no double-consume).

## Adoption checklist for new projects

1. **Set `PRISMATIC_LINEAR_WEBHOOK_SECRET`** in production (random 32+ bytes, stored in vault).
2. **Point Linear webhook** to `https://<your-engine>/api/gateway/linear`.
3. **Verify** with `tests/test_webhook_handler.py` before deploying.
4. **Monitor** `prismatic_state/linear_webhook_queue.db` for queue depth.
5. **Watch** budget consumption in `prismatic_state/linear_budget.db`.

## Related docs

- `okf/standards/review-loop-canonical.md` — review-loop enforcement (GRO-2024)
- `okf/standards/linear-rate-limit.md` — budget codification (GRO-2008/2010/2020/2034)
- `okf/standards/dispatch-architecture.md` — this doc
- `okf/decisions/okf-adoption.md` — why OKF
- `okf/decisions/tier-6-event-driven.md` — Tier 6 decision record (pending)