---
name: prismatic-full-feature-delivery-gate
description: "Mandatory umbrella skill enforcing full vertical-slice delivery, complete feature matrices, runtime call traces, fail-closed security, and proof-backed feature completion."
tags: [umbrella, delivery, quality-gate, vertical-slice, tier3]
related_skills:
  - agy-runtime-contract-closure
  - agy-tdd-discipline
---

# prismatic-full-feature-delivery-gate

## Purpose

Mandatory umbrella quality gate for final PR merge readiness and release audits. Ensures that Antigravity delivers complete, secured, recoverable vertical slices instead of disconnected source-shaped stubs.

---

## Trigger

Load ONLY during explicit final PR merge readiness audit or release review pass. (Do NOT load during routine daily feature work, bug fixes, or localized refactoring).

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Product Outcome

Deliver a usable, secured, recoverable vertical slice whose UI/API/control-plane claims are backed by real state, database migrations, authenticated routes, and runtime decision paths.

---

## Existing Authority & Preservation Boundary

Inspect prior canonical good commits resolved from current task evidence or project authority references before editing. Never drop existing capabilities, PWP surfaces, or working tabs.

---

## Workflow Protocol

1. **Asset Inventory**: Inventory existing product assets, routers, persistence, migrations, services, generated artifacts, workflows, and tests before editing.
2. **Feature Matrix**: Write a feature matrix with rows for:
   - Durable state
   - Migration
   - Service logic
   - Runtime wiring
   - API endpoints
   - Dashboard UI
   - Authn/Authz
   - Audit logging
   - Failure path
   - Recovery behavior
   - Packaging / CI
   - Tests
   - Operator documentation
3. **Status Classification**: Mark each row as `existing/preserve`, `repair`, `missing`, or `not applicable` with evidence. `Not applicable` requires explicit architectural justification.
4. **Journey Tracing**: Trace one real operator journey and one hostile/failure journey across all applicable rows.
5. **Vertical Implementation**: Implement the smallest **vertical** slice connecting database -> service -> API -> UI -> audit.
6. **No-Mock Enforcement**: Remove or clearly label mock/sample/no-op data; mock data cannot satisfy live feature acceptance.
7. **Runtime Composition Verification**: Run route inventory and generated-artifact checks (`scripts/build_dashboard.py --check`) to prove runtime composition includes the feature.
8. **Exact-Head Proof**: Require exact-head independent review after every pushed repair head.

---

## Anti-Stub Gate

Block completion if:
- Any applicable matrix row is unfulfilled or marked completed via stubs.
- A helper class exists but its real caller bypasses or ignores it.
- The dashboard UI selector is optional or searched conditionally.
- Persistence is replaced by process-local in-memory state.
- Security defaults off or is opt-in via environment flags.
- Failure recovery requires manual database edits or manual state intervention.

---

## Standardized 11-Field Proof Packet

```text
COMMAND=<exact build/test command>
RESULT=<PASS|FAIL|BLOCKED>
LOG=<absolute path to log>
SCOPE=prismatic-full-feature-delivery-gate
AD_HOC_OR_CANONICAL=CANONICAL_EXACT_HEAD_PROOF
NOT_CLAIMING=<explicit non-claims, e.g. production_deployment>
HEAD=<exact 40-char commit sha>
TREE=<exact 40-char tree sha>
ARTIFACT_SHA256=<file sha256 digest when applicable>
ACTIVATION_TRACE=<which tiers loaded and why>
MARKER=PE_FULL_FEATURE_DELIVERY_GATE_OK
```
