---
name: prismatic-validation-pipeline
description: "Platform-agnostic multi-agent validation pipeline — self-validation loops, peer review, post-publishing validation. Works with Google AI Ultra, Claude, GitHub Copilot, local 70B models. Built into the Prismatic Engine."
category: agent-orchestration
---

# Prismatic Validation Pipeline

Platform-agnostic, provider-swappable multi-agent quality pipeline. Built into the Prismatic Engine as a repeatable pattern. Works with any AI provider stack: Google AI Ultra (AGY/Gemini), Anthropic Claude (Claude Code), GitHub Copilot, or local 70B models (llama.cpp/vLLM).

## Pipeline Architecture

```
Worker (implement)
  ├── Self-Validation Loop (build → test → fix → retest → pass)
  ├── Peer Review (different provider: detailed report + research)
  ├── Second Witness (independent AGY: architecture compliance audit)
  │     └── APPROVED / NEEDS_CHANGES / BLOCKED → creates fix tasks
  ├── Fix Application (address peer review + second witness findings)
  └── Orchestrator Approval (Fred: verify all gates passed → merge)
```

### Post-Publish (Jules CLI (jules.google.com)/GitHub-triggered)
```
Jules CLI (jules.google.com) detects GitHub push
  ├── Active Review (implement fixes)
  ├── Read-Only Review (report + suggestions)
  ├── Apply Suggestions (code/build loop)
  ├── Publish to GitHub
  └── Jules CLI (jules.google.com) validates → stop or restart loop
```

## Provider Interface (Platform-Agnostic)

Every AI provider implements this abstract interface. The Prismatic Engine routes to the configured provider without caring about the underlying implementation.

### Provider Contract (PRISMATIC_ENGINE.yaml)

```yaml
providers:
  # Google AI Ultra (Antigravity SDK / AGY CLI / Gemini API)
  google-antigravity:
    type: google-antigravity
    auth:
      method: oauth  # or api_key, service_token
      keyring: true   # use OS keyring
      fallback_env: GOOGLE_AI_CREDENTIALS
    capabilities:
      - code_generation
      - code_review
      - research
      - asset_generation     # Gemini Omni / Veo 3.1
      - multi_agent          # native sub-agent framework
      - vision               # from_file() multimodal loopback
    credit_model:
      provider: google-flow
      monthly_budget: 10000    # AI Ultra: 10,000 Flow credits
      cost_map:
        omni-flash: {4s: 15, 6s: 20, 8s: 25, 10s: 30}
        veo-fast: {4s: 10, 6s: 10, 8s: 10}
        veo-quality: {8s: 100}  # HIGH COST — requires human approval
      hard_limit: 9000  # stop at 90% of monthly budget
    invocation:
      binary: agy
      research_mode: "--print"            # bounded, no builder instinct
      implementation_mode: "--prompt-interactive --add-dir <repo>"
      pty_required: true
      max_concurrent: 7
      print_timeout: 300
    policies:
      - name: block_veo_quality_over_8s
        rule: "engine == 'veo-quality' && duration > 8"
        action: deny
      - name: human_approval_veo_quality
        rule: "engine == 'veo-quality'"
        action: ask_user
      - name: default_allow
        rule: "*"
        action: allow

  # Anthropic Claude (Claude Code CLI)
  claude-code:
    type: claude-code
    auth:
      method: api_key
      env_var: ANTHROPIC_API_KEY
    capabilities:
      - code_generation
      - code_review
      - research
    credit_model:
      provider: anthropic
      monthly_budget: null     # no hard cap — token-based pricing
      cost_map: {}             # cost tracked via Anthropic usage API
    invocation:
      binary: claude
      research_mode: "-p"      # non-interactive print
      implementation_mode: ""  # interactive mode
      pty_required: true
      max_concurrent: 3
    policies:
      - name: rate_limit_per_minute
        rule: "requests_per_minute > 10"
        action: throttle

  # GitHub Copilot
  github-copilot:
    type: github-copilot
    auth:
      method: oauth
      env_var: GITHUB_TOKEN
    capabilities:
      - code_generation
      - code_review
    credit_model:
      provider: github
      monthly_budget: null     # included in Copilot subscription
    invocation:
      binary: copilot
      research_mode: "--acp --stdio"
      implementation_mode: "--acp --stdio"
      pty_required: false      # ACP subprocess transport
      max_concurrent: 1
    policies:
      - name: default_allow
        rule: "*"
        action: allow

  # Local 70B Model (llama.cpp / vLLM)
  local-llm:
    type: local-llm
    auth:
      method: none             # local endpoint, no auth
    capabilities:
      - code_generation
      - code_review
      - research               # limited by context window
    credit_model:
      provider: local
      monthly_budget: null     # electricity only
      context_limit: 8192      # tokens — critical for 70B models
    invocation:
      endpoint: http://localhost:8080/v1
      binary: null             # HTTP API, not CLI
      pty_required: false
      max_concurrent: 1        # GPU-bound
    policies:
      - name: context_80pct_warning
        rule: "context_filled >= context_max * 0.8"
        action: warn
```

