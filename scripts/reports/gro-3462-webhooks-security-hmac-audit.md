# GRO-3462 — Webhooks Security & HMAC Verification Audit

Audit time: 2026-07-05T21:49:14Z  
Auditor: Ned  
Scope: Prismatic Engine webhook-like HTTP ingress surfaces, signature/HMAC validation, payload filters, and network allowlist controls.

> Lane note: the Linear issue requested `audits/ned/`, but Ned's writable lanes in this repo are `scripts/`, `prismatic/`, and `plugins/`. This audit is saved under `scripts/reports/` to satisfy lane governance and avoid the known `audits/` pre-push rejection path.

## Executive summary

The repo has one partially protected payment webhook implementation and three unauthenticated gateway/event-ingest webhook surfaces:

| Surface | File | Current protection | Risk |
| --- | --- | --- | --- |
| Stripe billing webhook | `prismatic/billing/stripe_webhooks.py` | Uses Stripe library verification only when `stripe` package is installed and `STRIPE_WEBHOOK_SECRET` is set | **High**: ImportError fallback parses unsigned JSON |
| GitHub gateway webhook stub | `prismatic/gateway/server.py` | None | **High**: accepts raw body, no HMAC, no event validation |
| Linear gateway webhook stub | `prismatic/gateway/server.py` | None | **High**: accepts raw body, no Linear signature verification, no event validation |
| Alertmanager webhook | `prismatic/gateway/alert_manager.py` | None | **Medium/High**: accepts unauthenticated alert payloads and can trigger Telegram/Slack/file routing side effects |
| Event ingest route | `prismatic/gateway/ipc_bridge.py` | None | **High**: accepts arbitrary event payloads from HTTP POST and publishes to the event bus |

Bottom line: webhook ingress should be treated as **not production-safe on a public interface** until HMAC/signature verification, payload constraints, and network binding/allowlist rules are added.

## Evidence

### 1. Stripe signature verification has an unsafe unsigned fallback

File: `prismatic/billing/stripe_webhooks.py`

- `StripeWebhookHandler.handle_webhook()` calls `_verify_signature()` before dispatching event handlers (`lines 62-78`).
- `_verify_signature()` correctly rejects missing `STRIPE_WEBHOOK_SECRET` (`lines 114-117`).
- When `stripe` imports successfully, it uses `stripe.Webhook.construct_event(payload, sig_header, self._webhook_secret)` (`lines 119-124`).
- **Problem:** on `ImportError`, it logs a warning and returns `json.loads(payload)` without verifying the `Stripe-Signature` header (`lines 125-132`).
- Tests explicitly monkey-patch `_verify_signature` to bypass signature checks (`tests/test_stripe_billing.py` lines 281-288). That is acceptable as a unit-test isolation technique, but there is no compensating test that proves the real path fails closed when the Stripe SDK is absent.

Impact: if the runtime image misses the `stripe` package, any caller who can reach `/api/billing/stripe/webhook` can forge `invoice.paid` or `customer.subscription.deleted` events and mutate the credit ledger.

Recommended fix:

1. Remove the unsigned JSON fallback entirely. Missing Stripe SDK should be a startup/deployment error or a 400/500 fail-closed response.
2. Add tests that simulate `ImportError` and assert `StripeWebhookError` rather than unsigned acceptance.
3. Add replay/tolerance assertions if not already covered by Stripe SDK defaults.

### 2. GitHub and Linear gateway webhook stubs accept unsigned payloads

File: `prismatic/gateway/server.py`

- `github_webhook()` at `/api/gateway/github` reads `await request.body()`, logs byte count, and returns OK (`lines 313-318`).
- `linear_webhook()` at `/api/gateway/linear` reads `await request.body()`, logs byte count, and returns OK (`lines 321-326`).
- There is no `X-Hub-Signature-256`, `Linear-Signature`, timestamp, delivery-id, event-type, or secret validation.
- There is no payload schema filtering, size limit, replay dedupe, or event allowlist.

Impact: currently these are stubs with no downstream mutation in the audited branch, but if connected later they will normalize unsigned webhook acceptance. This is a classic future-footgun: a stub returns `ok`, tests pass, someone wires side effects behind it later.

Recommended fix:

1. Add a shared verifier helper using `hmac.compare_digest()` and provider-specific canonical bodies:
   - GitHub: `X-Hub-Signature-256: sha256=<hex>` over the raw request body.
   - Linear: verify against Linear's documented webhook signature header and timestamp format before parsing JSON.
2. Fail closed when the corresponding secret env var is unset in non-test mode.
3. Add explicit event allowlists before dispatch, e.g. GitHub `pull_request`, `pull_request_review`; Linear `Issue`, `Comment` only if needed.
4. Return 401/403 on invalid signature rather than `200 ok`.

### 3. Alertmanager webhook has no authentication or source filtering

File: `prismatic/gateway/alert_manager.py`

- `create_alert_webhook_route()` exposes `POST /alerts/webhook` (`lines 346-362`).
- The handler accepts a dict or list body, normalizes to a list, extracts alert labels/annotations, and routes each alert (`lines 370-388`).
- Routing can fire Telegram/Slack/file side effects via `AlertRouter.route()`.
- There is no HMAC/shared-secret header, bearer token, mTLS check, source IP allowlist, body-size cap, or schema rejection for unknown alert names/severities.

