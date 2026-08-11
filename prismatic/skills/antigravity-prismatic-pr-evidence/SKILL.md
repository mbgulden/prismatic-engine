---
name: antigravity-prismatic-pr-evidence
description: Standard protocol for evidence generation, verification receipts, exact-head proof tracking, Playwright 375px visual audits, and the 5-Step One-Shot Handoff Protocol for peer reviews between Antigravity and George.
category: pr-governance
---

# Antigravity Prismatic PR Evidence & One-Shot Handoff Protocol

This skill defines the standardized operational protocol for generating evidence bundles, verification receipts, visual audits, and bulletproof "One-Shot" peer review handoff packets between **Antigravity** (Engineering / Authoring Agent) and **George** (Reviewer / Review Factory Judge / Principal Auditor).

---

## 1. Core Objectives & The 5-Step "One-Shot" Protocol

To eliminate review rejections, un-reachable commits, and evidence mismatches, every completed work handoff MUST follow this strict 5-step sequence:

```text
 1. LOCAL TDD & FIXES     ─────▶ Run tests & fix platform edge cases (fcntl/pwd/paths)
           │
           ▼
 2. DUAL-TREE SYNC        ─────▶ Track .agents/ & skills/ in repository Git root
           │
           ▼
 3. REMOTE PUSH           ─────▶ Push branch to origin FIRST (git push -u origin <branch>)
           │
           ▼
 4. VERIFY REMOTE REF     ─────▶ Prove git ls-remote origin <branch> returns exact HEAD SHA
           │
           ▼
 5. GENERATE LOG & PACKET ─────▶ Run verification, capture log SHA-256, write packet
```

---

## 2. Process Retrospective Lessons (George Audit Synthesis)

1. **Remote Ref Snapshot Requirement**: The packet MUST embed the exact `git ls-remote origin <branch>` output captured at packet generation time.
2. **Environment Portability Gate**: Code and tests MUST NOT depend on hardcoded profile names (`ned`, `george`) or import-time cached profile paths.
3. **Self-Verifying Evidence**: Verification packets must pass their own structural and empirical validation gates before publication.

---

## 3. Evidence Ledger Specification

When preparing a Pull Request or addressing review findings, the agent MUST assemble a complete **Evidence Ledger**:

```markdown
### Verification Evidence Ledger

| Metric | Required Threshold | Result |
| :--- | :--- | :--- |
| **Remote Branch** | `origin/<branch>` | `origin/feature/<name>` |
| **Head Commit SHA** | `git rev-parse HEAD` | `<HEAD_COMMIT_SHA>` |
| **Candidate Tree SHA** | `git cat-file -p HEAD` | `<TREE_SHA>` |
| **Remote Reachability** | `git ls-remote origin <branch>` | `REMOTE_REACHABLE_OK` |
| **Pytest Suite** | 100% Green Pass | `N / N PASSED` |
| **Git Diff Check** | `git diff --check` | 0 errors / 0 warnings |
| **Tracked Git Rules** | `.agents/AGENTS.md` in HEAD | Tracked in Git |
| **Log SHA-256 Digest** | `Get-FileHash` SHA-256 | `<64-char Hex SHA>` |
```

---

## 4. Playwright 375px Visual Audit Protocol

Visual verification MUST be automated via Playwright against a live Gateway instance:

1. **Script Path**: `scripts/visual_audit_playwright.js`
2. **Execution Pattern**:
   - Spawn live Gateway server (`prismatic.gateway.server`) on isolated test port.
   - Assert `document.documentElement.scrollWidth <= 375` on 375px mobile viewport (zero horizontal overflow).
   - Save screenshots to `artifacts/visual_audit/`.

---

## 5. Peer Interoperability Protocol

- **Fail-Closed Verification**: If any check fails, do NOT request review. Fix the root cause, re-run verification, push to remote, and update the evidence ledger.
- **Idempotent Replays**: Re-evaluating an already repaired head SHA must yield identical verification receipts.
- **Direct Workspace Links**: Format all file paths in handoff communications as deep markdown links (e.g. `[server.py](https://prismatic.growthwebdev.com/workspaces?file=prismatic/gateway/server.py)`).