### Provider Capability Matrix

| Capability | AGY/Gemini | Claude Code | Copilot | Local 70B |
|------------|------------|-------------|---------|-----------|
| Code generation | ✅ | ✅ | ✅ | ✅ |
| Code review | ✅ | ✅ | ✅ | ✅ |
| Research | ✅ | ✅ | ❌ | ⚠️ (limited) |
| Asset generation | ✅ (Omni/Veo) | ❌ | ❌ | ❌ |
| Multi-agent | ✅ (native) | ❌ | ❌ | ❌ |
| Vision | ✅ (from_file) | ✅ | ❌ | ❌ |
| Credit budget | ✅ (Flow) | ❌ (token) | ❌ (sub) | ❌ (local) |

## Pipeline Stage Definitions

Each stage is a provider-agnostic contract. Swap the provider, the contract stays the same.

### Stage 1: Worker Implementation

```
Contract:
  role: implementer
  inputs: Linear issue (title + description + spec)
  outputs: code changes on feature branch
  self_validation: REQUIRED (build + test + lint before handing off)
  lane: feature/ branch, locked files
  
Provider mapping:
  AGY: --prompt-interactive --add-dir <repo>
  Claude: interactive mode with repo context
  Copilot: --acp --stdio with repo context
  Local: HTTP POST to /v1/chat/completions with system prompt
```

### Stage 2: Self-Validation Loop (Claude-Style)

```
Contract:
  role: worker (same agent)
  mode: reflexively validate own output
  steps:
    1. Build: compile/transpile/lint
    2. Test: run test suite
    3. Fix: address any failures
    4. Retest: verify fixes
    5. Pass: only hand off when clean
  max_iterations: 3 (escalate to orchestrator after 3 failed loops)
  artifact: build log + test results + fix commits
  
Provider mapping:
  All: bash commands (npm test, cargo build, pytest)
  AGY bonus: ToolContext.set_state() to track iteration count
```

### Stage 3: Peer Review

```
Contract:
  role: reviewer (different agent from worker)
  mode: read-only analysis
  inputs: worker's branch + self-validation logs
  outputs:
    - review_report.md (structured findings)
    - gap_analysis.md (what's missing vs spec)
    - suggested_fixes.md (specific, actionable, NO core file changes)
    - verdict: APPROVED | NEEDS_FIXES | REJECTED
  constraints:
    - READ ONLY on worker's files
    - NO direct code changes
    - MUST cite specific lines for each finding
    - MUST include research citations for factual claims
  
Provider mapping:
  AGY: --print mode, read-only, "RESEARCH-ONLY. Do NOT write code."
  Claude: -p mode, read-only directive
  Copilot: --acp --stdio with read-only constraints
  Local: HTTP POST with read-only system prompt
```

### Stage 4: Fix Application

```
Contract:
  role: implementer (can be same worker or different)
  inputs: peer review report + suggested fixes
  outputs: fix commits on feature branch
  self_validation: REQUIRED (re-run self-validation loop)
  constraints:
    - Address ALL findings marked NEEDS_FIXES
    - DO NOT change architecture without peer re-review
    - Each fix in separate commit
  
Provider mapping:
  Same as Stage 1
```

### Stage 4.5: Second Witness (Independent Architecture Review)

