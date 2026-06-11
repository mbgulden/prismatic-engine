# PR Auto-Merger Enhancement — GRO-29 Implementation

**Date:** June 6, 2026
**Reference:** GRO-29 (archived) — Define high-volume PR review, analysis, and merge workflow for agent-generated branches
**Base doc:** `pr-review-workflow.md` in this directory

## What Was Implemented

### 1. Risk Classification Heuristic (Section 2)
- Classification function `classify_pr()` that fetches changed files from the PR and matches against pattern sets:
  - 🔴 **High Risk** — credential, network, payment, infrastructure paths → `pr:class:red`
  - 🟠 **Medium Risk** — API, schema, dependency, model paths → `pr:class:orange`
  - 🟡 **Low Risk** — config, gitignore, Makefile → `pr:class:yellow`
  - 🟢 **Safe** — README, docs, tests, examples → `pr:class:green`
- Override logic: highest-risk pattern wins regardless of PR body's stated class

### 2. Merge Decision Matrix (Section 5)
- Full matrix implemented in `merge_decision()`:
  - 🟢 Safe + CI OK → auto-merge immediately
  - 🟡 Low Risk + CI OK → cooldown timer (1h) then auto-merge
  - 🟠 Medium Risk + CI OK + Codex review approved → auto-merge
  - 🔴 High Risk → always blocked (requires Michael approval)
  - CI/Tests failed → route back to agent (up to 3 retries)

### 3. Cooldown Timer for 🟡 Low Risk PRs
- 1-hour cooldown (`COOLDOWN_SECONDS = 3600`)
- State stored in `/tmp/pr_auto_merger_state.json` (persists across cron cycles)
- First check starts the timer; subsequent checks report remaining time
- After expiry: auto-merge proceeds

### 4. CI Retry Tracking (Section 7)
- Max 3 retries per PR (`MAX_TEST_RETRIES = 3`)
- Retry count persisted in state file
- After 3 failures: escalates

### 5. PR Queue Prioritization (Section 8)
- PRs sorted: 🟢 green → 🟡 yellow → 🟠 orange → 🔴 red
- Within class: oldest first (FIFO)

## Script Location
`agentic-swarm-ops/ops/scheduled-workers/pr_auto_merger_and_router.py`
