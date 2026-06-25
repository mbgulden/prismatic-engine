---
type: Runbook
title: Prismatic Engine — Where Everything Is + How To Do Tasks
description: Single entry point for everything related to the Prismatic Engine. Where files live, how to find things, how to do common tasks. Read this first when picking up the system.
resource: okf/runbooks/prismatic-engine-tasks.md
tags: [runbook, prismatic-engine, system-map, onboarding, tasks]
timestamp: 2026-06-19T23:00:00Z
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Prismatic Engine — Where Everything Is + How To Do Tasks

This is the **single entry point** for working with the Prismatic Engine. If you don't know where something is or how to do a task, start here.

## TL;DR

- **Hub docs**: `mbgulden/growthwebdev-knowledge/okf/` — canonical, source of truth
- **Spoke docs**: `mbgulden/prismatic-engine/okf/` — mirrors of hub, project-specific only
- **Code**: `/home/ubuntu/work/prismatic-engine/prismatic/` (engine) + `/home/ubuntu/.hermes/profiles/orchestrator/` (cron scripts)
- **Live gateway**: `/etc/systemd/system/prismatic-gateway.service` → port 9000
- **Public URL**: `https://webhooks.growthwebdev.com/webhooks/linear`
- **Cloudflare Tunnel**: "Growth Web v2" → tunnel `abe7bbd9-ff25-4c1e-be14-3efc5ea27bce`

## OKF doc map (read in this order)

1. **[`tier-7-journey.md`](../projects/prismatic-engine/tier-7-journey.md)** — chronological narrative of the most recent production-grade work. **Read this first.**
2. **[`dispatch-production-grade.md`](../standards/dispatch-production-grade.md)** — mandatory 7-layer security + 7-gate dispatch. **Follow this when adding any new dispatch path.**
3. **[`agy-activation-investigation.md`](../projects/prismatic-engine/agy-activation-investigation.md)** — why AGY isn't running, the nudge-based IPC chain, activation sequence. **Read this to understand the agent layer.**
4. **[`tier-7-architecture.md`](../projects/prismatic-engine/tier-7-architecture.md)** — system diagram + data flows + file map + state DB layout.
5. **[`webhook-security.md`](../standards/webhook-security.md)** — detailed webhook layer reference (HMAC, replay, body size, rate limit, IP allowlist, audit, sanitized errors).
6. **[`linear-webhook-events.md`](../integrations/linear-webhook-events.md)** — what Linear events to subscribe to and how each is processed.
7. **[`cloudflare-tunnel-webhooks.md`](../integrations/cloudflare-tunnel-webhooks.md)** — how the tunnel routes + how to update.
8. **[`dispatch-architecture.md`](../standards/dispatch-architecture.md)** — webhook-first architecture decision rationale.
9. **[`agy-peer-review.md`](../standards/agy-peer-review.md)** — peer review standard + review-loop codification.
10. **[`review-loop-canonical.md`](../standards/review-loop-canonical.md)** — Worker → AGY → Fred → Done loop.

For everything **not** on this list, it's either project-specific (in the spoke `okf/` directly), an audit (in `okf/audits/`), or a report (in `okf/reports/`).

## Code map

