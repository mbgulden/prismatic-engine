# Ingestion Queue Audit after Fred's Two-Pass Dashboard Work

**Date:** 2026-07-15  
**Prepared by:** Kai  
**Audience:** Michael / Fred  
**Repo/worktree observed:** `/home/ubuntu/work/prismatic-engine`  
**Branch observed:** `deploy-fresh`  
**Observed branch status:** `deploy-fresh...origin/deploy-fresh [ahead 1]`  
**Observed upstream HEAD:** `5489f542` — `Merge pull request #262 from mbgulden/feature/fred-okf-location-map`  
**Primary ingestion queue PR observed:** `#261` / `9c20214e` — `Merge pull request #261 from mbgulden/feature/fred-ingestion-queue-real-contract`  

## Executive summary

Fred's two-pass ingestion queue work materially improved the dashboard path. The important change is that the dashboard is no longer merely reading recent EventBus history as a queue-shaped stand-in. It now has a durable ingestion queue contract centered on `linear_webhook_queue.db`, with real queue payloads, stats, retry, and purge semantics.

This is a strong step forward and should be preserved.

The system is **not yet fully ready to claim `DASHBOARD_DISPATCH_INGESTION_READY_OK`**, because the dependency-complete FastAPI/TestClient verifier could not run in this shell and because the dispatch-preflight / one-task AGY proof is still outside this audit. But the core ingestion queue code path passed focused ad-hoc behavior verification.

## Bottom-line status

| Area | Status | Notes |
|---|---|---|
| Durable queue DB contract | **Pass / good** | `prismatic/ingestion_queue.py` creates/migrates `linear_webhook_queue.db` and normalizes rows. |
| Linear webhook enqueue | **Pass / good** | `linear_webhook()` calls `enqueue_linear_event()` after parsing Linear webhook body. |
| Dashboard queue API | **Pass / good** | `/api/webhooks/queue` and `/api/gateway/webhooks/queue` now return durable queue payloads. |
| Dashboard stats API | **Pass / good** | `/api/webhooks/stats` and `/api/gateway/webhooks/stats` return `linear_webhook_queue.db` source. |
| Retry control | **Pass / good with caveat** | Retry resets a durable row to `pending`; ensure UI messaging distinguishes retry-request vs immediate dispatch. |
| Purge control | **Pass / good with caveat** | Purge deletes terminal rows; keep confirmation/destructive-action guard in UI. |
| Dashboard JavaScript syntax | **Pass** | Inline dashboard JS passed `node --check`. |
| Python syntax | **Pass** | Key queue/dashboard modules passed `py_compile`. |
| Full FastAPI contract verifier | **Blocked by environment** | `ModuleNotFoundError: No module named 'fastapi'`. This is not proof of app failure, but full verification remains pending. |
| AGY dispatch recovery proof | **Pending** | Still requires valid model/preflight and one-task redispatch proof. |

## What changed since the prior dashboard handoff

The earlier dashboard preservation report identified the `Ingestion Queue` tab as promising but still too compatibility-shaped. At that point, the dashboard path was closer to:

```text
recent event history / compatibility rows
+ safe no-op retry/purge acknowledgements
```

After Fred's latest pass, the current observed path is closer to:

```text
Linear webhook
→ durable enqueue into linear_webhook_queue.db
→ dashboard queue/stats APIs read durable DB
→ retry mutates durable queue row to pending
→ purge removes terminal durable rows
→ dashboard tab renders queue/status/dispatcher/recovery signals
```

That is the right direction.

## Key files to preserve

### Core ingestion queue contract

- [`prismatic/ingestion_queue.py`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/prismatic/ingestion_queue.py)

Important functions observed:

