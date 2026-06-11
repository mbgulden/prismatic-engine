# Fred Briefing — Prismatic Engine: Full Integration Plan

**From:** Kai (Content Agent)  
**Date:** 2026-06-07  
**Linear:** GRO-811, GRO-812, GRO-813, GRO-814  
**For:** Fred (Orchestrator)

---

## What's Going On

Michael wants the Prismatic Engine to be the orchestration layer for all our agents. You've already built the **dispatch infrastructure** (poll Linear → route by label → signal agent → track completion). What's missing is:

1. **Workspace Governance** — preventing agents from stepping on each other in the repo
2. **Visibility** — a dashboard so Michael can see what's happening
3. **The 7-Step Iterative Loop** — making "build it, wake me when done" possible
4. **Orchestration Mode Switch** — interactive ↔ collaborative ↔ autonomous

---

## What Already Exists

### Your Work (Dispatch Layer)
| File | What It Does | Status |
|------|-------------|--------|
| `/home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py` | Event loop: poll Linear → route → signal → track | ✅ Complete (1,331 lines) |
| `/home/ubuntu/work/prismatic-engine/prismatic/router.py` | Label-based pipeline routing with YAML config | ✅ Complete |
| `/home/ubuntu/work/prismatic-engine/prismatic/providers/signals/*.py` | Signal providers (File, HTTP, Redis, Telegram) | ✅ Complete |
| `/home/ubuntu/work/prismatic-engine/prismatic/providers/tasks/linear.py` | Linear GraphQL provider | ✅ Complete |
| `/home/ubuntu/work/prismatic-engine/prismatic/agents/hermes.py` | Hermes signal adapter | ✅ Complete |
| `/home/ubuntu/work/prismatic-engine/prismatic/workspace.py` | Workspace registry loader | ✅ Complete |
| `/home/ubuntu/work/prismatic-engine/prismatic/dedup.py` | SQLite dedup database | ✅ Complete |
| `/home/ubuntu/work/prismatic-engine/prismatic/run_records.py` | Agent run tracking | ✅ Complete |

### Antigravity Hub (Governance Pattern — Needs Porting)
| File | What It Does | Needs |
|------|-------------|-------|
| `Synology_NAS/Photo/workshop/.../SwarmLockManager.ts` | File-level mutex locks (`acquireLock`, `releaseLock`, `isLocked`, `getLockOwner`, `releaseAllLocksForAgent`) | Port from TS/VS Code to headless Python or wrap `swarm.js` |
| `.../ContractManager.ts` | AgentContract with `allowedDirectories` + `readOnlyDirectories` — generates enforcement prompts | Embed in system prompts |
| `.../SwarmPlanner.ts` | Decompose megaprompts into specialized worker contracts with lane assignments | Port to Python |
| `.../HandoffProtocol.ts` | Auto-git-snapshots before handoff, thread ancestry tracking | Already good pattern |
| `.../swarm.js` | Node CLI: `node swarm.js lock\|unlock\|status <filepath> <agentId>` | Refactor for central + relative paths |
| `.../swarm_locks.json` | Lock data store (currently empty `[]`) | Populate via swarm.js |

### My Research (Governance Layer)
| File | What It Contains |
|------|-----------------|
| `/agentic-swarm-ops/prismatic-engine/research/01-multi-agent-git-coordination-landscape.md` | Full landscape research: claude-swarm, agit, dist-space, FSBerlin, Clawix |
| `/agentic-swarm-ops/prismatic-engine/specs/prismatic-engine-architecture-v1.md` | My v1 architecture (has bugs — see below) |
| `/agentic-swarm-ops/prismatic-engine/reports/core-evaluation.md` | Full evaluation — core vs plugin, 7-step loop, mode switch |

### AGY's Review
| File | What It Contains |
|------|-----------------|
| `/agentic-swarm-ops/prismatic-engine/reports/agy-implementation-plan.md` | AGY's full review + phased roadmap with Mermaid diagram |
| Linear GRO-812 | 3 comments: Implementation Plan, Summary, Walkthrough |

---

## Bugs AGY Found in My v1 Architecture

These need fixing in your implementation:

| Bug | My Mistake | Fix |
|-----|-----------|-----|
| **Lock paths** | Used absolute paths (`/home/ubuntu/work/...`) | Must use **repo-relative paths** (`content/tours/mokulua.md`) |
| **Lock location** | Per-checkout `.antigravity/swarm_locks.json` | **Centralized** at `/home/ubuntu/.antigravity/swarm_locks.json` (env var `SWARM_LOCKS_DIR`) |
| **Semantic deps** | Not considered | Pre-push hook must detect transitive breakage (imports/exports) |
| **Stale cleanup** | Separate cron job | Lazy pruning during every lock/unlock/status command |

---

## Phased Integration Plan

### Phase 1: Convention (immediate — this week)