| What | Where | What it does |
|---|---|---|
| Engine code | `/home/ubuntu/work/prismatic-engine/prismatic/` | The Prismatic Engine package |
| `prismatic/dispatcher.py` | `prismatic/dispatcher.py` | Linear queries, dispatch gate stack, agent launchers |
| `prismatic/gateway/server.py` | `prismatic/gateway/server.py` | FastAPI app, webhook handler, middleware |
| `prismatic/providers/signals/` | `prismatic/providers/signals/` | Signal delivery (file, http, redis providers) |
| `prismatic/linear/budget.py` | `prismatic/linear/budget.py` | LinearBudget token bucket per agent |
| `prismatic/credit_policy_engine.py` | `prismatic/credit_policy_engine.py` | PolicyAction enum + evaluate_agent_launch |
| `prismatic/mode_switch.py` | `prismatic/mode_switch.py` | Orchestration mode + transition approval |
| Cron scripts | `/home/ubuntu/.hermes/profiles/orchestrator/scripts/` | Daily/hourly jobs |
| `agent_dispatcher.py` | `~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py` | Cron-driven full-cycle dispatch (now daily safety net) |
| `linear_oauth_refresh.py` | `~/.hermes/profiles/orchestrator/scripts/linear_oauth_refresh.py` | Mints fresh OAuth token every 45 min |
| `linear_api_compat.py` | `~/.hermes/profiles/orchestrator/scripts/linear_api_compat.py` | Compatibility shim for legacy cron scripts |
| `refresh_gateway_oauth_env.sh` | `~/.hermes/profiles/orchestrator/scripts/refresh_gateway_oauth_env.sh` | Rebuilds gateway env file after OAuth refresh |
| `agy_sandbox_event_supervisor_cron.sh` | `~/.hermes/profiles/orchestrator/scripts/` | **PAUSED** — spawns AGY in tmux when nudges appear |
| `nudge_detector.py` | `~/.hermes/profiles/orchestrator/scripts/nudge_detector.py` | Polls every 1 min for nudge files |
| `nudge_executor.py` | `~/.hermes/profiles/orchestrator/scripts/nudge_executor.py` | Executes nudges → calls launch_agy_with_artifact.py |
| `launch_agy_with_artifact.py` | `~/.hermes/profiles/orchestrator/scripts/` | PTY + tmux wrapper that handles AGY's `/tmp` write restriction |
| Tests | `/home/ubuntu/work/prismatic-engine/tests/` | pytest suite (254 passing, 1 flaky, 0 failing) |

## State DB layout

`/home/ubuntu/work/prismatic-engine/prismatic_state/`:

```
event_router.db          — dedup + stall tracker (SQLite)
  ├─ dedup_log           (issue_id, agent_label, cycle_id, processed_at)
  ├─ agy_stall_tracker   (issue_id, cycle_count, escalated)
  └─ telemetry_*         (events, runs, credit ledger, etc.)

webhook_audit.log        — JSONL append-only (every webhook outcome)

linear_webhook_queue.db  — fallback queue for non-dispatchable events
  └─ linear_webhook_queue (event_id, identifier, event_type, action, raw_json)

linear_budget.db         — LinearBudget state
  └─ budget_state, budget_logs

env.d/linear_oauth.env   — auto-generated OAuth token for systemd EnvironmentFile
ipc_bridge.sock          — Unix socket for local agent-to-gateway IPC
```

## Systemd services

| Service | Port | Role | Health |
|---|---|---|---|
| `prismatic-gateway.service` | 9000 | FastAPI webhook + dispatch + WebSocket + IPC | ✅ Active, Tier 7 code live |
| `cloudflared.service` | — | Cloudflare Tunnel daemon | ✅ Active, config points to :9000 for webhooks |
| `hermes-orchestrator-gateway.service` | 9119 | Hermes orchestrator (different process) | ⚠️ Active but unhealthy (port 9119 hangs) |
| `cloudflared-hde.service` | — | Cloudflare Tunnel for humandesignengine.com | ✅ Active (separate domain) |

## Cloudflare Tunnel

Tunnel: **"Growth Web v2"** (id: `abe7bbd9-ff25-4c1e-be14-3efc5ea27bce`, status: healthy)

Current ingress:
- `sentinel.growthwebdev.com` → k8s sentinel-backend
- `hermes.growthwebdev.com` → `http://127.0.0.1:9119`
- `code.growthwebdev.com` → `http://127.0.0.1:8080`
- `beyondsaas.ai`, `www.beyondsaas.ai` → `http://127.0.0.1:8090`
- **`webhooks.growthwebdev.com` → `http://127.0.0.1:9000`** (Prismatic Engine)
- `*` → `http_status:404`

To update: edit via Cloudflare API or dashboard (see `cloudflare-tunnel-webhooks.md`).

## Secrets — where they live