Impact: if this route is reachable outside a trusted private network, an attacker can send arbitrary critical alerts to Telegram/Slack or flood alert sinks. Even internally, a compromised workload can spoof alerts without attribution.

Recommended fix:

1. Require an `Authorization: Bearer <ALERTMANAGER_WEBHOOK_TOKEN>` or HMAC header for HTTP ingress.
2. Optionally support a CIDR allowlist for known Alertmanager/Tailscale source ranges.
3. Reject unknown `alertname` values unless a generic fallback is explicitly desired.
4. Add per-request alert count and body-size caps.

### 4. Event ingest route accepts arbitrary event-bus writes over HTTP

File: `prismatic/gateway/ipc_bridge.py`

- `create_event_ingest_route()` exposes `POST /events` (`lines 224-239`).
- It accepts a single event object or array, requires only `type` and `source` by docstring (`lines 240-244`), and passes each body into `publish_event()` (`lines 245-251`).
- No signature, token, source-IP allowlist, event type allowlist, or actor identity check is present at the HTTP boundary.

Impact: an exposed gateway lets any caller inject synthetic swarm events. Depending on downstream consumers, that can contaminate dashboards, audit trails, lock/event telemetry, or future automation.

Recommended fix:

1. Prefer Unix socket ingestion for local trusted publishers; disable HTTP event ingest by default.
2. If HTTP ingest remains, require HMAC or bearer auth and an event type allowlist.
3. Reject arbitrary `source` unless it maps to a configured agent/service identity.
4. Add payload size and batch count limits.

### 5. Network exposure defaults are permissive

File: `prismatic/gateway/server.py`

- CORS allows all origins, credentials, methods, and headers (`lines 58-65`).
- CLI host default is `0.0.0.0` (`lines 348-351`).
- OpenAPI is disabled, which reduces discovery but does not protect endpoints (`line 55`).

Impact: if the gateway is deployed on a public or broadly reachable interface, browser and non-browser clients can reach permissive webhook/event endpoints. CORS does not protect server-to-server webhook endpoints, but the `allow_credentials=True` + `allow_origins=["*"]` shape is still an unsafe default for any future cookie/header-authenticated dashboard endpoints.

Recommended fix:

1. Default host to `127.0.0.1`; require explicit config for `0.0.0.0`.
2. Add deployment-level allowlist rules: Tailscale/private CIDRs only unless a route has provider HMAC validation.
3. Replace wildcard CORS with configured dashboard origins.

## Payload filter checklist

Current status:

- Stripe: event type allowlist exists (`invoice.paid`, `invoice.payment_failed`, `customer.subscription.deleted`, `customer.subscription.updated`), but unsigned fallback undermines it.
- GitHub/Linear stubs: no provider event-type allowlist because no JSON parsing/dispatch exists yet.
- Alertmanager: accepts arbitrary alert names and severities; routing rules decide what fires, but unknown input is still accepted.
- Event ingest: accepts arbitrary event `type`/`source` and publishes it.

Minimum hardening target:

- Validate raw-body signature before JSON parsing.
- Enforce event type allowlists.
- Cap body size and batch length.
- Reject missing/unknown tenant/customer identifiers on side-effecting payment events.
- Log delivery id / event id for replay dedupe.

## Prioritized remediation plan

1. **P0 — Fail closed for Stripe SDK absence.** Remove unsigned fallback in `_verify_signature()` and add a regression test.
2. **P0 — Add shared HMAC verification helpers.** Centralize provider verification with `hmac.compare_digest()` and raw-body canonicalization.
3. **P1 — Protect `/api/gateway/github`, `/api/gateway/linear`, `/alerts/webhook`, and `/events`.** Add auth/signature gates before acknowledging payloads.
4. **P1 — Disable or private-bind generic event ingress.** HTTP event ingestion should not be on a public listener by default.
5. **P2 — Add network allowlist configuration.** Support trusted CIDRs and document reverse-proxy/Tailscale expectations.
6. **P2 — Add replay and schema tests.** Cover invalid signature, missing signature, old timestamp/replay, overlarge payload, unknown event type, and unsigned SDK-missing Stripe path.

## Verification performed for this audit

- Static search for webhook/signature/HMAC/allowlist terms across `prismatic/`, `scripts/`, and tests.
- Manual review of:
  - `prismatic/billing/stripe_webhooks.py`
  - `prismatic/gateway/server.py`
  - `prismatic/gateway/alert_manager.py`
  - `prismatic/gateway/ipc_bridge.py`
  - `tests/test_stripe_billing.py`
  - `tests/test_alert_manager.py`
- No production secrets were printed or modified.

## Definition of Done mapping

- Audit signature validations: **completed** — Stripe partial/fail-open path found; GitHub/Linear/Alertmanager/Event ingest missing verification.
- Audit payload filters: **completed** — Stripe allowlist exists; generic ingress surfaces need event/schema limits.
- Audit network allowlist rules: **completed** — no application-level allowlist found; gateway defaults are permissive.
- Markdown output: **completed** — this report.
- Output location: **completed with lane-governed substitution** — saved under `scripts/reports/` instead of forbidden `audits/ned/`.
