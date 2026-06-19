---
type: Standard
title: Webhook Security Model
description: Production-grade security model for the Prismatic Engine webhook receivers (Linear, GitHub). Covers HMAC, body size, IP allowlist, audit logging, and sanitized errors.
resource: okf/standards/webhook-security.md
tags: [standard, security, webhook, hmac, linear, github, audit, prismatic-engine]
timestamp: 2026-06-19T13:30:00Z
linear_issue: GRO-2057
git_repo: mbgulden/prismatic-engine
git_path: prismatic/gateway/server.py
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Webhook Security Model

**Status:** ENFORCED as of Jun 19 2026 (Tier 7 hardening).
**Refs:** GRO-2057, GRO-2058, GRO-2059, GRO-2060, GRO-2061, GRO-2062.

## Threat model

The Prismatic Engine accepts webhook events from two external systems:

- **Linear** (`/api/gateway/linear`) — issue lifecycle events.
- **GitHub** (`/api/gateway/github`) — PR/review events.

The engine also exposes internal HTTP/WebSocket endpoints for the orchestrator profile:
`/locks`, `/runs`, `/schedules`, `/chat/sessions`, `/health`, `/ws`.

Threats addressed:

1. **Forged webhook events** — attacker POSTs to `/api/gateway/linear` without a valid Linear signature, triggering unintended dispatch.
2. **Replay attacks** — attacker captures a valid webhook payload+signature and replays it.
3. **DoS via large payloads** — attacker POSTs 100MB body, exhausting memory.
4. **Unauthorized local access** — process on the host reaches `/locks` to read internal state.
5. **CORS exploitation** — malicious browser origin reads engine state via credentialed requests.
6. **Information disclosure** — error responses leak stack traces, filesystem paths, or secrets.
7. **Forensic blindness** — no audit trail means we can't tell who triggered what.

## Defense layers

### 1. HMAC validation (forgery)

Every webhook request must carry a valid HMAC-SHA256 signature.

| Endpoint | Header | Algorithm | Secret env var |
|---|---|---|---|
| `/api/gateway/linear` | `Linear-Signature` (hex) | HMAC-SHA256 | `PRISMATIC_LINEAR_WEBHOOK_SECRET` |
| `/api/gateway/github` | `X-Hub-Signature-256: sha256=<hex>` | HMAC-SHA256 | `PRISMATIC_GITHUB_WEBHOOK_SECRET` |

Comparison is constant-time via `hmac.compare_digest`.

**Failure mode:** If the secret env var is unset, HMAC is skipped. This is **dev-only**. Production must set the secret.

### 1b. Replay protection (timestamp freshness)

Linear's HMAC signature alone doesn't include a timestamp — so a captured valid payload+signature could be replayed indefinitely. We use the event's `createdAt` field (set by Linear at event-generation time, ISO 8601 UTC) to enforce a freshness window.

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_WEBHOOK_REPLAY_WINDOW` | `300` (5 min) | Max age of an event, in seconds |

| Condition | Behavior |
|---|---|
| `createdAt` within window | 200, processed normally |
| `createdAt > window seconds old` | 401, audited, no dispatch |
| `createdAt > 60s in the future` | 401, audited, no dispatch (clock-skew attack) |
| `createdAt` absent | 200 with warning log (backward compat for old Linear events) |
| `createdAt` unparseable | 200 with warning log |

The 60s future tolerance accommodates clock skew between Linear and the engine. Anything more than 60s in the future is rejected because legitimate events never have negative age.

GitHub webhook does **not** apply replay protection because GitHub includes a timestamp in their HMAC scheme (we'd add it there if needed).

### 2. Body size limit (DoS)

Middleware `limit_body_size` rejects any POST/PUT/PATCH with `content-length > MAX_BODY_BYTES`. Also rejects requests with `Transfer-Encoding: chunked` (no reliable size bound) or missing `Content-Length` header (unknown size).

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_MAX_BODY_BYTES` | `1048576` (1MB) | Cap per-request body size |

Linear events are typically <10KB. 1MB allows batched events but rejects abusive payloads.

