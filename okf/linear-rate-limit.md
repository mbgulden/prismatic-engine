---
type: Standard
title: Linear API Rate-Limit Codification
description: Codification of the 2500 req/hour Linear API budget. Every GraphQL call goes through LinearBudget.check_and_consume() before hitting the API.
resource: okf/linear-rate-limit.md
tags: [linear, rate-limit, linearbudget, agent:agy, codification, prismatic-engine]
timestamp: 2026-06-19T10:30:00Z
linear_issue: GRO-2008,GRO-2010,GRO-2020,GRO-2034
git_repo: mbgulden/prismatic-engine
git_path: okf/linear-rate-limit.md
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Linear API Rate-Limit Codification

**Canonical version:** [`growthwebdev-knowledge/okf/standards/linear-rate-limit.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/linear-rate-limit.md).
**This spoke version** mirrors the canonical standard with prismatic-engine-specific
context.

**Status:** ENFORCED in `prismatic/linear_budget.py` and `agent_dispatcher.py` as of Jun 18 2026.
**Linear budget:** 2500 req/hour (default).

## What this standard guarantees

Every Linear GraphQL call — whether from a cron, a webhook handler, or a
manual operator — gates through `LinearBudget.check_and_consume(bucket)` before
the request is sent. When the bucket is exhausted, the call is refused with
a clear error rather than silently exhausting the API quota.

## Where enforcement lives (engine)

| Path | Role | GRO |
|---|---|---|
| `prismatic/linear_budget.py` | Canonical engine module. Defines `LinearBudget.check_and_consume()`. | GRO-2008/2020 |
| `prismatic/linear/budget.py` | Legacy shim (re-exports from canonical). Kept for backward compat. | GRO-2020 |
| `~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py::_linear_gql()` | Wraps direct GraphQL with `LinearBudget.check_and_consume()`. | GRO-2034 |
| `agent_dispatcher.py::gql()` | Top-level dispatch helper. Calls `linear_call` (canonical) or `_linear_gql` (fallback). Both gated. | GRO-2034 |
| `scripts/check_linear_cron_rate.sh` | Lint script: fails CI if total expected cron usage > 2000 req/hour. | GRO-2008 |

## API

```python
from prismatic.linear.budget import LinearBudget

budget = LinearBudget(limit_per_hour=2500)
if budget.check_and_consume("cron.agent_dispatcher"):
    response = linear_call(...)
else:
    raise Exception("Rate limit exceeded for cron.agent_dispatcher")
```

Persistence: `prismatic_state/linear_budget.db` (SQLite).

## Buckets used today

| Bucket | Consumer | Approx rate |
|---|---|---|
| `cron.agent_dispatcher` | Orchestrator profile dispatcher | ~336/hour (every 5 min × 28 calls/tick) |
| `dispatcher.agent_*` | Engine dispatcher (per-agent) | varies |
| `webhook.linear` | Linear webhook handler | spike-prone |

## Webhook loophole (GRO-2034)

Webhook delivery itself is independent of Linear API budget (server-to-server
push). But the webhook handler scripts trigger `agent_dispatcher.py --one-shot`,
which now goes through `LinearBudget.check_and_consume()`. The dispatcher is
the single chokepoint for both cron and webhook paths.

Worst-case burst impact before GRO-2034: 100 webhook events × ~3 GraphQL calls
= 300 stealth-budget-burns/hour. After GRO-2034: same 300 calls, each gated;
budget exhaustion halts dispatch cleanly.

## Engine integration

The orchestrator dispatcher's `gql()` function has two paths:

1. **Canonical:** `prismatic.linear_budget.linear_call("bucket", query, vars)` — already gated.
2. **Legacy fallback** (when the engine module isn't fully importable):
   `agent_dispatcher.py::_linear_gql()` — now also gated (GRO-2034).

Both paths consume from the same `LinearBudget` instance and the same SQLite DB.

## When the budget is exhausted

The dispatcher prints:

```text
Linear API rate limit exceeded (budget exhausted for cron.agent_dispatcher)
```

This is a hard fail, not a soft warning. The dispatch cycle aborts. The next
cron tick (5 min later) gets a fresh refill and proceeds.

## How to add a new budget-aware caller (in the engine)

1. Import `LinearBudget` from `prismatic.linear.budget`.
2. Wrap your call site:
   ```python
   budget = LinearBudget()
   if not budget.check_and_consume("<your_bucket_name>"):
       raise Exception("Linear budget exhausted")
   response = linear_call(...)
   ```
3. Document your bucket in the "Buckets used today" table above.
4. Run `scripts/check_linear_cron_rate.sh` and update it if your caller adds
   > 200 req/hour.

## Linear history

- GRO-2008 — LinearBudget codification (initial spec + token bucket)
- GRO-2010 — Recovery runbook
- GRO-2020 — Engine module move (`prismatic/linear_budget.py` canonical, shim at `prismatic/linear/budget.py`)
- GRO-2034 — Dispatcher fallback path wired through LinearBudget (closed loophole)
- GRO-2037 — Lint script must include webhook handlers (follow-up)