```
Contract:
  role: reviewer (AGY, independent of worker and peer reviewer)
  mode: architecture compliance audit
  trigger: cron-driven (every 30 min) or triggered after peer review approval
  inputs:
    - Architecture blueprint (specs/core-architecture-v1.md)
    - Task implementation plans
    - Completed work (commits, PRs, deploy artifacts)
  outputs:
    - Compliance verdict per task: APPROVED / NEEDS_CHANGES / BLOCKED
    - Gap analysis: what was built vs what was specified
    - Fix tasks: created directly in Linear for NEEDS_CHANGES/BLOCKED items
  constraints:
    - RUNS ON A SEPARATE PROVIDER from the worker and peer reviewer
    - Cross-references ALL work against the architecture spec (not just code quality)
    - Creates concrete Linear fix tasks with exact file paths and expected changes
    - Never marks own work done — only creates tasks for others
  agent_assignment:
    - Design/spec issues → agent:agy
    - Implementation issues → agent:ned or agent:fred
    - Orchestration issues → agent:fred
  
Provider mapping:
  AGY: deepseek-v4-pro, --print mode, loads full blueprint context
  Cron: every 30 min (not 5 min — reviews need depth, not speed)
  Delivery: Telegram to user for visibility
```

**Second Witness Context File** (loaded on every run):

For the full end-to-end operating procedure (scan, review, rate, fix-task creation, report format), see `references/second-witness-operating-procedure.md`.

```markdown
# Second Witness Review Protocol — Prismatic Engine Core

## Architecture Blueprint
specs/core-architecture-v1.md

## Task Registry
| Issue | Title | State | Spec | Implementation |
|-------|-------|-------|------|----------------|
| GRO-1494 | Dual-runtime isolation | In Progress | core-architecture-v1.md §2 | .prismatic/ dirs, venvs |
| GRO-1495 | Distribution packaging | In Review | §4 | pyproject.toml, install.sh |
| ... | ... | ... | ... | ... |

## Review Protocol
1. Load architecture blueprint
2. Scan all Linear issues in Review/Done states
3. For each task:
   a. Verify implementation matches spec requirements
   b. Cross-reference file paths against blueprint
   c. Check for scope creep (built more/less than specified)
   d. Rate: APPROVED / NEEDS_CHANGES / BLOCKED
4. Create fix tasks for NEEDS_CHANGES/BLOCKED
5. Generate timestamped report

## Fix Task Creation
- NEEDS_CHANGES → create Linear issue, label agent:agy (if design) or agent:ned (if code)
- BLOCKED → create Linear issue, label agent:fred, add blocker description
- APPROVED → no action, log to report

## Anti-Patterns
- Do NOT re-review already-APPROVED tasks (check state before reviewing)
- Do NOT fabricate findings (cite specific spec sections and file paths)
- Do NOT create fix tasks that duplicate existing ones
```

### Stage 5: Final Review (Orchestrator)

```
Contract:
  role: orchestrator (Fred or equivalent)
  mode: verification, NOT re-review
  checks:
    1. All peer review findings addressed? (cross-reference report)
    2. Second Witness approved? (check for compliance verdict)
    3. Build passes? (check CI/logs)
    4. Tests pass? (check test results)
    5. No new files in read-only lanes? (pre-push hook)
    6. Locks released? (swarm.js status)
  action: APPROVE (merge to staging) or RETURN (back to worker)
  metric: time from first commit to final approval
  
Provider mapping:
  Fred (Hermes orchestrator) — the only agent that can merge to staging
  Alternate: Jules CLI (jules.google.com) as post-publish validator (see Phase 2)
```

### Stage 6: Post-Publish Validation (Jules CLI (jules.google.com) Loop)

```
Contract:
  role: validator (Jules CLI (jules.google.com) or equivalent)
  trigger: GitHub push detected
  mode: full validation loop on published code
  steps:
    1. Jules CLI (jules.google.com) detects push → triggers validation
    2. Reviewer agent does code/build loop (implements fixes)
    3. Second reviewer does read-only review (report + suggestions)
    4. First agent applies suggestions (code/build loop)
    5. Publish fixes to GitHub
    6. Jules CLI (jules.google.com) validates final state → STOP or RESTART
  
Provider mapping:
  Trigger: Jules CLI (jules remote new) or GitHub webhook
  Reviewer 1 (active): AGY/Claude/local (implementation mode)
  Reviewer 2 (read-only): Ned/Claude/local (review mode)
  
Stop conditions:
  - No findings in review report
  - 3 full loops completed (prevent infinite cycle)
  - Credit budget exhausted
```

## Credit Budget Policy Engine

Platform-agnostic credit tracking. Adapted from Google Antigravity SDK's `policy` hooks and the existing BudgetManager (`BudgetManager.ts`).