| Function | Purpose |
|---|---|
| `state_dir()` | Resolves PE state dir. |
| `queue_db_path()` | Locates `linear_webhook_queue.db`. |
| `ensure_queue_db()` | Creates/migrates durable queue schema without dropping legacy data. |
| `increment_counter()` | Tracks durable webhook counters. |
| `enqueue_linear_event()` | Inserts Linear webhook events into durable queue. |
| `normalize_row()` | Converts DB rows into dashboard/API payload shape. |
| `queue_payload()` | Returns paginated queue payload with `source=linear_webhook_queue.db`. |
| `queue_stats_payload()` | Returns queue counters/depths/latency source from durable queue. |
| `retry_task()` | Resets a queue row to `pending`. |
| `purge_queue()` | Purges terminal queue rows. |

### Dashboard compatibility/status layer

- [`prismatic/ingestion_status.py`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/prismatic/ingestion_status.py)

This remains useful for normalized dashboard summaries and higher-level recovery/dispatcher status surfaces.

### Gateway API wiring

- [`prismatic/gateway/server.py`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/prismatic/gateway/server.py)

Important routes observed:

| Route | Purpose |
|---|---|
| `GET /api/webhooks/stats` | Durable webhook/queue stats. |
| `GET /api/gateway/webhooks/stats` | Gateway-prefixed alias for stats. |
| `GET /api/webhooks/queue` | Durable queue payload. |
| `GET /api/gateway/webhooks/queue` | Gateway-prefixed alias for queue payload. |
| `POST /api/webhooks/queue/retry/{task_id}` | Reset queue row to pending. |
| `POST /api/gateway/webhooks/queue/retry/{task_id}` | Gateway-prefixed retry alias. |
| `POST /api/webhooks/queue/purge` | Purge terminal queue rows. |
| `POST /api/gateway/webhooks/queue/purge` | Gateway-prefixed purge alias. |
| `GET /api/gateway/dispatcher/status` | Dispatcher state summary for dashboard. |
| `GET /api/gateway/recovery/status` | Recovery/failure taxonomy status. |

### Dashboard UI

- [`prismatic/gateway/templates/dashboard.html`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/prismatic/gateway/templates/dashboard.html)

Important markers observed:

| Marker | Present |
|---|---:|
| Ingestion tab fetches `` `${API_PREFIX}/webhooks/queue` `` | yes |
| `retryTask` | yes |
| `purgeQueue` | yes |
| `queue-total-badge` | yes |
| `stat-pending` | yes |
| `stat-processing` | yes |
| `dispatch_status` rendering | yes |
| `No synthetic fallback rendered` marker | yes |

### Queue drainer

- [`scripts/drain_webhook_queue.py`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/scripts/drain_webhook_queue.py)

This is still the important CLI/bounded drain side of the system. Dashboard retry/purge and queue display are useful, but final readiness requires proving this drainer and the live dispatcher path agree on the same DB/status semantics.

### Dashboard contract verifier

- [`scripts/verify-governance-dashboard-contract.py`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/scripts/verify-governance-dashboard-contract.py)

Fred updated this to know about the real ingestion queue contract. It now explicitly checks for route expectations such as:

```text
/api/webhooks/stats → source = linear_webhook_queue.db
/api/gateway/webhooks/stats → source = linear_webhook_queue.db
/api/webhooks/queue → source = linear_webhook_queue.db
/api/gateway/webhooks/queue → source = linear_webhook_queue.db
```

It also checks that old no-op queue messages are removed:

```text
Retry request recorded by gateway compatibility layer
Purge request accepted by gateway compatibility layer
```

That is exactly the right regression contract direction.

## Verification performed in this audit

### 1. Python syntax compile

Command:

```bash
python3 -m py_compile \
  prismatic/ingestion_queue.py \
  prismatic/ingestion_status.py \
  prismatic/gateway/server.py \
  scripts/drain_webhook_queue.py \
  scripts/verify-governance-dashboard-contract.py
```

Result: **passed**.

### 2. Dashboard inline JavaScript syntax

Command:

```bash
node --check /tmp/hermes-verify-dashboard-inline.js
```

Result: **passed**.

