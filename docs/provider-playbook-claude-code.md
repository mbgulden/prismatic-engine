# Provider Playbook: Claude Code

> **Binary:** `claude` | **Auth:** API Key (`ANTHROPIC_API_KEY`) or OAuth | **PTY Required:** ✓ (interactive), ✗ (print mode)  
> **Concurrent tasks:** 3 max | **Cost:** Token-based (Anthropic usage API)

## Pipeline Stage → Claude Command Map

### Stage 1: Worker Implementation

**Mode 1: Print Mode (preferred for bounded tasks)**

```bash
claude -p "Implement <feature> in <file>. 
  Build, test, and self-validate before handing off.
  Commit each logical change separately." \
  --allowedTools "Read,Edit,Write,Bash" \
  --max-turns 10 \
  --max-budget-usd 2.00
```

**Mode 2: Interactive TMUX (multi-turn iterative work)**

```bash
# Start Claude in tmux session
tmux new-session -d -s claude-work -x 140 -y 40
tmux send-keys -t claude-work 'cd <repo> && claude --dangerously-skip-permissions' Enter

# Handle trust dialog (first time per directory)
sleep 4 && tmux send-keys -t claude-work Enter

# Handle permissions dialog (Down → Enter for "Yes, I accept")
sleep 2 && tmux send-keys -t claude-work Down && sleep 0.3 && tmux send-keys -t claude-work Enter

# Send task
sleep 2 && tmux send-keys -t claude-work 'Implement <feature>. Self-validate: build, test, fix, retest.' Enter

# Monitor
sleep 30 && tmux capture-pane -t claude-work -p -S -50
```

**Key flags:**
| Flag | Print | Interactive | Purpose |
|------|-------|-------------|---------|
| `-p` | ✓ | — | One-shot non-interactive mode |
| `--dangerously-skip-permissions` | — | ✓ | Auto-approve all tool use in interactive |
| `--allowedTools` | ✓ | ✓ | Restrict tools (e.g., `"Read,Edit,Bash"`) |
| `--max-turns <n>` | ✓ | — | Prevent runaway loops (5-10 for most tasks) |
| `--max-budget-usd <n>` | ✓ | — | Cap API spend (min ~$0.05 for cache creation) |
| `--model <alias>` | ✓ | ✓ | Model: `sonnet`, `opus`, `haiku` |
| `--effort <level>` | ✓ | ✓ | Reasoning depth: `low`, `medium`, `high`, `max` |
| `--add-dir <paths>` | ✓ | ✓ | Additional working directories |

**Cost estimate:** ~$0.015 USD per code-generation task (sonnet), ~$0.05+ for opus  
**Timeout:** Set per-task (120-300s typical)

---

### Stage 2: Self-Validation Loop

Claude runs this internally during implementation. The agent:
1. Builds (`npm build` / `cargo build` / `make`)
2. Tests (`npm test` / `pytest` / `cargo test`)
3. Fixes failures
4. Re-tests
5. Hands off only when clean

In print mode, enforce with:
```bash
claude -p "Implement <feature>. After implementing: run build, run tests, 
  fix any failures, retest. Only report success when all tests pass." \
  --allowedTools "Read,Edit,Write,Bash" --max-turns 15
```

**Max iterations:** 3 (escalate after 3 failed loops)  
**Cost:** Included in implementation cost

---

### Stage 3: Peer Review (Read-Only)

**Print mode (recommended — no interactive dialog handling):**

```bash
# Quick review
claude -p "Review this code for bugs, security issues, and style problems. 
  Be thorough. Cite specific lines. READ-ONLY — do NOT modify files." \
  --allowedTools "Read" \
  --max-turns 5 \
  --max-budget-usd 0.50

# Deep review with piped diff
git diff main...feature-branch | claude -p \
  "Review this diff. Produce: review_report.md with structured findings, 
   gap_analysis.md for spec gaps, suggested_fixes.md (specific, actionable, 
   NO direct code changes). Verdict: APPROVED | NEEDS_FIXES | REJECTED." \
  --allowedTools "Read" --max-turns 10 --max-budget-usd 1.00
```

**Structured JSON output for programmatic consumption:**
```bash
claude -p "Review auth.py for security issues. Return findings as structured JSON." \
  --output-format json \
  --json-schema '{"type":"object","properties":{"findings":{"type":"array","items":{"type":"object","properties":{"severity":{"type":"string"},"line":{"type":"integer"},"description":{"type":"string"},"fix":{"type":"string"}}}}},"required":["findings"]}' \
  --max-turns 5
```