**What to do:**
1. Create `PRISMATIC_ENGINE.yaml` in the active-oahu repo (see template below)
2. Add lane constraints to each agent's SOUL.md
3. Standardize commit message format: `[AgentName] description`
4. Enforce branch naming: `content/*`, `feature/*`, `design/*`, `fix/*`

**PRISMATIC_ENGINE.yaml template:**
```yaml
version: 1
agents:
  fred:
    lanes: ["src/", "infra/", "deploy/", "agentic-swarm-ops/"]
    branch_prefix: "feature/"
  kai:
    lanes: ["content/", "active-oahu/"]
    branch_prefix: "content/"
  agy:
    lanes: ["assets/", "designs/", "research/"]
    branch_prefix: "design/"
  jules:
    lanes: []
    branch_prefix: "fix/"
    read_only: true

locks:
  centralized: "/home/ubuntu/.antigravity/swarm_locks.json"
  heartbeat_ttl_ms: 300000

staging:
  governor: "fred"
  branch: "deploy-fresh"
```

### Phase 2: Centralized Lock Engine (next week)

**What to do:**
1. Refactor `.antigravity/swarm.js` to:
   - Use centralized `/home/ubuntu/.antigravity/swarm_locks.json`
   - Resolve all paths to repo-relative (use `git rev-parse --show-toplevel`)
   - Add `heartbeat` command: `node swarm.js heartbeat <agentId>`
   - Implement lazy pruning on every command
2. Embed lock calls in agent startup scripts
3. Write the heartbeat daemon

**The `swarm.js` refactor is already documented here:**
`/agentic-swarm-ops/prismatic-engine/reports/agy-implementation-plan.md` (Section 3 — Hermes Adaptation Strategy)

### Phase 3: Git Hook Validation (medium term)

**What to do:**
Write a Python `pre-push` hook that checks:
1. Branch name matches agent prefix
2. Changed files are within agent's lane
3. Agent holds lock for each changed file
4. Agent identity matches commit prefix

### Phase 4: Visibility Dashboard (medium term)

**What to do:**
Build a simple web endpoint showing:
- Active locks: who holds what
- Active agents: who's running what task
- Stale lock alerts
- Recent run history

Your existing `run_records.py` and `dispatcher.py` already log this data. Just need a display.

### Phase 5: 7-Step Iterative Loop (longer term)

**What to do:**
Build the review→feedback→refine→integrate infrastructure:
1. Review gate (what happens when Step 4 completes)
2. Feedback signal (how to send work back to the original agent)
3. Refine cycle (how many iterations before escalation to human)
4. Mode switch (interactive ↔ collaborative ↔ autonomous)

This is the mechanism that makes "build the thing, wake me when done" possible. Your dispatcher handles Steps 1-3. Steps 4-7 are what's new.

---

## Where Michael's Antigravity Hub Patterns Live

The 7-step loop and role-specialized agent chains are already coded in TypeScript in your VS Code extension. The files to port:

```
/Synology_NAS/Photo/workshop/Antigravity Orchestration Hub/
├── src/engine/SwarmPlanner.ts        # Step 1: decompose megaprompt into contracts
├── src/engine/ContractManager.ts      # Step 2: contracts with lane scoping
├── src/engine/SwarmOrchestrator.ts    # Steps 2-3: route + execute
├── src/engine/HandoffProtocol.ts      # Step 6-7: handoff + lineage tracking
├── src/engine/BudgetManager.ts        # Token budget per agent thread
├── src/engine/LineageManager.ts       # Thread ancestry (who spawned who)
├── src/ui/SwarmWebview.ts            # Visibility dashboard (VS Code panel)
├── .antigravity/swarm.js              # Lock CLI
└── .antigravity/swarm_locks.json      # Lock data store
```

---

## Where My Research Files Live

```
/agentic-swarm-ops/prismatic-engine/
├── research/
│   └── 01-multi-agent-git-coordination-landscape.md
├── specs/
│   └── prismatic-engine-architecture-v1.md         ← has bugs, see AGY review
├── test-plans/
│   └── test-batches-v1.md                           ← 46 tests, 8 batches
└── reports/
    ├── core-evaluation.md                           ← this evaluation
    ├── AGY-briefing.md
    └── agy-implementation-plan.md                   ← AGY's review + roadmap
```

These need to be merged into the main prismatic-engine repo at `/home/ubuntu/work/prismatic-engine/`.

---

## Immediate Next Steps for You

1. **Read AGY's report** → `/agentic-swarm-ops/prismatic-engine/reports/agy-implementation-plan.md`
2. **Fix `swarm.js`** → central locks + relative paths + heartbeat + lazy pruning
3. **Create `PRISMATIC_ENGINE.yaml`** in active-oahu repo
4. **Add lane rules** to each agent's SOUL.md
5. **Move research files** from agentic-swarm-ops into the main prismatic-engine repo

Then come back for Phase 3-5.
