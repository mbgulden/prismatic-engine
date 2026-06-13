# Provider Playbook: GitHub Copilot

> **Binary:** `copilot` | **Auth:** OAuth (`GITHUB_TOKEN`) | **PTY Required:** ✗ (ACP subprocess transport)  
> **Concurrent tasks:** 1 | **Cost:** Included in Copilot subscription

## Pipeline Stage → Copilot Command Map

### Stage 1: Worker Implementation

GitHub Copilot uses the ACP (Agent Communication Protocol) subprocess transport — no PTY needed.

```bash
copilot --acp --stdio "Implement <feature> in <file>.
  Build, test, and self-validate. Commit when done."
```

**Key flags:**
| Flag | Purpose |
|------|---------|
| `--acp --stdio` | ACP subprocess transport (standard integration path) |
| (prompt as argument) | One-shot execution |

**Cost:** $0.00 per task (included in Copilot subscription)  
**Timeout:** No enforced limit — monitor externally

---

### Stage 2: Self-Validation Loop

Copilot runs this internally during implementation. The agent:
1. Builds (runs project build command)
2. Tests (runs test suite)
3. Fixes failures
4. Re-tests
5. Hands off when clean

**Max iterations:** 3 (escalate after 3 failed loops)  
**Cost:** Included in implementation

---

### Stage 3: Peer Review (Read-Only)

Copilot CAN do code review in ACP mode:

```bash
# Review a PR
copilot --acp --stdio "Review the changes in this branch vs main.
  Check for: bugs, security issues, missing tests, style violations.
  READ-ONLY — do NOT modify any files.
  Output: structured findings with file:line references."
```

**⚠️ Limitation:** Copilot's review depth is lower than AGY or Claude. Use for quick sanity checks, not deep architecture reviews.

**Cost:** $0.00 (included in subscription)

---

### Stage 4: Fix Application

Same as Stage 1:

```bash
copilot --acp --stdio "Apply fixes from review. Address ALL findings.
  One commit per fix. Build and test after each fix."
```

---

### Stage 5: Orchestrator Review (Fred)

Not a Copilot stage.

---

### Stage 6: Post-Publish Validation

Copilot is not used for post-publish validation — it lacks the autonomous loop capabilities of AGY/Claude for this stage.

---

## Copilot Capabilities

| Capability | Available |
|------------|-----------|
| Code generation | ✅ |
| Code review | ✅ (basic) |
| Research | ❌ |
| Asset generation | ❌ |
| Multi-agent | ❌ |
| Vision | ❌ |
| Credit budget tracking | ❌ (subscription model) |

---

## Parallel Work

Copilot supports only 1 concurrent task in ACP mode. For parallel work, use Claude or AGY.

---

## Alternative: Codex CLI

If Copilot ACP is unavailable, the older OpenAI Codex CLI can serve as a drop-in:

```bash
# One-shot execution
codex exec "Implement <feature>" --full-auto

# Without sandbox (fastest)
codex exec "Fix <bug>" --yolo

# PR review in temp clone
REVIEW=$(mktemp -d) && git clone <repo> $REVIEW && cd $REVIEW && \
  gh pr checkout 42 && codex review --base origin/main
```

**Codex flags:**
| Flag | Effect |
|------|--------|
| `exec "prompt"` | One-shot execution, exits when done |
| `--full-auto` | Sandboxed but auto-approves file changes |
| `--yolo` | No sandbox, no approvals (fastest, most dangerous) |
| `review --base <branch>` | PR review mode |

**⚠️ OAuth note:** Codex OAuth is frequently rate-limited (429). Check status with `hermes auth list | grep codex`. When all creds are 429, fall back to Claude or deepseek.

---

## Alternative: OpenCode CLI

Provider-agnostic, open-source coding agent:

```bash
# One-shot
opencode run "Implement <feature>"

# With context files
opencode run "Review this config" -f config.yaml -f .env.example

# Force specific model
opencode run "Refactor auth" --model openrouter/anthropic/claude-sonnet-4

# Interactive TUI (requires pty)
opencode  # background=true, pty=true
```

**Key flags:**
| Flag | Use |
|------|-----|
| `run 'prompt'` | One-shot execution and exit |
| `-f <file>` | Attach context file(s) |
| `--model <provider/model>` | Force specific model |
| `--thinking` | Show model thinking blocks |
| `--format json` | Machine-readable output |

---

## When to Use Copilot

| Scenario | Best Provider |
|----------|--------------|
| Simple bug fixes, small features | Copilot (free, fast) |
| Complex refactoring, architecture | Claude or AGY |
| Research, documentation, planning | AGY or Claude |
| Asset generation (images, video) | AGY only |
| Budget-constrained shops | Copilot (subscription covers all) |
| High-quality review (security-criticial) | Claude or AGY |

---

## Pitfalls

- **No research capability:** Copilot cannot do web searches, read external docs, or synthesize research. Tasks requiring research must go to AGY or Claude.
- **Review depth limited:** Copilot reviews are surface-level compared to AGY/Claude. Use for quick sanity checks, not deep architecture audits.
- **Single concurrent task:** ACP mode supports one task at a time. Parallel work requires Claude or AGY.
- **OAuth rate limiting:** Codex OAuth frequently hits 429 rate limits. Always verify OAuth status before routing tasks to Copilot.
- **No multi-agent:** Copilot cannot orchestrate sub-agents. Multi-agent workflows require AGY.
- **No vision:** Copilot cannot process images. Visual QA and asset review require AGY or Claude.
