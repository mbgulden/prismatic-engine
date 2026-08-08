---
name: gateway-route-composition-and-websocket-security
description: "Canonical FastAPI gateway route composition, router mounts, WebSocket token authentication pre-accept, and unified transport security."
tags: [gateway, routes, fastapi, websocket, security, authentication]
related_skills:
  - agy-tdd-discipline
  - agy-secure-coding
  - prismatic-full-feature-delivery-gate
---

# gateway-route-composition-and-websocket-security

## Purpose

Ensure all required REST routers (Review Factory, Workspace Tree, Deploy Hook, PWP) are mounted into the canonical FastAPI gateway composition root, and unify HTTP and WebSocket security to validate credentials before connection acceptance.

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Trigger

Load when creating or mounting FastAPI routers, rebuilding the canonical app factory, modifying `@app.websocket("/ws")`, updating broadcaster authentication, or modifying transport credentials.

---

## Product Outcome

All required routers are mounted in the exact canonical `app` instance exercised in production, and HTTP/WebSocket transports enforce consistent, credential-derived authorization without secret leakage or unauthenticated endpoints.

---

## Existing Authority & Preservation Boundary

Inspect `prismatic/gateway/server.py` and `prismatic/gateway/control_auth.py` before editing. Preserve existing health, agent, lock, and PWP endpoints.

---

## Workflow Protocol

1. **Composition Root Identification**: Identify the single canonical `app` factory/composition root (`prismatic.gateway.server:app`) exercised in production and tests.
2. **Router Inventory & Mounts**: Mount Review Factory (`/api/review-factory`), Workspace Tree (`/api/workspace`), Deploy Hook (`/api/deploy`), and existing routers without dropping active surfaces.
3. **Route Inventory Verification**: Add an explicit route inventory test verifying that all required paths exist on a freshly instantiated app.
4. **Unified Principal Resolution**: Centralize Principal and session resolution so HTTP API routes and `/ws` share identical authentication semantics.
5. **Pre-Accept WebSocket Authentication**: Authenticate WebSocket clients **before** calling `await websocket.accept()`.
6. **No Weak Auth Patterns**: Remove default fallback tokens, substring matching (`token in allowed`), unencrypted query-string credentials, and opt-in security flags.
7. **Constant-Time Comparison**: Use exact constant-time string comparison (`hmac.compare_digest`) for token validation.
8. **Dashboard Session Token**: Provide a dashboard-safe credential/session mechanism that does not leak secrets in query parameters or server logs.
9. **Log Audit Hygiene**: Audit client metadata and authorization decisions to ensure no secret tokens appear in logs or stack traces.

---

## Anti-Stub Gate

Block completion if:
- A router module is created but not mounted in the canonical gateway `app`.
- Route tests run against a separate test-only FastAPI instance instead of `prismatic.gateway.server:app`.
- Authentication middleware excludes WebSockets or checks credentials after calling `websocket.accept()`.
- Default tokens (e.g. `"valid-token"`) or substring checks are permitted in production code.
- Security enforcement defaults to off or requires manual opt-in flags.

---

## Adversarial Test Requirements

- **Canonical Route Inventory**: Route inventory test against reconstructed `app` verifies presence of `/api/review-factory/healthz`, `/api/workspace/tree`, `/api/deploy/status`, and `/ws`.
- **Pre-Accept WS Rejection**: Unauthenticated or bad-token WebSocket connection is rejected with 1008/4001 before `websocket.accept()` completes.
- **Substring Attack Rejection**: Partial or substring token matches fail authentication.
- **Valid Scoped Principal Pass**: Connection with valid token succeeds and maps to correct Principal identity.
- **Log Redaction Audit**: Inspected server logs contain zero raw token string values.

---

## Required Proof Packet

```text
COMMAND=pytest prismatic/review_factory/tests/test_rf_routes.py tests/test_deploy_routes_api.py -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<path to log>
SCOPE=gateway-route-composition-and-websocket-security
HEAD=<commit sha>
TREE=<tree sha>
ROUTES_MOUNTED_VERIFIED=true
PRE_ACCEPT_WS_AUTH_VERIFIED=true
MARKER=PE_GATEWAY_ROUTE_SECURITY_OK
```