### 3. Focused durable queue behavior check

I ran a temporary `/tmp/hermes-verify-ingestion-queue-behavior.py` script against a temp `PRISMATIC_STATE_DIR`.

Result:

```text
AD_HOC_INGESTION_QUEUE_BEHAVIOR_OK
db=/tmp/hermes-verify-ingestion-0jzcvp5r/state/linear_webhook_queue.db
inserted=True
initial_total=1
stats_source=linear_webhook_queue.db
pending_depth=1
retry_status=ok
retry_item_status=pending
purged=1
```

This verifies, in isolation:

- queue DB creation,
- Linear-style event enqueue,
- durable queue payload source,
- durable stats source,
- retry reset to pending,
- terminal-row purge.

### 4. Full dashboard contract verifier

Command:

```bash
python3 scripts/verify-governance-dashboard-contract.py
```

Result: **blocked by missing dependency**:

```text
ModuleNotFoundError: No module named 'fastapi'
```

This shell lacks project dependencies. That is an environment-prep blocker, not evidence that the dashboard contract is broken. But the full contract verifier still needs to run in Fred's dependency-complete environment before anyone claims suite green.

## Findings

### Finding 1 — Durable queue source is now real

**Severity:** positive finding  
**Status:** preserve

The dashboard queue APIs now point at durable queue logic, not a stand-in EventBus history list:

```text
GET /api/webhooks/queue
GET /api/gateway/webhooks/queue
→ prismatic.ingestion_queue.queue_payload()
→ source = linear_webhook_queue.db
```

This directly addresses the prior audit's biggest ingestion-tab caveat.

### Finding 2 — Retry/purge are real mutations now, not old compatibility no-ops

**Severity:** positive finding with safety follow-up  
**Status:** preserve + harden

The old explicit no-op strings are gone from the queue route body:

```text
Retry request recorded by gateway compatibility layer
Purge request accepted by gateway compatibility layer
```

The new implementation resets rows to `pending` and purges terminal rows. That is correct. The remaining concern is UX/operator safety:

- retry should say “reset to pending,” not imply the dispatch worker has definitely executed it;
- purge should be visibly limited to terminal rows and ideally require confirmation in the dashboard.

### Finding 3 — Dashboard verification script has the right contract but could not run here

**Severity:** P1 verification gap  
**Status:** run in dependency-complete environment

`verify-governance-dashboard-contract.py` now encodes the right source expectations, but it depends on FastAPI/TestClient. This shell does not have `fastapi` installed.

Fred should run the verifier inside the actual project/dev environment and attach output.

### Finding 4 — Drainer/dispatcher end-to-end proof is still the next real readiness gate

**Severity:** P1 release/readiness gap  
**Status:** next audit/fix slice

The queue data model works. The dashboard API works at the static and isolated-function level. The next hard proof is:

```text
Linear webhook sample
→ durable queue row pending
→ bounded drain operation
→ dispatcher/preflight decision
→ status transition completed/failed/stale
→ dashboard reflects transition
→ recovery/retry path works
```

Until that is proven, we should say “durable ingestion queue contract is implemented,” not “the whole ingestion dispatch system is production-ready.”

### Finding 5 — AGY redispatch safety is still a separate gate

**Severity:** P0 for resuming AGY bulk work  
**Status:** pending

The previous AGY failure was a dispatcher model/preflight/staging failure. Fred's ingestion queue work helps the dashboard and queue layer, but it does not automatically prove AGY can be safely redispatched.

Before AGY resumes:

- model alias must be valid,
- preflight must run before batch launch,
- dependency-staged tasks must not all launch at once,
- one-task proof on [GRO-3837](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3837) must pass with nonzero input/output tokens.

## Recommended next steps for Fred

### Step 1 — Run the full contract verifier in a real project environment

```bash
python3 scripts/verify-governance-dashboard-contract.py
```

Expected outcome:

```text
GOVERNANCE_DASHBOARD_CONTRACT_OK
```

