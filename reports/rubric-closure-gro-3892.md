# Rubric Closure Ledger: GRO-3892 — End-to-End Cohesive App Demo Path

This document lists the closure evidence for **GRO-3892** proving the end-to-end cohesion of the Prismatic Engine app surface (clean run → dashboard readiness → plugin selection → policy preview → job start → approval/rejection path → artifact/provenance → audit event → export/publish-safe action → safe disconnect).

---

## 1. Scorecard Summary

| Rubric ID | Category | Rubric Item | Current Score | Target Score | Evidence | Gap | Blocker | Owner | Next Action |
|:---|:---|:---|:---:|:---:|:---|:---|:---|:---|:---|
| **GRO-3892** | Integration | Cohesive App Surface Demo | **Verified API/TestClient proof** | 10 | Executed `PYTHONPATH=. python3 scripts/app_surface_golden_demo.py` from PR head; all 11 API/TestClient stages passed and output `APP_SURFACE_GOLDEN_DEMO_OK`. | Live dashboard visual proof is not included in this PR. | None for API/TestClient scope. | `agent:fred` review gate | Review PR #277 and decide whether API/TestClient proof is sufficient or request separate live UI screenshots/browser proof. |

---

## 2. General Metadata
- **Issue Link:** [GRO-3892](https://linear.app/team/issue/GRO-3892)
- **Title:** [Rubric 10/10][Post-pass] End-to-end cohesive app demo path
- **Owner:** `agent:fred` review gate after AGY output
- **Target Score:** 10 on the accepted 0–10 rubric
- **Current Score:** Verified for API/TestClient golden-demo scope; live UI visual proof is explicitly out of scope unless separately attached.

---

## 3. PR & Branch Info
- **Branch Name:** `feature/GRO-3892-golden-demo-path`
- **Pull Request URL/Number:** [PR #277](https://github.com/mbgulden/prismatic-engine/pull/277)

---

## 4. Acceptance Proof & Verification

### Description of Changes
- Created a robust app-wide integration verification script: `scripts/app_surface_golden_demo.py`.
- Wires together all primary touchpoints of the dashboard via REST calls against FastAPI's TestClient to verify the integrated APIs under a clean, isolated state environment.
- Validates the following 11-step golden demo lifecycle in sequence:
  1. **Clean Run**: Clears job/artifact registries and PWP status to prevent state pollution.
  2. **Dashboard Readiness**: Verifies GET `/api/plugins/catalog` and `/api/plugins/governance` are ready.
  3. **Plugin Selection**: Connects the `pwp-design-token-plugin`.
  4. **Policy Preview**: Evaluates policy checks: safe mock (`example-plugin` evaluates to `allow`) vs risky mock (`pwp` evaluates to `needs_approval`).
  5. **Job Start (Blocked)**: Ensures starting a job requiring operator approval is blocked (returns 409).
  6. **Approval & Job Execution**: Approves the job, starts it (status changes to `running`), and completes it.
  7. **Artifact/Provenance**: Emits an artifact registering complete provenance records (plugin ID, job ID, generator, and provider details).
  8. **Export/Publish Gating (Blocked)**: Prevents publishing or exporting the artifact prior to approval.
  9. **Approval & Action (Allowed)**: Approves the artifact, allowing subsequent publish-ready transition and export actions.
  10. **Audit Event**: Assures that the normalized cross-plugin audit events record all milestones (`job_created`, `started`, `completed`, `artifact_created`, `artifact_approved`).
  11. **Safe Disconnect & Continuity**: Disconnects PWP and verifies that historical job/artifact data survives.

### Verifier Command & Output
- **Command:** `PYTHONPATH=. python3 scripts/app_surface_golden_demo.py`
- **Exit Code:** 0
- **Output Marker / Excerpt:**
  ```text
  ========================================================================
  STEP: 1. Clean Run
  STATUS: PASS
  DETAIL: Cleared and initialized state at /tmp/agy-controlled-stage-proofs/20260715T205110Z/GRO-3892/state
  ========================================================================
  ...
  ========================================================================
  STEP: 11. Safe Disconnect
  STATUS: PASS
  DETAIL: Disconnected: True, Jobs preserved: True, Artifacts preserved: True
  ========================================================================

  🎉 APP SURFACE GOLDEN DEMO VERDICT: PASS
  APP_SURFACE_GOLDEN_DEMO_OK
  ```

---

## 5. Evidence Scope Boundary

This PR provides API/TestClient proof for the cohesive app surface path. It does **not** provide live browser/dashboard visual proof and does **not** attach desktop/tablet/mobile screenshots.

If live visual proof is required for a later release gate, attach real screenshots or browser-console evidence in a separate review artifact. Do not infer visual QA from this API/TestClient verifier.

---

## 6. Canonical Evidence Contract Alignment
- **Verification Status:** `verified_for_api_testclient_scope`
- **Verification Scope:** `ad_hoc_targeted_api_testclient`
- **Failure Category:** `none_observed_in_targeted_api_testclient_run`
- **Cleanup Status:** Done. Isolated state and test outputs written exclusively under `/tmp/agy-controlled-stage-proofs/20260715T205110Z/GRO-3892/` during the AGY run; Fred reran the script from PR head during output review.
- **Done Gate Result:** `review_pending_until_staging_governor_accepts_and_merges`

---

## 7. Final Reviewer Signoff
- **Reviewer:** pending Fred/staging-governor review
- **Signoff Date:** pending
- **Verdict:** REVIEW_PENDING
- **Review Notes:** API/TestClient execution evidence is present. Live UI proof is not included and should not be claimed from this PR.