### Policy Definition (PRISMATIC_ENGINE.yaml)

```yaml
policies:
  global:
    monthly_budget: 10000       # platform credits (Flow/tokens/requests)
    hard_stop_at: 9000          # 90% — emergency reserve
    per_task_max: 500           # single task cannot exceed
    per_session_max: 2000       # single session cannot exceed
  
  # Policy chain: first match wins
  rules:
    - name: block_high_cost_without_approval
      provider: google-antigravity
      condition: "engine == 'veo-quality'"
      action: ask_user
      message: "⚠️ Agent requested veo-quality generation (100 credits). Approve?"
    
    - name: block_over_budget_task
      provider: "*"
      condition: "estimated_cost > per_task_max"
      action: deny
      message: "Task exceeds per-task credit limit."
    
    - name: block_over_budget_session
      provider: "*"
      condition: "session_total + estimated_cost > per_session_max"
      action: deny
      message: "Session credit limit reached. Wait for next session."
    
    - name: hard_stop_monthly
      provider: "*"
      condition: "monthly_total >= hard_stop_at"
      action: deny
      message: "Monthly credit budget exhausted. Emergency reserve only."
    
    - name: warn_high_cost
      provider: "*"
      condition: "estimated_cost > 50"
      action: warn
      message: "High-cost operation ({estimated_cost} credits). Verify necessity."
    
    - name: default_allow
      provider: "*"
      condition: "*"
      action: allow
```

### Credit Cost Map (Per-Provider)

```yaml
credit_costs:
  google-antigravity:
    code_generation: 5          # per task
    code_review: 3              # per review
    research: 8                 # per research task
    omni-flash-4s: 15
    omni-flash-6s: 20
    omni-flash-8s: 25
    omni-flash-10s: 30
    veo-fast-any: 10
    veo-quality-8s: 100         # HIGH — always requires approval
    veo-quality-10s: 120        # HIGH — always requires approval
  
  claude-code:
    code_generation: ~0.015     # approximate USD per task
    code_review: ~0.008
    research: ~0.025
  
  github-copilot:
    code_generation: 0          # included in subscription
    code_review: 0
  
  local-llm:
    code_generation: 0          # electricity cost (~$0.50/hr GPU)
    code_review: 0
    context_warning_pct: 80     # warn when context hits 80%
```

### Credit Tracking Implementation

Based on the existing `BudgetManager.ts` pattern:

```typescript
// Extend UsageTelemetry targetHead to include new providers
export interface UsageTelemetry {
    threadId: string;
    agentRole: string;
    targetHead: 'Antigravity UI' | 'Headless API' | 'Local AI' | 'GitHub Jules CLI (jules.google.com)' 
               | 'Claude Code' | 'GitHub Copilot' | 'Local 70B';
    cloudTokensUsed: number;
    localContextFilled: number;
    localContextMax: number;
    budgetLimit: number | null;
    estimatedCost: number;          // NEW: pre-flight cost estimate
    sessionTotal: number;           // NEW: running session total
    monthlyTotal: number;           // NEW: running monthly total
}

// Policy evaluation — runs BEFORE tool execution (like Google SDK's Decide hooks)
public evaluatePolicy(threadId: string, estimatedCost: number): PolicyDecision {
    const telemetry = this._telemetry.get(threadId);
    if (!telemetry) return { action: 'allow' };
    
    // Check hard stop first (emergency reserve)
    if (telemetry.monthlyTotal >= this._globalPolicies.hard_stop_at) {
        return { action: 'deny', reason: 'Monthly budget exhausted' };
    }
    
    // Check per-task cap
    if (estimatedCost > this._globalPolicies.per_task_max) {
        return { action: 'deny', reason: 'Exceeds per-task limit' };
    }
    
    // Check per-session cap
    if (telemetry.sessionTotal + estimatedCost > this._globalPolicies.per_session_max) {
        return { action: 'deny', reason: 'Exceeds session limit' };
    }
    
    // Warn on high-cost operations
    if (estimatedCost > 50) {
        return { action: 'warn', reason: `High cost: ${estimatedCost} credits` };
    }
    
    return { action: 'allow' };
}
```

## Measurable Success Metrics

Every pipeline run produces these metrics. Tracked in Linear issues and the Prismatic Engine observability layer.

