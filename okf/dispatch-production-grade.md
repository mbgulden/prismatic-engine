---
type: Standard
title: Production-Grade Dispatch Standard
description: Canonical requirements for any code path that dispatches to Linear, GitHub, or any external system via webhook. Defines mandatory security layers, observability requirements, and the dispatch gate stack. Read this BEFORE adding a new dispatch path.
resource: okf/standards/dispatch-production-grade.md
tags: [standard, dispatch, security, gates, telemetry, webhook, prismatic-engine]
timestamp: 2026-06-19T19:45:00Z
linear_issue: GRO-2057
---

# Production-Grade Dispatch Standard

This is the canonical standard for **any code path that dispatches to an external system** (Linear, GitHub, etc.) via webhook or polling. **Read this BEFORE adding a new dispatch path.** For the chronological narrative of how this standard came to be, see `okf/projects/prismatic-engine/tier-7-journey.md`.

## Scope

Applies to:
- Webhook handlers (Linear, GitHub, future integrations)
- Single-issue dispatch helpers (e.g. `dispatch_issue_by_identifier`)
- Batch dispatchers (e.g. `dispatch_once`)
- Cron-driven dispatch scripts (legacy)

Does NOT apply to:
- Read-only API queries (no gate stack required)
- Internal command routing (no external API calls)

## The 7-layer security stack

Every production-grade dispatch MUST implement these 7 layers. Each layer has a corresponding test.

### Layer 1: Authentication

| Endpoint type | Method | Constant-time? |
|---|---|---|
| Linear webhook | HMAC-SHA256 via `Linear-Signature` header | ✅ `hmac.compare_digest` |
| GitHub webhook | HMAC-SHA256 via `X-Hub-Signature-256` header | ✅ `hmac.compare_digest` |
| WebSocket | Bearer token OR HMAC | ✅ both |
| Internal HTTP | IP allowlist (default localhost) | N/A |
| Cron-driven | Service identity (env var) | N/A |

**Dual-secret rotation:** All HMAC-validated endpoints MUST accept both `PRISMATIC_*_SECRET` and `PRISMATIC_*_SECRET_NEXT` simultaneously during rotation. Primary secret works always; next secret works only during the rotation window.

### Layer 2: Replay protection

Linear webhook events have a `createdAt` timestamp. Reject:
- Events older than `WEBHOOK_REPLAY_WINDOW_SECONDS` (default 300 = 5min)
- Events more than `WEBHOOK_CLOCK_SKEW_SECONDS` (default 60) in the future

Return 401 with sanitized reason. Never 500.

### Layer 3: Body size limit (DoS)

Reject any request with body > `PRISMATIC_MAX_BODY_BYTES` (default 1MB). Also reject:
- `Transfer-Encoding: chunked` → 411 (Length Required)
- Missing `Content-Length` → 413 (Payload Too Large)

Both prevent chunked-encoding bypass attacks.

### Layer 4: Per-IP rate limit (flood)

Sliding window: `PRISMATIC_RATE_LIMIT_MAX` requests / `PRISMATIC_RATE_LIMIT_WINDOW_SECONDS` seconds / IP (defaults 60/60).

On limit hit:
- Return 429
- Include `Retry-After: <seconds>` header
- Log to audit

### Layer 5: IP allowlist (defense in depth)

For non-webhook endpoints, require the client IP to be in `PRISMATIC_ALLOWED_IPS` (default localhost + testclient).

Behind a reverse proxy, set `PRISMATIC_TRUSTED_PROXIES` to the proxy's IP(s). The middleware then respects `X-Forwarded-For` (leftmost IP) **only when** the immediate client is in the trusted-proxy set. Prevents header spoofing.

### Layer 6: Audit logging

Every dispatch outcome MUST be appended to `$PRISMATIC_STATE_DIR/webhook_audit.log` as JSONL (one JSON record per line).

Required fields:
```json
{
  "ts": 1718794400.123,
  "request_id": "uuid4-or-caller-provided",
  "source": "linear|github|cron|internal",
  "outcome": "dispatched|queued|rejected|blocked|skipped",
  "identifier": "GRO-2051",
  "event_type": "Issue|Comment|...",
  "reason": "human-readable explanation"
}
```

