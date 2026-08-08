---
name: rf-browser-ci-lossless-acceptance
description: "Fail-closed Playwright browser and exact-head CI acceptance protocol, establishing byte losslessness, mobile layout proof, and proof-class precision."
tags: [acceptance, playwright, browser, ci, losslessness, proof-packet, tier3]
related_skills:
  - agy-tdd-discipline
  - prismatic-full-feature-delivery-gate
---

# rf-browser-ci-lossless-acceptance

## Purpose

Execute fail-closed Playwright browser audits and exact-head CI verification, establishing byte-for-byte template losslessness, 375px mobile zero-overflow layout proof, and strict proof-class precision without overclaiming.

---

## Trigger

Load ONLY during explicit final PR merge readiness audit or release review pass. (Do NOT load during routine daily feature work).

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Product Outcome

Browser and CI verification fail closed when Review Factory features are missing or stubbed; dependencies are reproducible; source and generated dashboard bytes match byte-for-byte; and proof classes accurately report exact verification boundaries.

---

## Workflow Protocol

1. **Immutable Head Archive**: Run browser and CI audit scripts from a fresh, immutable checkout or archive of the exact candidate commit.
2. **Reproducible Tooling**: Run `npm ci` and install matching browser binaries explicitly.
3. **Pre-Execution Byte Check**: Run dashboard build-check (`python scripts/build_dashboard.py --check`) before browser execution and record SHA-256 digests of source and generated templates.
4. **Mandatory Selector Probes**: Assert Review Factory DOM selectors (`#section-review-factory`, `#rf-jobs-table`, `#rf-job-modal`) explicitly.
5. **Multi-Viewport Execution**: At Desktop (1920x1080), Tablet (768x1024), and Mobile (375x812), navigate to Hub Dashboard, open Review Factory tab, load authenticated real stats/jobs, and establish authenticated `/ws` streaming.
6. **Fail-Closed Failure Modes**: Fail immediately on missing DOM selectors, horizontal overflow (`scrollWidth > 375`), unhandled JS console/page errors, failed HTTP 4xx/5xx requests, or unauthorized WebSocket connections.
7. **Artifact Capture**: Save deterministic PNG screenshots (`rf_dashboard_desktop.png`, `rf_dashboard_mobile_375px.png`), console summaries, and compact verification logs.
8. **Post-Execution Losslessness Check**: Re-run dashboard build-check and compare SHA-256 digests post-execution to prove test steps did not mutate generated template bytes.

---

## Standardized 11-Field Proof Packet

```text
COMMAND=python scripts/visual_audit_playwright.js && pytest tests/test_playwright_visual_audit.py -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<absolute path to audit log>
SCOPE=rf-browser-ci-lossless-acceptance
AD_HOC_OR_CANONICAL=CANONICAL_EXACT_HEAD_PROOF
NOT_CLAIMING=<explicit non-claims, e.g. production_deployment, merge_authorization>
HEAD=<exact 40-char commit sha>
TREE=<exact 40-char tree sha>
ARTIFACT_SHA256=<file sha256 digest when applicable>
ACTIVATION_TRACE=<which tiers loaded and why>
MARKER=PE_REVIEW_FACTORY_LOSSLESS_ACCEPTANCE_OK
```