### Per-Task Metrics

| Metric | Definition | Target |
|--------|-----------|--------|
| time_to_self_validate | Minutes from first commit to self-validation pass | < 15 min |
| peer_review_depth | Number of specific findings in review report | ≥ 3 per 100 lines |
| fix_cycle_count | Number of fix→review→fix cycles | ≤ 2 |
| time_to_approval | Minutes from first commit to orchestrator approval | < 60 min |
| post_publish_findings | Issues found AFTER publishing | 0 (goal) |
| credit_cost | Total credits consumed | < per_task_max |

### Per-Session Metrics

| Metric | Definition | Target |
|--------|-----------|--------|
| tasks_completed | Number of issues moved to Done | ≥ 3 |
| review_acceptance_rate | % of reviews that pass on first submission | > 60% |
| credit_efficiency | Credits per completed task | < 100 |
| orchestrator_touch_points | Number of times Fred intervened | ≤ 2 (deadlocks only) |
| loop_restart_count | Number of full validation loop restarts | ≤ 1 |

### Pipeline Health Dashboard

```yaml
# Generated after each session
pipeline_health:
  phase1_development:
    active: true
    stages:
      worker_implementation: green
      self_validation: green
      peer_review: green
      second_witness: green      # Independent architecture review
      orchestrator_approval: green
    self_validation_pass_rate: 85%
    avg_review_depth: 4.2
    bottleneck: none
  
  phase2_post_publish:
    active: true
    jules_triggers_today: 3
    avg_fix_cycles: 1.2
    credit_cost_today: 145
  
  provider_status:
    google-antigravity: green (7/7 concurrent, 52% CPU)
    ned-cron: green (every 7 min)
    jules-monitor: green (every 4h)
    second-witness-cron: green (every 30 min)
    local-llm: not_configured
```

## PRISMATIC_ENGINE.yaml Complete Schema

The full schema with all new sections integrated:

