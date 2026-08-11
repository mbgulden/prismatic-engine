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

## 2. Mandatory Verification Invariants

1. **`PRE_PACKET_REMOTE_PUSH_CHECK`**: Antigravity MUST NOT generate a handoff packet artifact until `git push -u origin <branch>` has executed and `git ls-remote origin <branch>` returns the exact candidate commit SHA (`git rev-parse HEAD`).
2. **`DUAL_TREE_GIT_TRACKING_CHECK`**: `.agents/AGENTS.md` and `.agents/skills/<skill>/SKILL.md` MUST be committed to Git inside the repository root (`git ls-tree -r HEAD .agents`).
3. **`COMMAND_LINE_REPRODUCIBILITY`**: The evidence ledger MUST specify explicit environment variables (`$env:PYTHONPATH="."`) and root test configurations (`[tool.pytest.ini_options]`) so reviewers achieve 100% identical test results on bare `pytest` invocations.
4. **`FRESH_WORKTREE_CLEAN_ROOM_CHECK`**: Verification runs MUST prove that code executes cleanly on a fresh, untracked checkout without depending on local un-staged modifications or ambient environment state.
5. **`SECRET_SCANNING_FENCE`**: Every candidate commit MUST pass credential and path-safety scanning before pushing to remote.

---

## 3. Evidence Ledger Specification

When preparing a Pull Request or addressing review findings, the agent MUST assemble a complete **Evidence Ledger**:

```markdown
### Verification Evidence Ledger

| Metric | Required Threshold | Result |
| :--- | :--- | :--- |
| **Remote Branch** | `origin/<branch>` | `origin/feature/<name>` |
| **Head Commit SHA** | Exact Git SHA | `<HEAD_COMMIT_SHA>` |
| **Candidate Tree SHA** | Exact Tree SHA | `<TREE_SHA>` |
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
