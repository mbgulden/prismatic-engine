# Prismatic Engine — Test Batches

**Date:** 2026-06-07  
**Author:** Kai (Hermes Swarm)  
**Status:** Pending (not yet run)  

These test batches validate the Prismatic Engine's coordination protocols. They should be executed once the implementation phases are in place.

---

## Batch 1: Lane Compliance

**Goal:** Verify each agent respects its lane boundaries.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 1.1 | Kai pushes a change to `content/tours/mokulua.md` | ✅ Accepted (Kai owns content/) | ❌ Not tested |
| 1.2 | Kai pushes a change to `src/components/Nav.tsx` | ❌ Rejected (Kai doesn't own src/) | ❌ Not tested |
| 1.3 | Fred pushes a change to `deploy/wrangler.toml` | ✅ Accepted (Fred owns deploy/) | ❌ Not tested |
| 1.4 | Fred pushes a change to `content/tours/mokulua.md` | ❌ Rejected (Fred doesn't own content/, read-only) | ❌ Not tested |
| 1.5 | AGY pushes a new asset to `assets/logo.svg` | ✅ Accepted (AGY owns assets/) | ❌ Not tested |
| 1.6 | AGY pushes a change to `src/engine/state.ts` | ❌ Rejected (AGY doesn't own src/, read-only) | ❌ Not tested |

---

## Batch 2: Branch Discipline

**Goal:** Verify agents push to correct branch prefixes.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 2.1 | Kai pushes to `content/add-kayak-page` off `deploy-fresh` | ✅ Accepted | ❌ Not tested |
| 2.2 | Kai pushes to `feature/update-nav` | ❌ Rejected (Kai must use content/* prefix) | ❌ Not tested |
| 2.3 | Fred pushes to `feature/update-nav` off `deploy-fresh` | ✅ Accepted | ❌ Not tested |
| 2.4 | Fred pushes to `content/add-kayak-page` | ❌ Rejected (Fred must use feature/* or infra/*) | ❌ Not tested |
| 2.5 | Kai pushes directly to `deploy-fresh` | ❌ Rejected (only Fred merges to staging) | ❌ Not tested |
| 2.6 | Jules pushes to `deploy-fresh` | ❌ Rejected (Jules doesn't push directly, only PRs) | ❌ Not tested |
| 2.7 | Any agent pushes to `main` | ❌ Rejected (manual only) | ❌ Not tested |

---

## Batch 3: File Locking

**Goal:** Verify the mutex lock system prevents concurrent edits.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 3.1 | Kai claims `content/tours/mokulua.md` via `swarm.js lock` | ✅ Acquired | ❌ Not tested |
| 3.2 | Fred tries to claim the same file | ❌ Rejected: "File locked by kai" | ❌ Not tested |
| 3.3 | Kai releases the lock via `swarm.js unlock` | ✅ Released | ❌ Not tested |
| 3.4 | Fred claims the same file after release | ✅ Acquired | ❌ Not tested |
| 3.5 | Kai claims a file, then claims it again (own lock refresh) | ✅ Timestamp updated | ❌ Not tested |
| 3.6 | Kai claims 3 files, force-kills Kai mid-task | Locks remain | ❌ Not tested |
| 3.7 | Stale lock watcher runs (5 min since last heartbeat) | All Kai's locks auto-released | ❌ Not tested |
| 3.8 | Kai heartbeats every 60s while holding a lock | Lock stays active | ❌ Not tested |
| 3.9 | Fred claims whole `src/` directory | Kai can't claim any `src/` sub-file | ❌ Not tested |

---

## Batch 4: Pre-Push Conflict Detection

**Goal:** Verify conflict predictor catches overlaps before they reach staging.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 4.1 | Kai pushes `content/tours/new.md` — no other agent touched it | ✅ Push proceeds | ❌ Not tested |
| 4.2 | Fred pushes `src/components/Nav.tsx` — no conflicts | ✅ Push proceeds | ❌ Not tested |
| 4.3 | Fred pushes `src/kadence.css` — Kai also modified it since Fred's last pull | ❌ BLOCKED: "Kai modified this file — pull first" | ❌ Not tested |
| 4.4 | Kai pushes after pulling latest — no conflict | ✅ Push proceeds | ❌ Not tested |
| 4.5 | Kai pushes 3 files, 1 has conflict | ❌ All 3 blocked with conflict report | ❌ Not tested |
| 4.6 | File has conflict but agent holds the lock | ❌ Still blocked (lock ≠ merge permission) | ❌ Not tested |

---

## Batch 5: Staging Integrity

**Goal:** Verify staging branch remains clean.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 5.1 | Kai pushes `content/*` to GitHub, Fred merges to `deploy-fresh` | ✅ Clean merge | ❌ Not tested |
| 5.2 | Fred pushes `feature/*` to GitHub, Fred merges to `deploy-fresh` | ✅ Clean merge | ❌ Not tested |
| 5.3 | Staging preview URL reflects merged content | ✅ Preview shows both changes | ❌ Not tested |
| 5.4 | Fred merges content and feature that touch different files | ✅ No merge conflicts | ❌ Not tested |
| 5.5 | Fred merges content and feature that touch same file | ⚠️ Merge conflict — Fred resolves manually | ❌ Not tested |

---

## Batch 6: Agent Identity

**Goal:** Verify git attribution works.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 6.1 | Kai commits a change | Commit message: `[Kai] Add mokulua page` | ❌ Not tested |
| 6.2 | `git log --oneline` shows agent prefixes | Clear who did what | ❌ Not tested |
| 6.3 | `git config user.email` per agent profile | Fred uses `fred@activeoahutours.com` etc. | ❌ Not tested |
| 6.4 | Pre-push hook verifies commit prefix matches agent | Mismatch → BLOCKED | ❌ Not tested |

---

## Batch 7: Recovery Scenarios

**Goal:** Verify graceful recovery from failures.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 7.1 | Kai crashes mid-edit (holds lock, hasn't committed) | Stale lock watcher releases after 5 min | ❌ Not tested |
| 7.2 | Fred force-pushes to staging (against rules) | Pre-push hook rejects | ❌ Not tested |
| 7.3 | Git conflict on merge — Fred resolves manually | Manual resolution succeeds | ❌ Not tested |
| 7.4 | `swarm_locks.json` deleted accidentally | LockManager recreates empty one | ❌ Not tested |
| 7.5 | Two agents push to staging at the same time | Last push wins, pre-push hook on 2nd checks for divergence | ❌ Not tested |

---

## Batch 8: Content vs Code Conflict

**Goal:** Verify the critical content/code lane separation.

| # | Test | Expected Result | Status |
|---|------|----------------|--------|
| 8.1 | Kai adds 5 new markdown tour pages to `content/` | ✅ Accepted, no code touched | ❌ Not tested |
| 8.2 | Fred restructures `src/` navigation (changes file paths) | ✅ Accepted, no content touched | ❌ Not tested |
| 8.3 | Kai's content links to a page whose URL Fred changed | ⚠️ Detected by conflict predictor — reports broken reference | ❌ Not tested |
| 8.4 | Fred's nav component references a page title Kai changed | ⚠️ Detected by conflict predictor — reports mismatch | ❌ Not tested |

---

## Execution Notes

- Each batch can be run independently
- Tests are designed for the active-oahu repo (staging: deploy-fresh)
- Expected results should be verified via:
  - `git log` and `git diff`
  - `node .antigravity/swarm.js status`
  - Pre-push hook exit codes
  - Staging preview URL (active-oahu-tours-mirror.pages.dev)
- When a test fails, document:
  - What happened vs what was expected
  - The error output or state
  - Whether this reveals a protocol bug or an implementation bug