```yaml
# PRISMATIC_ENGINE.yaml — Complete Schema
# Deploy to every repository root

version: "2.0"

# === Agent Lanes (existing) ===
lanes:
  - agent: fred
    role: orchestrator
    write: [src/, infra/, deploy/, .github/]
    read_only: [content/, active-oahu/]
    branch_prefix: fred/
    commit_prefix: "[Fred]"
    
  - agent: agy
    role: research_strategist
    write: [docs/, research/, assets/]
    read_only: [src/, content/]
    branch_prefix: agy/
    commit_prefix: "[AGY]"
    
  - agent: ned
    role: primary_executor
    write: [src/, tests/, scripts/]
    read_only: [docs/, content/]
    branch_prefix: ned/
    commit_prefix: "[Ned]"
    
  - agent: jules
    role: post_publish_validator
    write: [docs/, .github/]
    read_only: [src/, content/]
    branch_prefix: jules/
    commit_prefix: "[Jules CLI (jules.google.com)]"

# === Providers (NEW) ===
providers:
  default: google-antigravity   # primary provider
  fallback_chain:               # ordered fallback if primary fails
    - claude-code
    - local-llm
  
  google-antigravity:
    type: google-antigravity
    auth: {method: oauth, keyring: true, fallback_env: GOOGLE_AI_CREDENTIALS}
    capabilities: [code_generation, code_review, research, asset_generation, multi_agent, vision]
    credit_model:
      provider: google-flow
      monthly_budget: 10000
      cost_map:
        code_generation: 5
        code_review: 3
        research: 8
        omni-flash-4s: 15
        veo-quality-8s: 100
      hard_limit: 9000
    invocation:
      binary: agy
      research_mode: "--print"
      implementation_mode: "--prompt-interactive --add-dir <repo>"
      pty_required: true
      max_concurrent: 7
      print_timeout: 300
      
  claude-code:
    type: claude-code
    auth: {method: api_key, env_var: ANTHROPIC_API_KEY}
    capabilities: [code_generation, code_review, research]
    credit_model: {provider: anthropic, monthly_budget: null}
    invocation:
      binary: claude
      research_mode: "-p"
      implementation_mode: ""
      pty_required: true
      max_concurrent: 3
      
  github-copilot:
    type: github-copilot
    auth: {method: oauth, env_var: GITHUB_TOKEN}
    capabilities: [code_generation, code_review]
    credit_model: {provider: github, monthly_budget: null}
    invocation:
      binary: copilot
      mode: "--acp --stdio"
      pty_required: false
      max_concurrent: 1
      
  local-llm:
    type: local-llm
    auth: {method: none}
    capabilities: [code_generation, code_review, research]
    credit_model:
      provider: local
      monthly_budget: null
      context_limit: 8192
    invocation:
      endpoint: http://localhost:8080/v1
      pty_required: false
      max_concurrent: 1

# === Pipeline Stages (NEW) ===
pipeline:
  phase1_development:
    stages:
      - name: worker_implementation
        provider_role: implementer
        mode: implementation
        validation: self_validation_loop
        
      - name: self_validation_loop
        provider_role: implementer
        mode: self_check
        steps: [build, test, fix, retest]
        max_iterations: 3
        gate: all_tests_pass
        
      - name: peer_review
        provider_role: reviewer
        mode: read_only
        outputs: [review_report.md, gap_analysis.md, suggested_fixes.md]
        verdicts: [APPROVED, NEEDS_FIXES, REJECTED]
        
      - name: fix_application
        provider_role: implementer
        mode: implementation
        inputs: [peer_review_report]
        validation: self_validation_loop
        
      - name: orchestrator_approval
        role: fred
        mode: verification
        checks: [findings_addressed, build_passes, tests_pass, lanes_clean, locks_released]
        action: [MERGE_TO_STAGING, RETURN_TO_WORKER]
        
  phase2_post_publish:
    trigger: github_push_detected
    stages:
      - name: active_review
        provider_role: implementer
        mode: implementation
        scope: fixes_only
        
      - name: read_only_review
        provider_role: reviewer
        mode: read_only
        constraint: NO_CORE_FILE_CHANGES
        
      - name: apply_suggestions
        provider_role: implementer
        mode: implementation
        inputs: [read_only_review_report]
        
      - name: publish_and_validate
        role: jules
        action: [RESTART_LOOP, STOP]

# === Policies (NEW) ===
policies:
  global:
    monthly_budget: 10000
    hard_stop_at: 9000
    per_task_max: 500
    per_session_max: 2000
    
  rules:
    - name: block_veo_quality_over_8s
      provider: google-antigravity
      condition: "engine == 'veo-quality' && duration > 8"
      action: deny
      
    - name: human_approval_veo_quality
      provider: google-antigravity
      condition: "engine == 'veo-quality'"
      action: ask_user
      
    - name: block_over_budget_task
      provider: "*"
      condition: "estimated_cost > per_task_max"
      action: deny
      
    - name: hard_stop_monthly
      provider: "*"
      condition: "monthly_total >= hard_stop_at"
      action: deny
      
    - name: default_allow
      provider: "*"
      condition: "*"
      action: allow
```

## Implementation Guide

### 1. Configure Providers

In your project's `PRISMATIC_ENGINE.yaml`, declare which providers are available. The pipeline auto-selects based on task type:

```yaml
# Minimal config — Google AI Ultra only
providers:
  default: google-antigravity
```

```yaml
# Budget-conscious — local 70B for code review, Claude for implementation
providers:
  default: claude-code
  fallback_chain: [local-llm]
  stage_overrides:
    peer_review: local-llm        # use local for reviews (free)
    worker_implementation: claude-code  # use Claude for building
```

```yaml
# GitHub Copilot shop — Microsoft ecosystem
providers:
  default: github-copilot
  stage_overrides:
    research: claude-code          # Copilot can't research
    asset_generation: google-antigravity  # only Gemini does media
```

### 2. Set Credit Budgets

```yaml
policies:
  global:
    monthly_budget: 10000
    hard_stop_at: 9000
    per_task_max: 500
```

The policy engine evaluates BEFORE every tool call. High-cost operations (veo-quality, long research) prompt for human approval. Budget tracking persists in `swarm_locks.json` → `budget_state`.

### 3. Run the Pipeline

```bash
# Phase 1: Development (triggered by Linear issue dispatch)
# Worker picks up issue → implements → self-validates → peer review → fixes → AGY approves

# Phase 2: Post-Publish (triggered by GitHub push)
# Jules CLI (jules.google.com) detects push → AGY reviews → Ned reviews → AGY fixes → publish → Jules CLI (jules.google.com) validates
```

### 4. Measure Results

After each session, the pipeline produces a health dashboard:

