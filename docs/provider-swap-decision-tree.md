# Provider Swap Decision Tree

> **How to choose the right AI provider for each pipeline stage, based on task type, cost, and quality requirements.**

## Quick Decision Matrix

| Task Type | 1st Choice | 2nd Choice | Fallback | Never Use |
|-----------|-----------|-----------|----------|-----------|
| **Simple bug fix** (<50 lines) | Copilot | Claude (haiku) | Local 70B | — |
| **Feature implementation** | Claude (sonnet) | AGY | Copilot | Local 70B |
| **Complex refactoring** (>500 lines) | Claude (opus) | AGY (high-effort) | — | Copilot, Local 70B |
| **Code review (quick)** | Claude (haiku) | Copilot | Local 70B | — |
| **Code review (deep/security)** | Claude (sonnet) | AGY | — | Copilot, Local 70B |
| **Research & documentation** | AGY | Claude | — | Copilot, Local 70B |
| **Asset generation (images)** | AGY (Omni Flash) | — | — | All others |
| **Asset generation (video)** | AGY (Veo Fast) | — | — | All others |
| **Visual QA** | AGY | Claude | — | Copilot, Local 70B |
| **Multi-agent orchestration** | AGY | — | — | All others |
| **Post-publish validation** | Claude + AGY (paired) | Jules loop | — | Copilot, Local 70B |
| **Budget maximization** | Copilot | Local 70B | Claude (haiku) | Claude (opus), AGY (Veo) |

---

## Decision Flowcharts

### Flowchart 1: Implementation Tasks

```
START: Implementation task
  │
  ├─ Budget-constrained? ─── YES ──→ Copilot or Claude (haiku)
  │
  ├─ Complex refactoring (>500 lines)? ─── YES ──→ Claude (opus)
  │       └─ Claude unavailable? ──→ AGY (high-effort, --prompt-interactive)
  │
  ├─ Requires research during implementation? ─── YES ──→ AGY
  │       (AGY can research + implement in one session)
  │
  ├─ Simple feature (<100 lines)?
  │       ├─ Claude available → Claude (sonnet, --max-turns 10)
  │       └─ Claude unavailable → Copilot or Local 70B
  │
  └─ Standard feature (100-500 lines)?
          ├─ Claude (sonnet) — default choice
          ├─ Claude unavailable → AGY
          └─ Claude + AGY both down? → Copilot
```

### Flowchart 2: Review Tasks

```
START: Review task
  │
  ├─ Security-critical code? ─── YES ──→ Claude (sonnet, --allowedTools "Read")
  │       Required: line-numbered findings, exploitation paths, fix suggestions
  │
  ├─ Performance review? ─── YES ──→ Claude (opus) or AGY research mode
  │       Required: bottleneck analysis, profiling insights
  │
  ├─ Quick sanity check (<200 lines)? ─── YES
  │       ├─ Claude available → Claude (haiku, --max-turns 3)
  │       └─ Claude unavailable → Copilot or Local 70B
  │
  ├─ Full architecture review? ─── YES
  │       ├─ Claude (sonnet, --max-turns 10) — reads all files, cross-references
  │       └─ Claude unavailable → AGY (--print, "READ-ONLY. RESEARCH-ONLY.")
  │
  └─ High-volume review (>10 files)?
          ├─ Claude (sonnet) — batch with piped diffs
          └─ Local 70B — file-by-file loop (free electricity)
```

### Flowchart 3: Research Tasks

```
START: Research task
  │
  ├─ Requires web search? ─── YES ──→ AGY only (native research tools)
  │
  ├─ Requires external data (GA4, Search Console, Ubersuggest)?
  │       └──→ AGY only (API integrations)
  │
  ├─ Codebase documentation (internal)?
  │       ├─ Claude (sonnet, --max-turns 15) — reads code, writes docs
  │       └─ Claude unavailable → AGY (--print, research mode)
  │
  └─ Planning / sprint planning?
          ├─ AGY (--print, bounded research, no builder instinct)
          └─ AGY unavailable → Claude (sonnet, --max-turns 10)
```

### Flowchart 4: Asset Generation

```
START: Asset generation
  │
  ├─ Static image (sprite, icon, background)?
  │       └──→ AGY (Omni Flash) — 15-30 credits, fast
  │
  ├─ Video (trailer, animation)?
  │       ├─ Short (<8s) → AGY (Veo Fast) — 10 credits
  │       └─ Long (8-10s) → AGY (Veo Quality) — 100 credits, human approval required
  │
  ├─ Audio / music?
  │       └──→ NOT an AI provider task — use Lyria 2 (Vertex AI), see `agent-ned` skill
  │
  └─ No other provider generates assets. Image/video ALWAYS routes to AGY.
```

---

## Cost-Driven Decisions

### Cost per standard implementation task (~100 lines)

| Provider | Cost per Task | Monthly Budget | Tasks/Month |
|----------|--------------|----------------|-------------|
| Copilot | $0.00 | Subscription (~$10-20) | Unlimited |
| Local 70B | $0.00 (marginal) | ~$360 (GPU electricity) | Unlimited* |
| Claude (haiku) | ~$0.003 | Variable | Many thousands |
| Claude (sonnet) | ~$0.015 | Variable | ~667 per $10 |
| Claude (opus) | ~$0.05 | Variable | ~200 per $10 |
| AGY (code gen) | 5 credits | 10,000/mo | 2,000/mo |
| AGY (Veo Quality) | 100 credits | 10,000/mo | 100/mo max |