| Secret | Stored in | Updated by |
|---|---|---|
| `LINEAR_API_KEY` | `~/.hermes/profiles/orchestrator/.env` + systemd unit | Manual rotation |
| `LINEAR_OAUTH_CLIENT_ID` / `_SECRET` | `~/.hermes/profiles/orchestrator/.env` | Manual (used to mint OAuth tokens) |
| `LINEAR_OAUTH_TOKEN` | `~/.hermes/profiles/orchestrator/credentials.json` + `~/.hermes/.env` | `linear_oauth_refresh.py` cron every 45min |
| `LINEAR_WEBHOOK_SIGNING_SECRET` | `~/.hermes/.env` + `~/.hermes/profiles/orchestrator/.env` + systemd unit | Manual rotation (rotate via `webhookUpdate` mutation) |
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` | systemd unit (auto-rebuilt by `refresh_gateway_oauth_env.sh`) | Mirror of `LINEAR_WEBHOOK_SIGNING_SECRET` |
| `PRISMATIC_GITHUB_WEBHOOK_SECRET` | systemd unit (32-byte random, generated once) | Manual rotation |

**Secret rotation procedure:**
1. For Linear webhook signing secret: GraphQL `webhookUpdate` mutation with new secret
2. Update systemd unit `PRISMATIC_LINEAR_WEBHOOK_SECRET=...`
3. Update `~/.hermes/.env` and `~/.hermes/profiles/orchestrator/.env` `LINEAR_WEBHOOK_SIGNING_SECRET=...`
4. `sudo systemctl daemon-reload && sudo systemctl restart prismatic-gateway.service`
5. Verify with end-to-end signed test

## Common tasks

### Task: verify the system is healthy

```bash
# 1. Gateway alive?
curl -sS --max-time 5 -o /dev/null -w 'gateway /health: HTTP=%{http_code}\n' http://localhost:9000/health

# 2. Tunnel routing correctly?
curl -sS --max-time 5 -X POST https://webhooks.growthwebdev.com/webhooks/linear \
  -H 'Content-Type: application/json' -d '{}' \
  -w '\ntunnel -> /webhooks/linear: HTTP=%{http_code}\n'
# Expected: 401 (HMAC required)

# 3. Recent dispatch activity?
tail -5 /home/ubuntu/work/prismatic-engine/prismatic_state/webhook_audit.log

