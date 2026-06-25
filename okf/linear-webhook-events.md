---
type: Integration
title: Linear Webhook Events — Required Subscriptions
description: What Linear events to subscribe to when configuring a Linear webhook for the Prismatic Engine dispatcher. Maps each event to the gateway's behavior (dispatch, queue, audit only).
resource: okf/integrations/linear-webhook-events.md
tags: [integration, linear, webhook, events, configuration, prismatic-engine]
timestamp: 2026-06-19T21:00:00Z
linear_issue: GRO-2084
---

# Linear Webhook Events — Required Subscriptions

This doc tells you exactly what to set when configuring a Linear webhook for the Prismatic Engine dispatcher. The configuration lives at Linear's settings UI (Settings → API → Webhooks).

## Quick reference

| Linear setting | Required value |
|---|---|
| **URL** | `https://webhooks.growthwebdev.com/webhooks/linear` |
| **Resource types** | `Issues`, `Issue labels`, `Comments`, `Projects` (recommended) |
| **Event types** | `create`, `update`, `remove` (covers most workflows) |
| **Signing secret** | The value of `LINEAR_WEBHOOK_SIGNING_SECRET` from `~/.hermes/.env` |
| **Enabled** | `true` |

The current gateway is configured at `https://webhooks.growthwebdev.com/webhooks/linear` via a Cloudflare Tunnel routing to port 9000 (Prismatic Engine gateway).

## How the handler processes each event

The handler reads `event.type` and `event.action` from the payload:

| Event type | Action | What the handler does |
|---|---|---|
| `Issue` | `create` | Dispatches via `dispatch_issue_by_identifier` if `agent:*` label present; else queues to SQLite |
| `Issue` | `update` | Same as create — dispatches or queues based on labels |
| `Issue` | `remove` | Queues only (deletes don't have labels to inspect) |
| `Comment` | any | Queues (we don't dispatch on comments yet) |
| `IssueLabel` | `create`/`update`/`remove` | Queues (label-only events often come without `agent:*` label) |
| `Project` | any | Queues |
| Anything else | any | Queues + audit |

**Audit log records every event** (rejected, queued, dispatched) in `$PRISMATIC_STATE_DIR/webhook_audit.log` with request-ID correlation.

## Why subscribe to more than just `Issue.create`?

The default Linear webhook config fires on Issue **create only**. That's too narrow for our dispatcher:

- **`update`** is needed because we label issues with `agent:fred` / `agent:agy` after creation. Without `update`, the dispatcher never sees the agent-label change that triggers dispatch.
- **Comments** matter if you want the dispatcher to react to user replies or `@-mentions` on issues.
- **Label events** matter if you want to track label changes for routing decisions.

If you only want minimal setup, `Issue.create` + `Issue.update` covers ~90% of the value. Everything else is incremental.

## What the dispatcher checks before launching an agent

When an `Issue` event arrives with an `agent:*` label, the handler calls `dispatch_issue_by_identifier(identifier)` which applies **7 gates** in order:

1. **Dedup check** — has this issue been dispatched within the TTL window? (24h default)
2. **AGY stall/escalation tracker** — is this issue escalated? If so, skip.
3. **Mode switch** — is transitions paused? If so, skip.
4. **Credit policy** — DENY → block + comment. ASK_USER → skip + mark processed. WARN → log + continue.
5. **Telemetry** — record credit evaluation.
6. **Launch** — call `AGENT_LAUNCHERS[agent_name](issue_id, title)`.
7. **Post-launch observability** — record agent run, emit IPC event, post Linear comment.

Any gate failure returns False; the webhook returns `200 {"status":"dispatch_no_op"}` to Linear (so Linear doesn't retry). The audit log records the gate decision.

## What events will fail silently

If you subscribe to events the handler doesn't process (e.g. `Cycle`, `Initiative`, `Document`), the handler queues them to SQLite but nothing else happens. **They're not lost** — they go to `$PRISMATIC_STATE_DIR/linear_webhook_queue.db`. The daily cron sweep (`agent_dispatcher.py` at 08:00 UTC) re-checks any queued events.

If you want to act on queued events before the daily sweep, run:

```bash
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py
```

This is the safety-net sweep — it re-checks Linear for any issues that should have been dispatched and were missed.

## What happens on HMAC failure

If the signature doesn't validate:

```
HTTP/1.1 401 Unauthorized
{"status":"rejected","reason":"invalid signature"}
```

Linear does NOT retry on 401 — the event is dropped. This is intentional: a bad signature means the request didn't come from Linear, so retrying would just be wasted effort. The audit log records every rejected request.

If the signature is missing:

```
HTTP/1.1 401 Unauthorized
{"status":"rejected","reason":"missing Linear-Signature header"}
```

If the body is too large (>1MB) or uses chunked Transfer-Encoding:

```
HTTP/1.1 413 Payload Too Large
{"status":"rejected","reason":"payload too large or unknown size"}

HTTP/1.1 411 Length Required
{"status":"rejected","reason":"chunked transfer not allowed"}
```

## What events trigger Linear API calls (and count toward rate limit)

The dispatcher path is **event-driven and minimal**: 1-2 Linear API calls per webhook event:

1. `get_issue_by_identifier(identifier)` — 1 call to fetch the issue
2. Maybe `add_comment(...)` after dispatch — 1 call (only on success)

For comparison, the old 5-min cron path was ~20 calls per tick = ~5,760 calls/day. The webhook path is **~1-2 calls per event** which is much cheaper.

## Recommended Linear webhook configuration (full setup)

```
URL:                  https://webhooks.growthwebdev.com/webhooks/linear
Label:                "Prismatic Engine dispatcher"
Resource types:       Issues, Issue labels, Comments, Projects
Event types:          create, update, remove
Signing secret:       (from LINEAR_WEBHOOK_SIGNING_SECRET in ~/.hermes/.env)
Enabled:              true
```

You can add/remove event types later without re-deploying. The handler treats unknown events as queue-only.

## How to verify it's working

After configuring, fire a test event from Linear's webhook UI:

1. Go to Settings → API → Webhooks → click your webhook → "Send test event"
2. Pick an event type (Issue.create is recommended)
3. Click "Send"
4. Within ~1 second, check the audit log:
   ```bash
   tail -5 /home/ubuntu/work/prismatic-engine/prismatic_state/webhook_audit.log
   ```
5. You should see a JSONL line with `outcome: "queued"` or `outcome: "dispatched"` depending on whether the test issue had an `agent:*` label.

## Why this is split from the rest of the engine

Linear webhook subscriptions are **operator-controlled** (they live in Linear's settings UI, not in code). The dispatcher code doesn't change when you add/remove events — it just routes each event to the right handler. So this doc is the source of truth for the recommended configuration.

If you want to add a new event type without re-deploying, edit your webhook in Linear's UI. If you want to add new behavior in the handler for an event type, edit `prismatic/gateway/server.py::linear_webhook` and add a new branch.

## References

- Linear webhook docs: https://developers.linear.app/docs/graphql/webhooks
- Prismatic Engine webhook handler: `prismatic/gateway/server.py::linear_webhook`
- Path-aliases: `/webhooks/linear` and `/webhooks/github` (this session's addition)
- Webhook security standard: `okf/standards/webhook-security.md`
- Audit log format: `okf/integrations/webhook-handler-test-pattern.md`