**Failure modes (AGY GRO-2078 review):**
- 413 `{"status": "rejected", "reason": "payload too large or unknown size"}` — body declared >1MB or Content-Length missing
- 411 `{"status": "rejected", "reason": "chunked transfer not allowed"}` — chunked Transfer-Encoding (AGY GRO-2078 MEDIUM #1 hardening)

### 2b. Per-IP rate limit (flood protection)

Sliding-window per-IP rate limit at the gateway. Default: 60 requests per 60s per IP.

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_RATE_LIMIT_WINDOW` | `60` (seconds) | Sliding window size |
| `PRISMATIC_RATE_LIMIT_MAX` | `60` (requests) | Max requests per IP per window |

State is in-memory (single-instance). Multi-instance deployments need a shared store (Redis, etc.).

**Failure mode:** 429 `{"status": "rejected", "reason": "rate limit exceeded"}` with `Retry-After` header.

Limits should be tuned per deployment. The defaults (60/60s = ~1 req/s sustained) match expected Linear webhook traffic (Linear typically sends <1 event/second even during heavy activity).

### 3. IP allowlist (internal endpoints)

Middleware `ip_allowlist` restricts non-webhook endpoints to a configurable IP set.

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_ALLOWED_IPS` | `127.0.0.1,::1,testclient` | Comma-separated allowlist |
| `PRISMATIC_TRUSTED_PROXIES` | `127.0.0.1,::1` | IPs allowed to set X-Forwarded-For |

**AGY GRO-2078 LOW #1 hardening:** When the immediate client IP is in `PRISMATIC_TRUSTED_PROXIES`, the middleware respects `X-Forwarded-For` (leftmost IP). This lets the engine sit behind a reverse proxy (Nginx, ALB) without bypassing auth.

Webhook endpoints (`/api/gateway/*`) are exempt — they're HMAC-authenticated. `/health` is intentionally public.

**Failure mode:** 403 `{"status": "rejected", "reason": "ip not allowed"}`.

### 4. CORS policy

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_CORS_ORIGINS` | unset (no middleware) | Comma-separated origins |

When unset, **no CORS middleware is added**. When set, only the listed origins are allowed, with explicit method/header lists. Credentials are restricted to those origins.

**Forbidden by spec:** `allow_origins=["*"]` with `allow_credentials=True`. We never set that combination.

### 5. Single-issue dispatch (amplification limit)

The webhook handler calls `dispatch_issue_by_identifier(identifier)` — a helper that fetches only the one issue that triggered the webhook, applies **all** the same gates as the full cycle, and launches the matching agent. **Cost: 1-2 Linear API calls per webhook event.** Prior implementation called `dispatch_once()` (full cycle) which triggered ~20 API calls per event.

**Gates applied (same as `dispatch_once` cycle, AGY GRO-2078 review):**

1. Dedup check (in-cycle)
2. AGY stall/escalation tracker check (prevents re-launching escalated stalled AGY)
3. Mode-switch transition approval (`evaluate_transition_approval`)
4. Credit-policy gate (DENY → block + comment, WARN → log, ASK_USER → skip + mark processed)
5. Telemetry (`collector.record_credit`, `collector.record_agent_run`)
6. IPC bridge `agent_launched` event
7. Linear dispatch comment

The AGY peer review (GRO-2078) caught that the initial single-issue path was missing gates 2-7; those were added in this update.

### 6. Audit logging

Every webhook outcome is appended to `$PRISMATIC_STATE_DIR/webhook_audit.log` as JSONL (one JSON record per line).

```json
{"ts": 1718794400.123, "source": "linear", "outcome": "dispatched", "identifier": "GRO-2051", "event_type": "Issue", "request_id": "550e8400-e29b-41d4-a716-446655440000"}
{"ts": 1718794401.456, "source": "linear", "outcome": "rejected", "reason": "bad signature", "request_id": "550e8400-e29b-41d4-a716-446655440001"}
{"ts": 1718794402.789, "source": "github", "outcome": "received", "repo": "growthwebdev/prismatic-engine", "event_type": "opened", "request_id": "550e8400-e29b-41d4-a716-446655440002"}
```

Outcomes: `dispatched`, `dispatch_no_op`, `queued`, `rejected`, `received`, `dispatch_failed`, `queue_failed`.

Audit log writes **never** block the webhook path — failures are logged via `logger.warning` but the request continues.

### 6b. Request-ID propagation

Every inbound request gets a `X-Request-ID` header. If the caller provides one, it's echoed back. If absent, a UUID4 is generated. The same `request_id` is included in the audit log entry for that request, so operators can correlate multiple audit entries (HMAC check, dispatch attempt, queue write) from a single inbound webhook call.

The response always carries the `X-Request-ID` header so callers can correlate the response with their own tracing infrastructure.

```text
Inbound:  X-Request-ID: trace-abc-12345  (caller provides)
Outbound: X-Request-ID: trace-abc-12345  (echoed)
Audit:    {"outcome": "dispatched", "request_id": "trace-abc-12345", ...}
```

```text
Inbound:  (no X-Request-ID header)
Outbound: X-Request-ID: 550e8400-e29b-41d4-a716-446655440000  (generated UUID4)
Audit:    {"outcome": "queued", "request_id": "550e8400-e29b-41d4-a716-446655440000", ...}
```

### 7. Sanitized error responses

All error responses return sanitized JSON. **Never** include:
- Stack traces
- Filesystem paths
- Internal exception messages

All exceptions are logged server-side via `logger.exception(...)` so operators can debug, but the response body is constrained.

## Configuration matrix

| Env var | Default | Required in prod? |
|---|---|---|
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` | unset (HMAC skipped) | **YES** — 32+ byte random secret |
| `PRISMATIC_GITHUB_WEBHOOK_SECRET` | unset (HMAC skipped) | **YES** — same as above |
| `PRISMATIC_MAX_BODY_BYTES` | 1048576 | Optional |
| `PRISMATIC_ALLOWED_IPS` | 127.0.0.1,::1,testclient | Optional (defaults to localhost) |
| `PRISMATIC_CORS_ORIGINS` | unset | Only if browser clients need access |

## Endpoint auth catalog

| Endpoint | Auth | Sensitive data | Risk |
|---|---|---|---|
| `/health` | None (public) | Status only | Low |
| `/api/gateway/linear` | HMAC + replay + rate-limit | Triggers dispatch | High (mitigated) |
| `/api/gateway/github` | HMAC + dual-secret | PR/review state | Medium (mitigated) |
| `/locks/*` | IP allowlist | Lock state | Low (internal) |
| `/runs/*` | IP allowlist | Run records | Low (internal) |
| `/schedules/*` | IP allowlist | Schedule state | Low (internal) |
| `/chat/sessions/*` | IP allowlist | Chat history | Low (internal) |
| `/ws` | IP allowlist + Bearer/HMAC + origin allowlist | Event stream | **Closed** (GRO-2058) |

**Open gap (Tier 7 follow-up, GRO-2058):** ~~`/ws` WebSocket has no per-client authentication.~~ **Closed in Tier 7 (GRO-2058)**.

The WebSocket endpoint now supports three auth paths:

1. **Bearer token**: `Authorization: Bearer <token>` where token matches `PRISMATIC_WS_TOKEN`. Constant-time compare.
2. **HMAC signature**: `X-WS-Signature: <hex>` + `X-WS-Timestamp: <unix>` where signature = `HMAC-SHA256(secret, "GET\n/ws\n<timestamp>")`. Replay window is `PRISMATIC_WS_REPLAY_WINDOW` (default 60s).
3. **No-auth mode**: if neither token nor secret is set, WS accepts all (dev mode; relies on IP allowlist middleware).

Origin allowlist via `PRISMATIC_WS_ALLOWED_ORIGINS` (comma-separated). When configured, requests from origins not in the list are rejected with 1008.

Failure: 1008 close code with sanitized reason. Never 500.

## Cron reduction

The dispatcher cron (formerly every 5 minutes) was demoted to **daily at 08:00 UTC**. The webhook handler is now the primary dispatch path.

### Before Tier 7
- Cron `agent_dispatcher.py` ran every 5 minutes
- 288 cron ticks/day × ~20 Linear API calls = ~5760 calls/day from cron alone
- Plus webhook handler calls (additive)

### After Tier 7 (Tier 6 webhook + Tier 7 cron reduction)
- Cron `agent_dispatcher.py` runs **once daily** as a safety-net sweep
- Webhook handler fires per event (~1-2 Linear API calls per webhook)
- Cron-driven API consumption: ~20 calls/day (down from 5760)
- Total reduction: **~99.6%** of cron-driven Linear API consumption

### Why a daily safety-net sweep?

The cron still exists for two reasons:

1. **Catch missed webhooks.** If the engine is down when Linear sends an event, Linear retries for ~24h. After that, the event is lost. The daily cron catches any events that fell through.

2. **Sanity check.** Each morning, the dispatcher runs once to confirm the system is healthy and any backlog issues are dispatched.

### Configuration

The cron job ID is `e2f1a3b4c5d6`. Schedule is `0 8 * * *` (08:00 UTC daily). To revert to higher frequency:

```python
# In jobs.json: e2f1a3b4c5d6
"schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"}
```

Not recommended — defeats the Tier 7 budget reduction.

### Related cron changes

The `prismatic_event_trigger.py` cron (every 2 minutes) was fixed in Tier 7 (was erroring every 2 min due to `linear_call` import bug). The 2-minute cron still fires for Autobot alerts on AGY milestones — that's distinct from dispatch and stays.

Other broken cron scripts (`kai_callback_monitor`, `prismatic_port_progress`, `comment_trigger_monitor`, `github_pr_monitor`) were fixed via the `linear_api_compat` shim — they now route through `prismatic.dispatcher.gql()` (which is LinearBudget-gated).

## Secret rotation

The HMAC secrets are loaded per-request via `os.environ`. Rotation is **zero-downtime** via dual-secret support.

### Environment variables

| Variable | Role |
|---|---|
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` | Primary secret — accepts signatures from Linear configured with this value |
| `PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT` | Optional rotation candidate — accepts signatures during rotation window |

Same for GitHub: `PRISMATIC_GITHUB_WEBHOOK_SECRET` and `PRISMATIC_GITHUB_WEBHOOK_SECRET_NEXT`.

### Rotation procedure (zero-downtime)

1. **Generate new secret**: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. **Set rotation candidate**: `PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT=<new>` in vault, restart engine.
3. **Configure Linear** to use the new secret (in Linear's webhook settings).
4. **Verify rotation**: monitor `webhook_audit.log` — both `matches_primary` and `matches_next` should appear in traffic (or just the new one once Linear is fully switched).
5. **Promote**: rename `PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT` → primary, remove the old one. Restart engine.

Throughout the rotation window, both signatures are accepted. No service interruption.

### Diagram

```text
Before rotation:
  Linear → HMAC(SECRET_A) → engine accepts SECRET_A
  Engine env: SECRET only

During rotation (after step 2):
  Linear → HMAC(SECRET_A) OR HMAC(SECRET_B) → engine accepts either
  Engine env: SECRET_A + SECRET_B (NEXT)

After rotation (step 5):
  Linear → HMAC(SECRET_B) → engine accepts SECRET_B
  Engine env: SECRET_B only
```

### Failure modes

| Condition | Behavior |
|---|---|
| `PRISMATIC_LINEAR_WEBHOOK_SECRET` unset | HMAC validation skipped (dev only) |
| Both primary and next set, signature matches neither | 401, audited |
| Only primary set, signature matches | 200, processed |
| Both set, signature matches primary | 200, processed (legacy linear still using old secret) |
| Both set, signature matches next | 200, processed (linear already rotated to new secret) |

## Adoption checklist

Before deploying to production:

- [ ] `PRISMATIC_LINEAR_WEBHOOK_SECRET` set to 32+ byte random secret
- [ ] `PRISMATIC_GITHUB_WEBHOOK_SECRET` set to 32+ byte random secret
- [ ] Linear webhook configured to use the secret
- [ ] GitHub webhook configured to use the secret
- [ ] `PRISMATIC_ALLOWED_IPS` includes only trusted operator IPs (not `*`)
- [ ] `PRISMATIC_MAX_BODY_BYTES` left at 1MB unless specific reason to change
- [ ] `PRISMATIC_CORS_ORIGINS` set only if browser dashboard needs access
- [ ] Audit log directory (default `./prismatic_state/`) on a persistent volume
- [ ] Alert wired for `webhook_audit.log` entries with outcome=rejected (suspicious traffic)

## Test coverage

| Test | File | Purpose |
|---|---|---|
| `test_body_size_limit_rejects_huge_payload` | `tests/test_webhook_security.py` | 413 on oversized POST |
| `test_body_size_limit_allows_normal_payload` | `tests/test_webhook_security.py` | 200 on normal POST |
| `test_audit_log_appended_for_successful_dispatch` | `tests/test_webhook_security.py` | Audit record on success |
| `test_audit_log_appended_for_rejected_bad_signature` | `tests/test_webhook_security.py` | Audit record on HMAC fail |
| `test_audit_log_appended_for_queued_event` | `tests/test_webhook_security.py` | Audit record on queue |
| `test_replay_protection_rejects_old_event` | `tests/test_webhook_security.py` | 401 on stale event (10 min old) |
| `test_replay_protection_rejects_future_event` | `tests/test_webhook_security.py` | 401 on future event (clock-skew attack) |
| `test_replay_protection_accepts_recent_event` | `tests/test_webhook_security.py` | 200 on event within window |
| `test_replay_protection_allows_missing_createdat` | `tests/test_webhook_security.py` | 200 when createdAt absent (backward compat) |
| `test_rate_limit_returns_429_after_threshold` | `tests/test_webhook_security.py` | 429 when IP exceeds rate limit |
| `test_request_id_generated_when_absent` | `tests/test_webhook_security.py` | UUID4 generated when X-Request-ID absent |
| `test_request_id_echoed_when_provided` | `tests/test_webhook_security.py` | Caller's X-Request-ID echoed in response |
| `test_request_id_in_audit_log` | `tests/test_webhook_security.py` | Audit log entry includes request_id |
| `test_bearer_token_accepts_valid_token` | `tests/test_websocket_auth.py` | WS accepts valid bearer |
| `test_bearer_token_rejects_invalid_token` | `tests/test_websocket_auth.py` | WS rejects invalid bearer |
| `test_bearer_token_rejects_missing_token` | `tests/test_websocket_auth.py` | WS rejects missing auth header |
| `test_hmac_signature_accepts_valid` | `tests/test_websocket_auth.py` | WS accepts valid HMAC |
| `test_hmac_signature_rejects_stale_timestamp` | `tests/test_websocket_auth.py` | WS rejects stale timestamp (replay protection) |
| `test_hmac_signature_rejects_future_timestamp` | `tests/test_websocket_auth.py` | WS rejects future timestamp |
| `test_hmac_signature_rejects_wrong_secret` | `tests/test_websocket_auth.py` | WS rejects HMAC signed with wrong secret |
| `test_hmac_with_non_numeric_timestamp_rejected` | `tests/test_websocket_auth.py` | WS rejects malformed timestamp |
| `test_origin_allowlist_rejects_unknown_origin` | `tests/test_websocket_auth.py` | WS rejects unknown origin |
| `test_origin_allowlist_accepts_allowed_origin` | `tests/test_websocket_auth.py` | WS accepts allowed origin |
| `test_no_auth_configured_accepts_all` | `tests/test_websocket_auth.py` | WS accepts all in dev mode (no auth) |
| `test_dual_secret_accepts_primary` | `tests/test_webhook_security.py` | Primary secret works during rotation |
| `test_dual_secret_accepts_next` | `tests/test_webhook_security.py` | Next secret works during rotation |
| `test_dual_secret_rejects_unknown` | `tests/test_webhook_security.py` | Unknown signature rejected |
| `test_dual_secret_github` | `tests/test_webhook_security.py` | GitHub dual-secret rotation |
| `test_dispatch_calls_single_issue_helper` | `tests/test_webhook_security.py` | Verifies single-issue path |
| `test_sanitized_error_no_stack_trace` | `tests/test_webhook_security.py` | No internal info leak |
| `test_github_webhook_rejects_missing_signature` | `tests/test_webhook_security.py` | 401 on missing GitHub sig |
| `test_github_webhook_rejects_bad_signature` | `tests/test_webhook_security.py` | 401 on bad GitHub sig |
| `test_github_webhook_accepts_valid_signature` | `tests/test_webhook_security.py` | 200 on valid GitHub sig |
| `test_github_webhook_audit_log` | `tests/test_webhook_security.py` | GitHub audit record |
| `test_cors_no_wildcard_when_unset` | `tests/test_webhook_security.py` | No wildcard CORS |

Plus the 9 webhook handler tests in `tests/test_webhook_handler.py` covering HMAC, dispatch, queue, idempotency.

Plus the 8 single-issue dispatch tests in `tests/test_dispatch_single_issue.py` covering the new helper.

## Related docs

- `okf/standards/dispatch-architecture.md` — overall architecture
- `okf/integrations/webhook-handler-test-pattern.md` — test pattern
- `okf/standards/agy-peer-review.md` — peer review loop that surfaced these gaps

## Sign-off

- Fred (orchestrator): implemented 2026-06-19
- AGY peer-review: pending (GRO-2067 filed)