*Local 70B: limited by context window and single-task concurrency

### Decision by budget tier:

**Tier 1: Zero budget** (no subscriptions)
```
Implementation: Local 70B (electricity only)
Review: Local 70B (file-by-file)
Research: ❌ Not possible
Assets: ❌ Not possible
```

**Tier 2: Copilot only** ($10-20/mo subscription)
```
Implementation: Copilot (ACP mode)
Review: Copilot (basic)
Research: ❌ Not possible (route to Fred for manual)
Assets: ❌ Not possible
```

**Tier 3: Claude API** ($50-200/mo variable)
```
Implementation: Claude (sonnet/opus by complexity)
Review: Claude (haiku/sonnet by depth)
Research: Claude (limited — no web search)
Assets: ❌ Not possible (route to AGY or manual)
```

**Tier 4: Full stack** (Claude + AGY + Copilot)
```
Implementation: Claude (sonnet default) + Copilot (simple tasks)
Review: Claude (deep) + Copilot (quick)
Research: AGY (full research capabilities)
Assets: AGY (Omni Flash + Veo Fast, Veo Quality with approval)
Multi-agent: AGY (native sub-agent framework)
```

---

## Quality-Driven Decisions

### When to choose quality over cost:

| Situation | Pay for quality? | Why |
|-----------|-----------------|-----|
| Security-critical code (auth, crypto) | YES — Claude (opus) | One missed vuln is more expensive than any review cost |
| Production deployment | YES — Claude (sonnet) review | Broken production costs orders of magnitude more |
| Architecture decisions | YES — Claude (opus) or AGY research | Wrong architecture costs weeks of rework |
| Simple UI tweak | NO — Copilot or Claude (haiku) | Low risk, easy to verify visually |
| Documentation | NO — Claude (sonnet) is sufficient | Docs don't need opus-level reasoning |
| Prototype / spike | NO — fastest available | Speed > quality for throwaway code |

### The "rework multiplier" rule:

> A cheap review that misses problems costs MORE in rework than an expensive review that catches them.

- **Copilot review:** $0.00 → misses 30% of issues → $X rework
- **Claude (sonnet) review:** $0.015 → misses 10% of issues → $(X/3) rework
- **Claude (opus) review:** $0.05 → misses 5% of issues → $(X/6) rework

**Bottom line:** Use the strongest available reviewer for production code. The review cost is a fraction of rework cost.

---

## Provider Capability Matrix (Full)

| Capability | AGY/Gemini | Claude Code | Copilot | Local 70B |
|------------|------------|-------------|---------|-----------|
| Code generation | ✅ | ✅ | ✅ | ✅ |
| Code review (deep) | ✅ | ✅ | ❌ | ⚠️ (limited) |
| Code review (quick) | ✅ | ✅ | ✅ | ✅ |
| Research (web) | ✅ | ❌ | ❌ | ❌ |
| Research (codebase) | ✅ | ✅ | ❌ | ⚠️ (limited) |
| Asset generation | ✅ (Omni/Veo) | ❌ | ❌ | ❌ |
| Multi-agent | ✅ (native) | ❌ | ❌ | ❌ |
| Vision | ✅ (from_file) | ✅ | ❌ | ❌ |
| Credit tracking | ✅ (Flow) | ❌ (token) | ❌ (sub) | ❌ (local) |
| Autonomous loops | ✅ | ✅ | ⚠️ (basic) | ❌ |
| Structured output | ✅ | ✅ | ❌ | ❌ |
| Tool use (shell) | ✅ | ✅ | ✅ | ❌ |
| PTY required | ✅ | ✅ (interactive) | ❌ (ACP) | ❌ |
| Max concurrent | 7 | 3 | 1 | 1 |
| Context window | Large | 200K | Large | 8K |

---

## Fallback Chain Configuration

Configure in `PRISMATIC_ENGINE.yaml`:

```yaml
providers:
  default: claude-code
  fallback_chain:
    - google-antigravity    # If Claude is down/rate-limited
    - local-llm             # If both cloud providers are down
  
  stage_overrides:
    peer_review: claude-code        # Always use Claude for review
    worker_implementation: claude-code
    research: google-antigravity    # Always use AGY for research
    asset_generation: google-antigravity  # Only AGY does assets
```

### Fallback rules:
1. Try the stage override first (if configured)
2. Try the default provider
3. Walk the fallback chain in order
4. If all fail: escalate to orchestrator (Fred) with a blocker

---

## Common Pitfalls in Provider Selection

- **Same-model review:** Using AGY to review AGY's code. The same model is blind to its own failure modes. Always use a different provider for peer review when possible.
- **Copilot for research:** Copilot has no research tools. Tasks requiring web search, external APIs, or deep documentation synthesis fail silently.
- **Local 70B for large files:** The 8K context window can't hold a 2,000-line file. Split into small chunks.
- **Veo Quality without approval:** 100 credits per 8s clip. The policy engine blocks it without human approval — don't route Veo Quality tasks without the `requires:human-approval` label.
- **Budget exhaustion emergency reserve:** The 90% hard stop exists for a reason. A rogue loop can burn thousands of credits before human detection. Don't override it.
- **Fallback chain depth:** Every fallback adds latency. Keep the chain to 2-3 providers max.