```yaml
pipeline_run_2026-06-12_14:30:
  tasks_completed: 4
  avg_review_depth: 5.2
  fix_cycle_count: 1.1
  credit_cost: 127
  orchestrator_touch_points: 1
  verdict: healthy
```

## Provider Playbooks (Runnable Command Reference)

The YAML provider configs above define the *contract*. For the exact **runnable commands** — bash one-liners per provider per pipeline stage, PTY requirements, model selection, tmux orchestration patterns, and a full decision tree for provider selection — see the playbook files committed in the Prismatic Engine repo:

| File | Covers |
|------|--------|
| `docs/provider-playbook-google-antigravity.md` | AGY commands, credit costs, Veo policies |
| `docs/provider-playbook-claude-code.md` | Claude Code print/interactive, tmux, model selection |
| `docs/provider-playbook-local-llm.md` | llama.cpp/vLLM curl patterns, context management |
| `docs/provider-playbook-github-copilot.md` | Copilot ACP, Codex/OpenCode fallbacks |
| `docs/provider-swap-decision-tree.md` | Decision flowcharts, cost matrices, capability matrix |

These were built from this skill + the `claude-code`, `codex`, and `opencode` skills (GRO-1477, Jun 2026). Load them when you need the exact command to invoke a provider, not the YAML contract.

## Plugin Hub Integration (Phase 2 — June 2026)

The Prismatic Plugin Hub (`specs/plugin-hub-architecture.md`) extends the validation pipeline with hardware-aware gating and a media event bus. It sits BETWEEN the dispatcher and generation APIs — a parallel service, not inside the dispatcher.

### New Pipeline Components

**Compute Governor Gate (Stage 2.5):** Before ANY media generation task executes, the VRAM Governor validates hardware availability:
```python
# Added between self-validation and peer review
allocation = await governor.request_allocation(
    plugin_name="veo_video",
    vram_mb=8000,
    priority=AllocationPriority.REALTIME,
)
if allocation is None:
    raise HardwareInadequateError("No GPU available for VeoVideo")
```

**Credit-Aware GPU Allocation:** The Governor consults `credit_policy_engine` BEFORE allocating VRAM. If the monthly Flow budget (<$25K) would be exceeded, the call is rejected with `CREDIT_EXHAUSTED` before GPU resources are wasted.

**Media Event Bus → Telemetry:** `asset.character.completed`, `video.clip.rendered`, `gpu.pressure` events stream to the Shared State Provider and into `telemetry.py`'s new `plugin_executions` table.

**Plugin Lifecycle Enforcement:** Every plugin inherits `PrismaticHubPlugin` ABC with mandatory hooks:
- `on_plugin_load()` — register event bus subscriptions, validate API credentials
- `validate_hardware_requirements()` — verify GPU, VRAM, CUDA before ANY generation
- `stream_execution_pipeline()` — async iterator yielding PipelineStep updates
- `graceful_teardown()` — idempotent resource release

**Starvation Regression Mandate:** Every plugin ships `test_starvation.py` — mocks 1×24GB responsive, launches all plugins simultaneously, verifies priority queue (REALTIME > HIGH > NORMAL > LOW) works without OOM.

See also: `specs/plugin-hub-architecture.md` (16.8KB full specification), GRO-1593 (Architect Core Plugin Hub & VRAM Resource Governor).

The full pipeline ran end-to-end on Darius Star: Ned built 5 features (GRO-1468–1472) → AGY peer-reviewed with 5 structured audit reports → Fred routed findings to Ned (GRO-1473 mobile fix, GRO-1480 audio manifest) → 2 of 5 original issues approved retroactively, 2 sent back for fixes, 1 (sprites) proven already working by AGY despite Fred's initial negative diagnosis.

The pattern: Worker commits → Peer reviewer reads ALL files, searches ALL call sites → produces line-numbered reports with verdicts → Orchestrator routes to fix or approve.

## Pitfalls

- **PRISMATIC_ENGINE.yaml lane chicken-and-egg (NEW Jun 2026):** The governance file `PRISMATIC_ENGINE.yaml` at repo root is NOT in any agent's lane — so no agent can push changes to it without `--no-verify`. This blocks adding new directories (like `specs/`) to any agent's lane. **Fix:** one-time `git push --no-verify` to push the lane config change, then subsequent pushes work normally. **Prevention:** ensure `PRISMATIC_ENGINE.yaml` is in the staging governor's lane (Fred: add repo root `.` or the file explicitly to the owner list). Once fixed, remove the `--no-verify` bypass.

