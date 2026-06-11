# Prismatic Engine — Core Architecture Evaluation

**Author:** Kai (Content Agent, Hermes Swarm)  
**Date:** 2026-06-07  
**Status:** Final — Reviewed by AGY  

---

## 1. The Problem Space

Multi-agent git workspace coordination is unsolved in open source. No repo fully prevents multiple AI agents from stepping on each other when editing the same repo.

**This is the problem Prismatic Engine solves.**

---

## 2. Key Realizations

### Dispatch alone isn't enough
Fred's dispatcher routes issues to agents and signals them to wake up. But once two agents are both working on the same repo, dispatch can't prevent:
- Two agents editing the same file → merge conflict
- Content agent changing a page whose URL a code agent renamed → broken links
- Force-push destroying another agent's work

**Dispatch answers "who does what." Governance answers "how do they not break each other."**

### The Antigravity Hub already has the governance pattern
Your VS Code extension has `SwarmLockManager` (file mutex), `ContractManager` (lane scoping with `allowedDirectories`), and `HandoffProtocol` (auto-git-snapshot). These just need to be adapted from TypeScript/VS Code to headless Hermes agents.

### AGY found bugs in my v1 architecture
- **Absolute paths** would fail if agents work in different checkout directories
- **Per-checkout lock files** would isolate lock registries so agents can't see each other's claims
- **Semantic dependencies** — modifying a shared type file can break an agent that never touched it

### Visibility is Core, not a bonus
Without a dashboard showing who holds what lock, which agent is running, and what's blocked, you're flying blind. Your SwarmWebview in the Antigravity Hub already does this — it just needs to work outside VS Code.

---

## 3. Core Architecture (What Actually Ships)

The Prismatic Engine has exactly **four core subsystems**:

