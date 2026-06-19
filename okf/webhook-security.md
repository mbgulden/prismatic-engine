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

Middleware `limit_body_size` rejects any POST/PUT/PATCH with `content-length > MAX_BODY_BYTES`.

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_MAX_BODY_BYTES` | `1048576` (1MB) | Cap per-request body size |

Linear events are typically <10KB. 1MB allows batched events but rejects abusive payloads.

**Failure mode:** 413 `{"status": "rejected", "reason": "payload too large"}`.

### 3. IP allowlist (internal endpoints)

Middleware `ip_allowlist` restricts non-webhook endpoints to a configurable IP set.

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_ALLOWED_IPS` | `127.0.0.1,::1,testclient` | Comma-separated allowlist |

Webhook endpoints (`/api/gateway/*`) are exempt — they're HMAC-authenticated. `/health` is intentionally public.

**Failure mode:** 403 `{"status": "rejected", "reason": "ip not allowed"}`.

### 4. CORS policy

| Env var | Default | Purpose |
|---|---|---|
| `PRISMATIC_CORS_ORIGINS` | unset (no middleware) | Comma-separated origins |

When unset, **no CORS middleware is added**. When set, only the listed origins are allowed, with explicit method/header lists. Credentials are restricted to those origins.

**Forbidden by spec:** `allow_origins=["*"]` with `allow_credentials=True`. We never set that combination.

### 5. Single-issue dispatch (amplification limit)

The webhook handler calls `dispatch_issue_by_identifier(identifier)` — a new helper that fetches only the one issue that triggered the webhook, applies dedup + credit-policy + mode-switch gates, and launches the matching agent. **Cost: 1-2 Linear API calls per webhook event.** Prior implementation called `dispatch_once()` (full cycle) which triggered ~20 API calls per event.

### 6. Audit logging

Every webhook outcome is appended to `$PRISMATIC_STATE_DIR/webhook_audit.log` as JSONL (one JSON record per line).

```json
{"ts": 1718794400.123, "source": "linear", "outcome": "dispatched", "identifier": "GRO-2051", "event_type": "Issue"}
{"ts": 1718794401.456, "source": "linear", "outcome": "rejected", "reason": "bad signature"}
{"ts": 1718794402.789, "source": "github", "outcome": "received", "repo": "growthwebdev/prismatic-engine", "event_type": "opened"}
```

Outcomes: `dispatched`, `dispatch_no_op`, `queued`, `rejected`, `received`, `dispatch_failed`, `queue_failed`.

Audit log writes **never** block the webhook path — failures are logged via `logger.warning` but the request continues.

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

## Secret rotation

The HMAC secrets are loaded per-request via `os.environ`. Rotation procedure:

1. Generate new 32-byte secret: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. Set `PRISMATIC_LINEAR_WEBHOOK_SECRET_NEW=<new>` in vault (Linear still using old).
3. Update Linear webhook config to use new secret.
4. Wait 5 minutes (Linear retry window).
5. Switch env to `PRISMATIC_LINEAR_WEBHOOK_SECRET=<new>`, remove `_NEW`.
6. Restart engine.

Zero-downtime rotation requires dual-secret support — **not yet implemented**. Tracked as Tier 7 follow-up.

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