# 4. AGY running?
ps -ef | grep -E '[a]gy' | head
```

### Task: deploy new code

```bash
cd /home/ubuntu/work/prismatic-engine
git pull
# Editable install — code is already live
sudo systemctl restart prismatic-gateway.service
sleep 3
PYTHONPATH=. python3 -m pytest tests/  # verify all green
```

### Task: rotate the Linear webhook signing secret

See **Secret rotation procedure** above. **Never paste secrets in chat — rotate immediately if they appear.**

### Task: re-enable AGY (currently paused)

See `agy-activation-investigation.md` "Activation sequence" section. Step-by-step commands are there.

### Task: add a new dispatch path

1. Read `dispatch-production-grade.md` — apply the 7-layer security + 7-gate dispatch
2. Update `dispatcher.py` with the new helper
3. Add tests in `tests/` (run `pytest tests/` to verify)
4. Update `webhook-security.md` if it changes security posture
5. Open a Linear issue labeled `agent:fred` for documentation review

### Task: debug a webhook that's not firing

```bash
# 1. Is the webhook registered at Linear?
python3 -c "
import json, urllib.request, pathlib
ENV = pathlib.Path('/home/ubuntu/.hermes/profiles/orchestrator/.env').read_text()
TOKEN = [l.split('=',1)[1].strip().strip('\"').strip(\"'\") for l in ENV.splitlines() if l.strip().startswith('LINEAR_API_KEY=')][0]
q = 'query{ webhooks(first:5){ nodes{ id label url enabled } } }'
req = urllib.request.Request('https://api.linear.app/graphql', data=json.dumps({'query':q}).encode(), headers={'Authorization':TOKEN,'Content-Type':'application/json'})
import urllib.request as r; print(json.loads(r.urlopen(req,timeout=30).read())['data']['webhooks']['nodes'])
"

# 2. Did the event reach our gateway?
tail -20 /home/ubuntu/.prismatic/logs/gateway.log

# 3. Did HMAC validate?
grep '"outcome": "rejected"' /home/ubuntu/work/prismatic-engine/prismatic_state/webhook_audit.log | tail

# 4. Did dispatch fire?
grep '"outcome": "dispatched"' /home/ubuntu/work/prismatic-engine/prismatic_state/webhook_audit.log | tail

# 5. Was AGY started?
ps -ef | grep -E '[a]gy' | head
```

## Current state (as of 2026-06-19 end of session)

### ✅ Working

- Prismatic Engine gateway is **live and processing real Linear webhooks** through the public tunnel
- HMAC validation, replay protection, body size limit, rate limit, IP allowlist, audit log — **all 7 security layers active**
- 46 events recorded in the audit log; 8 dispatched (synthetic tests), 9 queued (real Linear Comments/Labels), 4 dispatch_no_op (real Issues without agent label)
- Cloudflare Tunnel "Growth Web v2" routes `webhooks.growthwebdev.com` to port 9000
- Linear webhook **registered at Linear side** with rotated secret, label "Prismatic Engine Dispatcher"
- OAuth token rotation works (cron every 45min → `linear_oauth_refresh.py` → `refresh_gateway_oauth_env.sh` → systemd EnvironmentFile)
- All 4 cron-driven scripts that were broken (ImportError on `linear_call`) are fixed via `linear_api_compat.py` shim
- Daily cron safety-net sweep runs (`agent_dispatcher.py` daily at 08:00 UTC)
- All 254 tests passing (1 flaky isolation test unrelated to this work)
- Documentation is comprehensive (3 new OKF docs + 1 standard + investigation)

### ⚠️ Needs attention

1. **AGY is not running** — `AGY Sandbox Supervisor` cron was paused 2026-06-18 16:50 UTC. Dispatch is firing but nothing consumes the nudge files. **GRO-2085** tracks this.
2. **3 stale nudge files** accumulating in `/tmp/prismatic/` — `nudge-fred` (21:12), `nudge-kai` (12:25), `nudge-ned` (16:34)
3. **22 webhook events rejected** with "bad signature" — Linear's late retries using the old (now-rotated) signing secret. Will self-resolve over ~24h.

### 📋 Linear issues queued

- **GRO-2077** — Tier 7 webhook security AGY peer review (DONE)
- **GRO-2078** — Tier 7 first-batch AGY peer review verdict (APPLIED)
- **GRO-2079** — Tier 7 second-batch AGY peer review (DONE)
- **GRO-2080** — Tier 7 cron reduction AGY peer review (DONE)
- **GRO-2082** — /ws WebSocket auth AGY peer review (APPLIED)
- **GRO-2083** — Tier 7 OKF docs AGY peer review (PENDING)
- **GRO-2085** — AGY activation investigation (DRAFTED, awaiting action)

## If you (or future-me) get stuck

1. **Re-read the journey doc** — `okf/projects/prismatic-engine/tier-7-journey.md` has the chronological narrative
2. **Check the audit log** — every webhook outcome is recorded with request-ID for correlation
3. **Check the gateway log** — `/home/ubuntu/.prismatic/logs/gateway.log` has full request traces
4. **Check the dispatcher's dedup DB** — `sqlite3 prismatic_state/event_router.db "SELECT * FROM dedup_log ORDER BY processed_at DESC LIMIT 10"`
5. **AGY questions?** → `agy-activation-investigation.md`
6. **Webhook questions?** → `webhook-security.md` + `linear-webhook-events.md`
7. **Stuck on what to add?** → `dispatch-production-grade.md`

## Quick reference: file → URL → port

```
Linear (webhook source)
  ↓ POST https://webhooks.growthwebdev.com/webhooks/linear
Cloudflare Tunnel "Growth Web v2"
  ↓ http://127.0.0.1:9000
prismatic-gateway.service (FastAPI)
  ↓ dispatch_issue_by_identifier(identifier)
prismatic/dispatcher.py
  ↓ signal_fred(issue_id, title, priority)
prismatic/providers/signals/file.py
  ↓ writes /tmp/prismatic/nudge-fred
[1 min cron] nudge_detector.py
  ↓ detects nudge
[5 min cron] nudge_executor.py (currently PAUSED via supervisor)
  ↓ would call launch_agy_with_artifact.py
AGY in tmux session → executes task → writes result.md
  ↓ ack nudge, transition issue
Done.
```