If dependencies are missing, create/use the project venv first; do not mark this failed until dependencies are installed.

### Step 2 — Add/confirm an end-to-end queue drain smoke

Create a focused smoke that proves:

1. temp `PRISMATIC_STATE_DIR`,
2. enqueue Linear-like webhook event,
3. call bounded drain path,
4. assert status transition,
5. assert dashboard queue and stats reflect final state,
6. assert retry returns terminal/failed/stale row to `pending`,
7. assert purge only deletes terminal rows.

Suggested marker:

```text
INGESTION_QUEUE_DRAIN_SMOKE_OK
```

### Step 3 — Make dashboard operator semantics explicit

In the Ingestion Queue tab:

- label retry as `Reset to pending`, not “run now,” unless it truly dispatches immediately;
- label purge as `Purge terminal rows`;
- show source: `linear_webhook_queue.db`;
- show DB missing state with an operator-safe message;
- show stale/dead-letter counts separately from pending/processing/completed/failed.

### Step 4 — Keep the queue contract linked to AGY dispatch recovery

After queue smoke passes, run the dispatch recovery plan from:

- [`docs/agy-dispatch-recovery-audit-and-redispatch-plan-2026-07-14.md`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/docs/agy-dispatch-recovery-audit-and-redispatch-plan-2026-07-14.md)

Specifically:

```text
fix AGY model/preflight
→ reset abandoned Linear tasks safely
→ run one-task AGY proof only
→ then continue Stage 1
```

### Step 5 — Preserve the dashboard/main-proof integration plan

Keep using the preservation report:

- [`docs/fred-dashboard-integration-preservation-report-2026-07-14.md`](https://prismatic.growthwebdev.com/workspace-tree?file=prismatic-engine/docs/fred-dashboard-integration-preservation-report-2026-07-14.md)

Do not let the ingestion queue work cause a branch reset that drops the main-only public/plugin/release proof layer.

## Readiness scorecard

| Gate | Score now | Target | Notes |
|---|---:|---:|---|
| G1 Durable queue DB/schema | 9 | 10 | Good schema/migration path; add schema/version doc if not already present. |
| G2 Webhook enqueue | 9 | 10 | Linear webhook enqueues durable rows; needs live webhook proof output. |
| G3 Dashboard queue API | 9 | 10 | Durable queue and stats APIs exist with gateway aliases. |
| G4 Dashboard UI wiring | 8 | 10 | Markers and JS compile; needs browser/TestClient proof. |
| G5 Retry/purge controls | 8 | 10 | Real mutations exist; add stronger UI confirmation/semantics. |
| G6 Drain/dispatcher E2E | 6 | 10 | Drainer exists; needs end-to-end queue → drain → dispatch status proof. |
| G7 Recovery/dead-letter visibility | 7 | 10 | Recovery status exists; dead-letter/stale visibility should be explicit. |
| G8 AGY redispatch safety | 4 | 10 | Still blocked on model/preflight/one-task proof. |
| G9 Verification suite | 6 | 10 | Static/ad-hoc checks pass; FastAPI verifier blocked by missing deps here. |
| G10 Operator readiness | 7 | 10 | Good direction; needs readiness strip and exact next-action labels. |

## Best current statement

The honest current statement is:

```text
INGESTION_QUEUE_DURABLE_CONTRACT_OK — core durable queue payload/stats/retry/purge behavior verified ad hoc; full dashboard contract and queue-drain-dispatch proof still required before DASHBOARD_DISPATCH_INGESTION_READY_OK.
```

## Final recommendation

Do not rewrite Fred's ingestion queue work. Preserve it.

The next optimal slice is not another broad dashboard rewrite. It is a narrow proof slice:

```text
INGESTION_QUEUE_DRAIN_SMOKE_OK
```

Once that passes, connect it to the AGY dispatch recovery plan and run exactly one AGY task, [GRO-3837](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3837), before releasing any staged batch.