**Cost estimate:** ~$0.008 USD per review (sonnet)  
**Timeout:** 60-120s

---

### Stage 4: Fix Application

Same as Stage 1 implementation, applying fixes from review:

```bash
claude -p "Apply fixes from peer review. Address ALL NEEDS_FIXES items.
  One commit per fix. Do NOT change architecture without re-review.
  After applying: build, test, confirm all tests pass." \
  --allowedTools "Read,Edit,Write,Bash" --max-turns 15 --max-budget-usd 2.00
```

**Cost estimate:** ~$0.015-0.05 USD (sonnet/opus)

---

### Stage 5: Orchestrator Review (Fred)

Not a Claude stage.

---

### Stage 6: Post-Publish Validation (Jules Loop)

```bash
# Active review (Claude implements fixes)
claude -p "Validate published code. Fix any issues found. 
  Run full build+test cycle after each fix." \
  --allowedTools "Read,Edit,Write,Bash" --max-turns 15

# Read-only review (paired with AGY implementing)
claude -p "Review published code on <branch>. READ-ONLY. 
  Report bugs, security issues, and test gaps. NO code changes." \
  --allowedTools "Read" --max-turns 10
```

---

## Model Selection Guide

| Model | Use Case | Cost | Quality |
|-------|----------|------|---------|
| `haiku` | Simple fixes, linting, formatting | $0.80/M tokens | Fast, good enough |
| `sonnet` (default) | Most implementation and review | $3.00/M tokens | Strong reasoning |
| `opus` | Complex refactoring, architecture | $15.00/M tokens | Deepest reasoning |

**Effort levels:** `low` (fast/cheap), `medium`, `high`, `max` (deepest reasoning)

---

## Print Mode Deep Dive

### One-shot with structured output
```bash
claude -p "Analyze auth.py for security issues" \
  --output-format json --max-turns 5
```
Returns JSON with `session_id`, `num_turns`, `total_cost_usd`, `stop_reason`.

### Piped input (avoid file-reading overhead)
```bash
cat src/auth.py | claude -p "Review this code for bugs" --max-turns 1
git diff HEAD~3 | claude -p "Summarize these changes" --max-turns 1
```

### Session continuation
```bash
# Get session_id from previous run's JSON output
claude -p "Continue and add connection pooling" \
  --resume <session_id> --max-turns 5

# Or resume most recent in this directory
claude -p "What did you do last time?" --continue --max-turns 1
```

### Bare mode (fastest startup — no hooks, plugins, CLAUDE.md)
```bash
claude --bare -p "Run all tests and report failures" \
  --allowedTools "Read,Bash" --max-turns 10
```
Requires `ANTHROPIC_API_KEY` (skips OAuth).

---

## Interactive Session Quick Reference

### Key dialogs to handle:
1. **Trust dialog:** Send `Enter` (default is "Yes, I trust")
2. **Permissions dialog:** Send `Down` then `Enter` (default is "No, exit" — WRONG)

### Useful slash commands:
| Command | Purpose |
|---------|---------|
| `/review` | Request code review |
| `/compact [focus]` | Compress context to save tokens |
| `/context` | Visualize context usage |
| `/cost` | View token/cost breakdown |
| `/model [model]` | Switch models mid-session |
| `/effort [level]` | Set reasoning effort |
| `/exit` | End session |

### Keyboard shortcuts:
| Key | Action |
|-----|--------|
| `Ctrl+C` | Cancel current generation |
| `Ctrl+D` | Exit session |
| `Ctrl+O` | See Claude's thinking process |
| `Shift+Tab` | Cycle permission modes |

---

## Pitfalls

- **Permissions dialog defaults to "No"** — you must send Down+Enter to accept, not just Enter.
- **`--max-budget-usd` minimum is ~$0.05** — system prompt cache creation costs this much. Setting lower errors immediately.
- **`--max-turns` is print-mode only** — ignored in interactive sessions.
- **Trust dialog only appears once per directory** — cached after first acceptance.
- **Session resumption requires same directory** — `--continue` finds the most recent session for the current working directory.
- **Context degradation above 70%** — use `/compact` proactively in interactive sessions.
- **Clean up tmux sessions** — `tmux kill-session -t claude-work` when done.
- **Claude may use `python` not `python3`** — self-corrects on failure but may add a turn.
