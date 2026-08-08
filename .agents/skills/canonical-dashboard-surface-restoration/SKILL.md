---
name: canonical-dashboard-surface-restoration
description: "Restoration of canonical Hub Dashboard surfaces, Review Factory tab fragments, Workspace Tree Explorer, deep link preservation, and Playwright 375px mobile audit."
tags: [dashboard, UI, review-factory, workspace-tree, playwright]
related_skills:
  - agy-tdd-discipline
  - prismatic-full-feature-delivery-gate
---

# canonical-dashboard-surface-restoration

## Purpose

Reconnect known-good product dashboard surfaces into the canonical Hub shell without rewrites, maintaining deterministic generated template bytes, real authenticated API adapters, deep links (`/dashboard?file=...`), and rendered desktop/mobile viewports.

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Trigger

Load for Hub Dashboard UI changes, Review Factory tab integration, Workspace Tree Explorer, generated dashboard HTML fragments, UI adapters, mobile viewport fixes, or deep-link routing.

---

## Product Outcome

The canonical Hub Dashboard seamlessly renders all active tabs (Overview, Swarm, Review Factory, Workspaces, Deploy) backed by live, authenticated API endpoints with zero horizontal overflow on 375px mobile viewports.

---

## Existing Authority & Preservation Boundary

Inspect `prismatic/gateway/dashboard_src/manifest.json`, `prismatic/gateway/dashboard_src/tabs/`, and `prismatic/gateway/templates/dashboard.html` before editing. Port Review Factory source fragments path-by-path from known-good commit `5558c0699e0dd08669af1de9b3a6dc595284c006`. Preserve current Hub and PWP work.

---

## Workflow Protocol

1. **Asset & Fragment Inventory**: Inventory canonical source fragments, manifest sequence, generated template output, API adapters, DOM selectors, and prior good commit paths.
2. **Fragment Porting**: Port Review Factory source fragment path-by-path from known-good commit `5558c0699e0dd08669af1de9b3a6dc595284c006` to `dashboard_src/tabs/review_factory.html`.
3. **Manifest Integration**: Add a dedicated fragment entry (`{"path": "tabs/review_factory.html"}`) to `dashboard_src/manifest.json` rather than editing only generated HTML.
4. **Adapter Wiring**: Reconnect real RF stats/jobs (`#stat-rf-queued`, `#rf-jobs-table`, `#rf-job-modal`) and Workspace Tree adapters through canonical authenticated gateway routes.
5. **Deep Link Preservation**: Preserve Workspace Tree Explorer inside the Workspaces tab, `/dashboard?file=...` deep links, and `/workspace-tree?file=...` fallback URLs.
6. **No Fake Data**: Visually label sample/mock data or remove it entirely from live dashboard claims.
7. **Deterministic Assembly**: Run `python scripts/build_dashboard.py --check` before and after tests; unit tests MUST NOT mutate generated HTML bytes.
8. **375px Mobile Viewport Assertion**: Verify rendered 375 CSS px layout behavior using Playwright; assert `scrollWidth <= 375` and zero element overflow.

---

## Anti-Stub Gate

Block completion if:
- A separate mini-dashboard or isolated HTML page is created instead of integrating into the canonical Hub template.
- Dashboard HTML is edited directly without updating `dashboard_src/` fragments and `manifest.json`.
- RF tab selectors (`#section-review-factory`, `#rf-jobs-table`, `#rf-job-modal`) are missing or optional.
- Mock data is presented as live API data without visual labeling.
- Mobile layout verification relies only on static CSS rules without Playwright viewport execution.

---

## Adversarial Test Requirements

- **RF Tab Required**: Review Factory tab selector is present and functional in DOM.
- **Byte-for-Byte Assembly**: `dashboard_src/` fragments and `templates/dashboard.html` agree byte-for-byte post-assembly.
- **Deep Link Functionality**: Deep links (`/dashboard?file=PATH`) resolve correctly.
- **API Resilience**: UI handles loading, empty, success, 401 unauthorized, and 500 error states cleanly.
- **375px Mobile Zero Overflow**: Playwright mobile audit confirms zero horizontal overflow (`scrollWidth <= 375`).

---

## Required Proof Packet

```text
COMMAND=python scripts/visual_audit_playwright.js && pytest tests/test_playwright_visual_audit.py -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<path to log>
SCOPE=canonical-dashboard-surface-restoration
HEAD=<commit sha>
TREE=<tree sha>
RF_TAB_VERIFIED=true
MOBILE_375PX_ZERO_OVERFLOW=true
MARKER=PE_DASHBOARD_SURFACE_RESTORED_OK
```