`reason` MUST be sanitized (no stack traces, no paths, no secrets).

### Layer 7: Sanitized error responses

Exception handler returns `{status, reason}` JSON. Never 500 with stack trace. Never include filesystem paths or internal info.

Reuse the existing handler in `prismatic/gateway/server.py` or define equivalent.

## The 7-gate dispatch stack

For any code path that launches an agent (whether webhook-driven or cron-driven), apply these 7 gates in order. Each gate is independent and can be skipped if its data isn't relevant.

| # | Gate | Function | Failure mode |
|---|---|---|---|
| 1 | Dedup | `dedup.is_processed(issue_id, label, cycle_id)` | Return False (skip) |
| 2 | AGY stall tracker | `SELECT escalated FROM agy_stall_tracker WHERE issue_id=?` | Return False if agent_name=="agy" and escalated |
| 3 | Mode switch | `evaluate_transition_approval(issue_id, from_state, to_state, is_escalation, reason)` | Return False if paused |
| 4 | Credit policy | `evaluate_agent_launch(label, issue_id, operation)` | DENY → block + comment; ASK_USER → skip + mark processed; WARN → log + continue |
| 5 | Telemetry | `collector.record_credit(run_id, agent, ...)` | Best-effort, never blocks |
| 6 | Launch | `AGENT_LAUNCHERS[agent_name](issue_id, title=...)` | If returns False, return False |
| 7 | Post-launch observability | `collector.record_agent_run(...)`, `_emit_agent_event("agent_launched", ...)`, `add_comment(...)` | Best-effort, never blocks |

**Critical:** ALL 7 gates MUST be applied. Skipping any one is a bug. AGY peer review (GRO-2078) caught a single-issue dispatch path that was missing gates 2-7; the fix was to apply every gate.

## Configuration

Required environment variables (with defaults shown):

```bash
# Layer 1: HMAC
PRISMATIC_LINEAR_WEBHOOK_SECRET=                  # 32+ byte random; required in production
PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT=             # Optional, for rotation
PRISMATIC_GITHUB_WEBHOOK_SECRET=                  # 32+ byte random; required in production
PRISMATIC_GITHUB_WEBHOOK_SECRET_NEXT=             # Optional, for rotation
PRISMATIC_WS_TOKEN=                               # WebSocket bearer; or...
PRISMATIC_WS_SECRET=                              # ...WebSocket HMAC secret

# Layer 2: Replay
WEBHOOK_REPLAY_WINDOW_SECONDS=300                 # 5 min default
WEBHOOK_CLOCK_SKEW_SECONDS=60                     # 1 min default

# Layer 3: Body size
PRISMATIC_MAX_BODY_BYTES=1048576                  # 1 MB default

# Layer 4: Rate limit
PRISMATIC_RATE_LIMIT_MAX=60                       # 60 requests
PRISMATIC_RATE_LIMIT_WINDOW_SECONDS=60            # per 60 seconds

# Layer 5: IP allowlist
PRISMATIC_ALLOWED_IPS=127.0.0.1,::1,testclient    # Default
PRISMATIC_TRUSTED_PROXIES=127.0.0.1,::1           # IPs allowed to set X-Forwarded-For

# Layer 6: Audit
PRISMATIC_STATE_DIR=/var/lib/prismatic            # Where audit log lives

# WebSocket (Layer 1 alternative)
PRISMATIC_WS_ALLOWED_ORIGINS=https://app.example.com,https://other.example.com
PRISMATIC_WS_REPLAY_WINDOW=60                     # Numeric, not "sixty"
```

**All `PRISMATIC_*` env vars must be numeric where the type is int.** A bad value should fall back to default with a warning log, never crash.

## Adoption checklist

Before deploying a webhook handler to production:

