# Provider Playbook: Google Antigravity (AGY)

> **Binary:** `agy` | **Auth:** OAuth (Google Flow) | **PTY Required:** ✓  
> **Concurrent tasks:** 7 max | **Cost:** Flow credits (10,000/mo budget)

## Pipeline Stage → AGY Command Map

### Stage 1: Worker Implementation

**Mode:** Interactive (PTY) — multi-turn coding with self-validation

```bash
# Standard implementation
agy --prompt-interactive --add-dir <repo_path>

# With specific task
agy --prompt-interactive --add-dir <repo_path> \
  "Implement <feature> in <file>. Self-validate: build, test, fix, retest."
```

**Key flags:**
| Flag | Purpose |
|------|---------|
| `--prompt-interactive` | Multi-turn agent mode with tool use |
| `--add-dir <repo>` | Grant access to repo directory |
| `--sandbox` | Restrict file access to workspace only |

**Credit cost:** 5 credits per code-generation task  
**Timeout:** No enforced limit (interactive) — monitor with tmux  

---

### Stage 2: Self-Validation Loop

AGY runs this internally during implementation mode. The agent:
1. Builds (compiles/lints)
2. Runs test suite
3. Fixes failures
4. Re-tests
5. Hands off only when clean

**Max iterations:** 3 (escalate to orchestrator after 3 failed loops)  
**Artifact:** Build log + test results + fix commits  
**Credit cost:** Included in implementation cost (5 credits/task)

---

### Stage 3: Peer Review (Read-Only)

**Mode:** Print (non-interactive) — bounded research, NO code changes

```bash
# Research-only mode
agy --print "REVIEW <branch> against <spec>. READ-ONLY — no code changes.
  Produce: review_report.md, gap_analysis.md, suggested_fixes.md.
  For each finding, cite specific lines. Include research citations for factual claims.
  Verdict: APPROVED | NEEDS_FIXES | REJECTED"
```

**Key flags:**
| Flag | Purpose |
|------|---------|
| `--print` | Non-interactive, exits when done — prevents builder instinct |
| `--sandbox` | Restrict to read-only access |

**Credit cost:** 3 credits per review  
**Timeout:** `--print_timeout: 300` (5 minutes — set in PRISMATIC_ENGINE.yaml)

**BEST PRACTICE:** Use a DIFFERENT provider for peer review when possible. AGY-implemented code reviewed by Claude catches more issues than same-model review.

---

### Stage 4: Fix Application

Same as Stage 1 — AGY in implementation mode, applying fixes from the peer review report.

```bash
agy --prompt-interactive --add-dir <repo_path> \
  "Apply fixes from peer review: <review_report>. Address ALL NEEDS_FIXES items. 
   Do NOT change architecture without re-review. One commit per fix."
```

**Credit cost:** 5 credits per fix-application task

---

### Stage 5: Orchestrator Review (Fred)

Not an AGY stage — Fred verifies and merges.

---

### Stage 6: Post-Publish Validation (Jules Loop)

```bash
# Active review (AGY implements fixes detected post-publish)
agy --prompt-interactive --add-dir <repo_path> \
  "Validate published code on <branch>. Run full validation loop. Fix any issues found."

# Read-only review (different agent — Claude or Ned)
# See provider-playbook-claude-code.md
```

---

## Credit Cost Reference

| Operation | Flow Credits | Notes |
|-----------|-------------|-------|
| Code generation (per task) | 5 | Implementation + self-validation |
| Code review (per review) | 3 | Print-mode read-only |
| Research (per task) | 8 | Deep research with citations |
| Omni Flash 4s | 15 | Image generation, 4 seconds |
| Omni Flash 6s | 20 | Image generation, 6 seconds |
| Omni Flash 8s | 25 | Image generation, 8 seconds |
| Omni Flash 10s | 30 | Image generation, 10 seconds |
| Veo Fast (any duration) | 10 | Video generation, fast mode |
| Veo Quality 8s | 100 | ⚠️ HIGH COST — requires human approval |
| Veo Quality 10s | 120 | ⚠️ HIGH COST — requires human approval |

**Monthly budget:** 10,000 Flow credits  
**Hard stop:** 9,000 (90% — emergency reserve)  
**Per-task max:** 500 credits  
**Per-session max:** 2,000 credits

---

## AGY Capabilities Unique to Google Antigravity

| Capability | Available |
|------------|-----------|
| Code generation | ✅ |
| Code review | ✅ |
| Research | ✅ |
| Asset generation (Omni/Veo) | ✅ |
| Multi-agent orchestration | ✅ (native sub-agent framework) |
| Vision (`from_file()`) | ✅ |
| Credit budget tracking (Flow) | ✅ |

---

## Pitfalls

- **OAuth expiry:** AGY OAuth can expire mid-session. Always configure a fallback provider in `PRISMATIC_ENGINE.yaml`.
- **--print mode prevents builder instinct:** Without `--print`, AGY defaults to implementation mode and may write code during review. Always use `--print` for research/review tasks.
- **Veo Quality high cost:** Veo Quality engine is 100-120 credits (10-12x the cost of Omni Flash). The policy engine blocks it without human approval.
- **Credit exhaustion at 90%:** The hard_stop_at 90% rule ensures emergency reserve. Don't configure to 100%.
- **`--print` timeout:** Default is 300s. Increase for large codebases or deep research.
