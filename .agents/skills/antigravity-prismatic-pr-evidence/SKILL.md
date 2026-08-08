---
name: antigravity-prismatic-pr-evidence
description: Standard protocol for evidence generation, verification receipts, exact-head proof tracking, Playwright 375px visual audits, and seamless peer handoffs between Antigravity and George.
category: pr-governance
---

# Antigravity Prismatic PR Evidence & Handoff Protocol

This skill defines the standardized operational protocol for generating evidence bundles, verification receipts, visual audits, and seamless peer review handoff packets between **Antigravity** (Engineering / Authoring Agent) and **George** (Reviewer / Review Factory Judge / Principal Auditor).

---

## 1. Core Objectives & Invariants

1. **Exact-Head Verification**: Every PR repair or submission must be strictly tied to an exact commit SHA (`git rev-parse HEAD`) and candidate git tree SHA (`git cat-file -p HEAD | grep tree`).
2. **Zero-Whitespace / Lossless Source Integrity**: `git diff --check base..HEAD` must emit **0 lines and 0 warnings**.
3. **100% Green Automated Verification**: All unit, integration, and adversarial test suites must achieve a 100% green pass before handing off.
4. **Playwright 375px Zero-Overflow Invariant**: Any UI or dashboard surface must undergo automated Playwright visual audit (`scripts/visual_audit_playwright.js`) against a live running server, proving `scrollWidth <= 375` (zero horizontal overflow).
5. **Seamless Handoff Packet Generation**: Every completed review cycle generates a structured, unambiguous markdown handoff packet that George can validate deterministically.

---

## 2. Evidence Ledger Requirements

When preparing a Pull Request or addressing review findings, the agent MUST assemble a complete **Evidence Ledger**:

```markdown
### Verification Evidence Ledger

| Metric | Required Threshold | Result |
| :--- | :--- | :--- |
| **Branch** | Valid feature branch | `feature/branch-name` |
| **Head Commit SHA** | Exact Git SHA | `<HEAD_COMMIT_SHA>` |
| **Candidate Tree SHA** | Exact Tree SHA | `<TREE_SHA>` |
| **Pytest Suite** | 100% Green Pass | `N / N PASSED` |
| **Git Diff Check** | `git diff --check` | 0 errors / 0 warnings |
| **Ruff Check / Format** | Clean code hygiene | 0 errors / 0 warnings |
| **Playwright Audit** | 375px mobile viewport | `scrollWidth == 375` |
```

---

## 3. Playwright Visual Audit Protocol

Visual verification MUST be automated via Playwright rather than static HTML inspection:

1. **Script Path**: `scripts/visual_audit_playwright.js`
2. **Pytest Integration**: `tests/test_playwright_visual_audit.py`
3. **Execution Pattern**:
   - Spawn the live Gateway server (`prismatic.gateway.server`) on an isolated test port.
   - Navigate Chromium to `http://127.0.0.1:<PORT>`.
   - Measure viewports:
     - Desktop: `1920x1080`
     - Tablet: `768x1024`
     - Mobile: `375x812`
   - Assert `document.documentElement.scrollWidth <= 375` on 375px mobile viewport.
   - Save artifact screenshots to `artifacts/visual_audit/`.

---

## 4. Antigravity → George Handoff Packet Format

When handing off completed work or repairs to George:

1. **File Naming Convention**: `PR<PR_NUMBER>_ANTIGRAVITY_COMPLETE_REPAIR_PACKET_<DATE>.md`
2. **Required Sections**:
   - **Header**: PR Number, Branch, Repaired Head SHA, Tree SHA, Target Acceptance Marker.
   - **Addressed Findings Itemization**: Itemized breakdown of each finding raised in George's review, referencing exact modified file paths and function signatures.
   - **Verification Output & Logs**: Full un-truncated command outputs for test execution, ruff, git diff check, and visual audit.
   - **Acceptance Marker Declaration**: Explicit declaration of the green acceptance marker (e.g. `PE_REVIEW_FACTORY_EXACT_HEAD_REPAIR_OK`).

---

## 5. Peer Interoperability Protocol

To ensure seamless execution loops between Antigravity and George:

- **Fail-Closed Verification**: If any check fails, do NOT request review. Fix the root cause, re-run verification, and update the evidence ledger.
- **Idempotent Replays**: Re-evaluating an already repaired head SHA must yield identical verification receipts.
- **Direct Workspace Links**: Format all file paths in handoff communications as deep markdown links (e.g. `[server.py](https://prismatic.growthwebdev.com/workspaces?file=prismatic/gateway/server.py)`).