```
┌─────────────────────────────────────────────────────────────┐
│                     PRISMATIC ENGINE                         │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │  1. DISPATCH      │  │  2. GOVERNANCE    │                │
│  │  (Fred's code)    │  │  (Kai's pattern)  │                │
│  │                   │  │                   │                │
│  │  Poll Linear      │  │  Lane assignment   │                │
│  │  Route by label   │  │  File claims       │                │
│  │  Signal agent     │  │  Branch protocol   │                │
│  │  Track completion │  │  Pre-push hooks    │                │
│  └──────────────────┘  └──────────────────┘                │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │  3. VISIBILITY    │  │  4. REFINEMENT    │                │
│  │  (Trust layer)    │  │  (Loop layer)     │                │
│  │                   │  │                   │                │
│  │  Lock dashboard   │  │  7-step loop      │                │
│  │  Agent feed       │  │  Review gates     │                │
│  │  Stale alerts     │  │  Mode switch      │                │
│  │  Run history      │  │  (interactive→    │                │
│  │                   │  │   collaborative→  │                │
│  │                   │  │   autonomous)     │                │
│  └──────────────────┘  └──────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

Everything else—Design Studio plugin, Content Studio plugin, Code Review plugin—is a **bolt-on** that chains these four core capabilities.

---

## 4. Core vs Plugin — The Boundary

### Core (ships with prismatic-engine)
These make orchestration **possible** regardless of tooling:

| Feature | Why It's Core |
|---------|--------------|
| Task Provider (Linear/GitHub/Jira) | Must read work from somewhere |
| Signal Provider (file/HTTP/Redis/Telegram) | Must wake agents somehow |
| Router (label-based pipelines) | Must know which agent does what |
| Workspace Governance (lanes, locks, branches) | Must prevent collisions |
| Agent Identity (commit attribution) | Must know who did what |
| Visibility Dashboard | Must trust what you can't see |
| Iterative Refinement Loop (7-step) | Must iterate autonomously |
| Orchestration Mode Switch | Must adapt to human context |

### Plugin (bolt-on marketplace)
These are **applications** of the core engine:

| Plugin | Agent Chain | Example Use |
|--------|------------|-------------|
| **Design Studio** | AGY→Codex→Jules | UI/branding pipeline |
| **Content Studio** | Kai→Fred | SEO content pipeline |
| **Code Review** | Jules→Codex→Fred | PR automation |
| **Research Synthesizer** | AGY→Kai→Fred | Research→docs |
| **Your custom plugin** | Any chain | Your workflow |

---

## 5. The 7-Step Iterative Loop

This is the Claude Code "build it in 12 hours" mechanism, generalized for multi-agent:

```
┌────────────────────────────────────────────────────────┐
│             THE 7-STEP PRISMATIC LOOP                    │
│                                                         │
│  Step 1: DECOMPOSE                                       │
│    Megaprompt → specialized agent contracts             │
│    (The SwarmPlanner already does this)                  │
│                                                         │
│  Step 2: DISPATCH                                        │
│    Route each contract to the right agent               │
│    (Fred's dispatcher + lane validation)                 │
│                                                         │
│  Step 3: EXECUTE                                         │
│    Agent does its task (scoped by lane + locks)         │
│    (Kai writes content, Fred writes code)               │
│                                                         │
│  Step 4: REVIEW                                          │
│    Specialist reviewer agent checks the work            │
│    (Codex reviews code, AGY reviews design)             │
│                                                         │
│  Step 5: FEEDBACK                                        │
│    Issues found → revision request sent to agent        │
│    (Signal back to original agent with context)         │
│                                                         │
│  Step 6: REFINE                                          │
│    Agent revises based on review feedback               │
│    (Same agent, same lane, new iteration)               │
│                                                         │
│  Step 7: INTEGRATE                                       │
│    Approved work merged, next phase triggered           │
│    OR loop back to Step 4 if not approved               │
│                                                         │
└────────────────────────────────────────────────────────┘
```

**The mode switch** controls how many of these steps require human approval:
- **Interactive**: Human approves Steps 1, 4, 7
- **Collaborative**: Human approves Steps 4, 7 (our current mode)
- **Autonomous**: AI reviews only, human gets final summary

---

## 6. Orchestration Mode Switch

| Mode | Review Depth | Human Involvement | Best For |
|------|-------------|-------------------|----------|
| **Interactive** | Human approves each handoff | High touch | Design sprints, sensitive changes |
| **Collaborative** | Human reviews at breakpoints | Medium touch | Daily dev work (what we do now) |
| **Autonomous** | AI reviewers only | Final summary only | Overnight builds, batch content, deep research |

Same engine. Different gate configuration. The switch is a config value, not a different architecture.

---

## 7. What AGY Caught (Bugs in My v1)

| Issue | My v1 Mistake | AGY's Correction |
|-------|--------------|------------------|
| Lock file location | Per-checkout `.antigravity/swarm_locks.json` | Centralized `/home/ubuntu/.antigravity/swarm_locks.json` |
| Lock path format | Absolute paths (`/home/ubuntu/work/...`) | Repo-relative paths (`content/tours/mokulua.md`) |
| Semantic deps | Not considered | Pre-push hook must detect transitive breakage |
| Stale cleanup | Separate cron job | Lazy pruning during every lock/unlock/status command |

---

## 8. Implementation Priority

```
NOW ─────────────────────────────────────────────────── LATER
│                                                         │
│  Phase 1: Convention (this week)                        │
│  ├── PRISMATIC_ENGINE.yaml in each repo                 │
│  ├── Lane rules in agent SOUL.md                        │
│  ├── Commit message prefixes                            │
│  └── Branch naming convention                           │
│                                                         │
│  Phase 2: Lock Engine (next week)                        │
│  ├── Refactor swarm.js for central + relative paths     │
│  ├── Heartbeat daemon                                   │
│  └── Lazy lock pruning                                  │
│                                                         │
│  Phase 3: Pre-push Hooks (2 weeks)                       │
│  ├── Python git hook script                             │
│  ├── Lane validation                                    │
│  ├── Lock validation                                    │
│  └── Branch validation                                  │
│                                                         │
│  Phase 4: Visibility Dashboard (3 weeks)                 │
│  ├── Lock state display                                 │
│  ├── Agent activity feed                                │
│  └── Stale agent alerts                                 │
│                                                         │
│  Phase 5: 7-Step Loop (4 weeks)                          │
│  ├── Review gate infrastructure                         │
│  ├── Feedback/refine cycle                              │
│  └── Mode switch (interactive→collaborative→autonomous) │
│                                                         │
│  Phase ∞: Plugins                                        │
│  ├── Design Studio                                       │
│  ├── Content Studio                                      │
│  ├── Code Review Pipeline                                │
│  └── Your plugin here                                    │
│                                                         │
```

---

## 9. Key Files Reference

| What | Where |
|------|-------|
| **Prismatic Engine pip package** | `/home/ubuntu/work/prismatic-engine/` |
| **Landing page** | `/home/ubuntu/work/prismatic-engine-site/` |
| **Architecture plan (Fred's)** | `/agentic-swarm-ops/docs/architecture/prismatic-engine-plan.md` |
| **AGY Implementation Plan** | `/agentic-swarm-ops/prismatic-engine/reports/agy-implementation-plan.md` |
| **Antigravity Hub (lock code)** | `/Synology_NAS/Photo/workshop/Antigravity Orchestration Hub/src/engine/SwarmLockManager.ts` |
| **7-step loop pattern (TS)** | Same hub: `SwarmPlanner.ts`, `ContractManager.ts`, `HandoffProtocol.ts` |
| **Test batches** | `/agentic-swarm-ops/prismatic-engine/test-plans/test-batches-v1.md` |
| **Research landscape** | `/agentic-swarm-ops/prismatic-engine/research/01-multi-agent-git-coordination-landscape.md` |
| **This evaluation** | `/agentic-swarm-ops/prismatic-engine/reports/core-evaluation.md` |