- [ ] `PRISMATIC_LINEAR_WEBHOOK_SECRET` set to 32+ byte random secret
- [ ] `PRISMATIC_GITHUB_WEBHOOK_SECRET` set to 32+ byte random secret
- [ ] Linear webhook configured to use the secret
- [ ] GitHub webhook configured to use the secret
- [ ] `PRISMATIC_ALLOWED_IPS` includes only trusted operator IPs (not `*`)
- [ ] `PRISMATIC_TRUSTED_PROXIES` configured if running behind reverse proxy
- [ ] `PRISMATIC_MAX_BODY_BYTES` left at 1MB unless specific reason to change
- [ ] `PRISMATIC_CORS_ORIGINS` set only if browser dashboard needs access
- [ ] `PRISMATIC_WS_TOKEN` set to a strong bearer token (or `PRISMATIC_WS_SECRET` for HMAC)
- [ ] `PRISMATIC_WS_ALLOWED_ORIGINS` configured to explicitly allow trusted web dashboard origins
- [ ] `PRISMATIC_WS_REPLAY_WINDOW` left at 60 unless specific reason to change (must be numeric)
- [ ] `PRISMATIC_RATE_LIMIT_MAX` left at 60 unless specific reason to change
- [ ] Audit log directory (default `./prismatic_state/`) on a persistent volume
- [ ] Alert wired for `webhook_audit.log` entries with outcome=rejected (suspicious traffic)
- [ ] All 7 dispatch gates applied (verify with AGY peer review)
- [ ] Tests cover all 7 layers (≥30 tests minimum)

## Adoption verification

To verify a new dispatch path meets this standard:

1. **Run the test suite.** Every security layer should have ≥1 test. The full suite should be ≥90% passing in isolation AND in full-suite order.
2. **Review with AGY peer review.** File a GRO issue labeled `agent:agy` with the change request. AGY's verdict should be `APPROVED` or have actionable `NEEDS_CHANGES`.
3. **Apply every gate.** Read `prismatic/dispatcher.py::dispatch_once` to see the canonical gate stack. Match it.
4. **Update OKF.** Add a doc to `okf/standards/` or `okf/integrations/` describing the new path. Reference this standard.

## Anti-patterns (don't do this)

❌ **Adding a new dispatch path without checking `dispatch_once` for the gate stack.** AGY caught this once. Don't repeat.

❌ **Using `sys.modules[...] = MagicMock()` to mock imports in tests.** Causes cross-test pollution. Use `patch.object` inside each test.

❌ **Catching exceptions too broadly.** `except Exception: pass` hides real bugs. Catch specific exceptions; let others propagate.

❌ **Logging raw stack traces.** Use `logger.exception()` only in dev. Production should log sanitized messages.

❌ **Hardcoding secrets or paths.** Use environment variables with sensible defaults.

❌ **Adding a cron job without considering event-driven alternatives.** Every cron is a budget drain. If a webhook can do it, do that.

❌ **Skipping audit logging for "internal" calls.** Audit everything that touches external systems.

❌ **Returning 500 with stack trace on validation errors.** Catch and return 4xx with sanitized reason.

## Why this standard exists

The Tier 7 journey (May-June 2026) found that:

1. **A 5-minute polling cron was burning ~5,760 Linear API calls per day.** Event-driven dispatch cut this by 99.6%.

2. **Five cron scripts were silently erroring** because of a stale `from prismatic.linear.budget import linear_call` import. The `linear_api_compat.py` shim fixed all five.

3. **Two `github_webhook` functions existed in the same file** due to copy-paste edits. The second one captured `/api/gateway/linear` decorator, silently misrouting all Linear webhooks. Linear dispatch from webhooks had never worked.

4. **The single-issue dispatch path (`dispatch_issue_by_identifier`) bypassed 5 of 7 gates** that the full cycle applies. AGY peer review (GRO-2078) caught this — and it was a real production-blocker.

5. **Body size limit could be bypassed via chunked Transfer-Encoding.** A textbook DoS vector.

6. **IP allowlist didn't respect X-Forwarded-For** behind reverse proxies.

Every finding is now codified in this standard. New dispatch paths that don't meet these requirements will fail AGY peer review.

## References

- `okf/standards/webhook-security.md` — Detailed webhook security layer reference
- `okf/standards/dispatch-architecture.md` — Webhook-first architecture
- `okf/standards/agy-peer-review.md` — Peer review standard
- `okf/integrations/webhook-handler-test-pattern.md` — Test patterns
- `okf/projects/prismatic-engine/tier-7-journey.md` — Chronological narrative
- `prismatic/dispatcher.py::dispatch_once` — Canonical gate stack implementation
- `prismatic/gateway/server.py::linear_webhook` — Canonical webhook handler