- **Silent validator crashes block pipeline transitions (Jun 2026):** The `agent_output_validator.py` crashed on `ValidationResult.__init__() missing passed param` — the `passed: bool` field had no default value. This prevented AGY→Fred label transitions for 3 completed tasks (GRO-1180, 1223, 1233), all with result files on disk (46KB, 6.5KB, 72KB). Detection: multiple AGY issues stuck In Progress with `agent:agy` label and `/tmp/agy-dispatch-GRO-XXX-result.md` files present. Fix: (a) verify validator syntax, (b) add defaults to dataclass fields, (c) manually transition stuck issues to Done. Root cause: `ValidationResult(issue_id=identifier)` called without the required `passed` argument.

- **Negative findings need cross-file verification:** When you claim something is "never called," "not loaded," or "missing," search ALL invocation sites across the entire codebase before concluding. Grep for the function/variable name in every JS/TS/PY file. AGY's sprite audit proved the loading functions were called from `ui.js` and `game_loop.js` — files Fred didn't check. Negative claims are the most likely to be wrong.
- **Provider lock-in:** Always configure at least one fallback provider. If AGY OAuth expires mid-session, the pipeline falls back to Claude or local without human intervention.
- **Credit exhaustion:** The hard_stop_at 90% rule ensures emergency reserve. Don't set it to 100% — a rogue loop can burn credits faster than human detection.
- **Self-validation skip:** Never allow workers to skip self-validation. A worker claiming "builds fine" without evidence is the #1 source of broken staging.
- **Peer review by same provider:** Use a DIFFERENT provider/model for peer review when possible. Same-model review is blind to its own failure modes. AGY implements → Claude reviews → local validates.
- **Infinite loops:** Max 3 self-validation iterations per task, max 3 full post-publish loops. Escalate to orchestrator after that.
- **Silent failures:** Every pipeline stage logs to `pipeline_run_<timestamp>.json`. If a stage fails silently (AGY timeout, local GPU OOM), the orchestrator detects the missing log and escalates.
- **Local 70B context overflow:** At 80% context, the policy engine warns. At 95%, it hard-stops. Local models silently truncate, producing corrupted output.
- **Credit cost of review vs rework:** A cheap review that misses problems costs more in rework than an expensive review that catches them. Default to the strongest reviewer available, not the cheapest.

- **Second Witness context file scope drift (Jun 2026):** The context file at `specs/second-witness-context.md` hardcodes a review scope (e.g., GRO-1493–1500). As the project grows (50 issues in this session vs the original 8), the context file's issue range becomes stale. **Pattern:** When the specified scope is fully Done but the project has "In Review" issues outside that range, the Second Witness should: (1) scan the FULL project via `project(id:...) { issues { nodes { ... } } }`, (2) flag ANY "In Review" issues found — both in-scope and out-of-scope, (3) include a "Context File Update Needed" notice in the report when out-of-scope review candidates are found. **Do NOT skip out-of-scope "In Review" issues just because the context file doesn't list them.** The context file is a snapshot, not a contract — the review mandate is for the PROJECT, not a fixed issue range.\n- **Dual-project detection (Jun 2026):** A venture may have MULTIPLE Linear projects with the same name (e.g., two "Prismatic Engine" projects with different IDs). The context file's hardcoded project ID may be stale or reference only one of them. **Pattern:** query ALL team projects via `team(id:...){ projects{ nodes{ id name } } }` → filter by name → query both. Never assume the first `project(id:)` hit covers all issues for that venture. In one session: project `2eb2913f` had Phase 2/3 epics; project `747b3ea8` had the original Phase 1 scope and fix issues. Both needed scanning to produce a complete review.

- **Multi-issue PR detection (Second Witness, Jun 2026):** When reviewing a PR linked to a single issue, check `gh pr view <N> --json commits` for commits referencing OTHER issue numbers (`GRO-XXXX` in commit messages). Flag these in the review under a "PR Hygiene" note. Multi-issue PRs violate one-PR-per-issue discipline and complicate rollback. Not a blocker for approval (the code may be correct) but must be noted. Example: PR #6 for GRO-1567 contained 5 commits spanning GRO-1567, GRO-1583, GRO-1584, GRO-1578, GRO-1591.
