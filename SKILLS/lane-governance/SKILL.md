---
name: lane-governance
description: >-
  Set up and enforce Prismatic Engine lane governance — agent lane ownership,
  file locking via swarm CLI, pre-push git hooks, PRISMATIC_ENGINE.yaml
  configuration, branch conventions, and commit message prefix enforcement.
  Prevents agent collision and unauthorized file modifications.
---

# Lane Governance

## Trigger
Load this skill when setting up lane governance for a new Prismatic Engine
workspace, adding a new agent lane, or debugging lane violation errors.

## Overview

Lane governance prevents agent collisions by assigning each agent exclusive
write access to specific directories and enforcing those boundaries through
file locking and git hooks.

## Lane Architecture

```
Workspace Root
├── PRISMATIC_ENGINE.yaml     ← Lane definitions (governed by Fred)
├── src/                       ← Ned: write, Jules: write
├── infra/                     ← Ned: write
├── deploy/                    ← Ned: write
├── content/                   ← AGY: write, Kai: write
├── active-oahu/               ← Kai: write
├── docs/                      ← AGY: write, Jules: write
├── .github/                   ← Ned: write
├── scripts/                   ← Ned: write
├── prismatic/                 ← Fred: write
└── plugins/                   ← Ned: write
```

## PRISMATIC_ENGINE.yaml

```yaml
# PRISMATIC_ENGINE.yaml — single source of truth for lane governance
version: "1.0"
lanes:
  agent:ned:
    write: ["src/", "infra/", "deploy/", ".github/", "scripts/", "plugins/"]
    read_only: ["content/", "active-oahu/", "docs/"]
    branch_prefix: "feature/"
    commit_prefix: "[Ned]"
  agent:fred:
    write: ["prismatic/", "SKILLS/", "templates/"]
    read_only: []
    branch_prefix: "fred/"
    commit_prefix: "[Fred]"
    role: orchestrator
  agent:agy:
    write: ["content/", "docs/", "reports/", "research/"]
    read_only: ["src/", "infra/", "deploy/"]
    branch_prefix: "agy/"
    commit_prefix: "[AGY]"
  agent:kai:
    write: ["active-oahu/", "content/tours/"]
    read_only: ["src/", "infra/"]
    branch_prefix: "kai/"
    commit_prefix: "[Kai]"
  agent:jules:
    write: ["src/", "docs/"]
    read_only: ["infra/", "deploy/"]
    branch_prefix: "jules/"
    commit_prefix: "[Jules]"
```

## File Locking Protocol

### Lock a File Before Editing

```bash
# Lock a file
python3 /home/ubuntu/.antigravity/swarm.js lock <repo-relative-path> <agent>

# Example
python3 /home/ubuntu/.antigravity/swarm.js lock src/nav.js ned
```

### Unlock After Committing

```bash
# Unlock
python3 /home/ubuntu/.antigravity/swarm.js unlock <repo-relative-path> <agent>

# Example
python3 /home/ubuntu/.antigravity/swarm.js unlock src/nav.js ned
```

### Lock File Location
- Path: `/home/ubuntu/.antigravity/swarm_locks.json`
- Heartbeat: every 60 seconds
- Stale TTL: 5 minutes (locks auto-expire if heartbeat stops)

## Branch Convention

- **Write-agent branches:** Start with `feature/` prefix, e.g.,
  `feature/fix-nav-routing`
- **Orchestrator branches:** Start with agent prefix, e.g., `fred/review-GRO-1234`
- **Never push directly to `main`** — production deployments are
  manual-only via the Staging Governor (Fred).
- **Only Fred may merge PRs** into staging/production.

## Commit Message Prefix

All commits MUST be prefixed with the agent identifier and issue reference:

```
[Ned] Fix nav routing for mobile breakpoints (#GRO-1234)
[AGY] Add competitor audit report for Hawaii tour operators (#GRO-1235)
[Fred] Merge feature/fix-nav-routing into main (#GRO-1234)
[Jules] Implement SwarmPlanner contract generation (#GRO-1236)
[Kai] Add Kaneohe sandbar tour page with schema (#GRO-1237)
```

## Pre-Push Hook

The pre-push hook enforces:
1. **No direct pushes to `main`** — blocked with message: "Production
   deployments are manual-only. Use deploy-fresh for staging."
2. **Lane ownership validation** — agent can only push files in their
   assigned write directories.
3. **Commit message prefix check** — all commits must have the correct
   `[Agent]` prefix and issue reference.

### Hook Location
`/home/ubuntu/work/<repo>/.git/hooks/pre-push`

### Installing the Hook
```bash
# In the workspace repo
cp /home/ubuntu/work/prismatic-engine/scripts/pre-push-hook.py \
   .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

## Adding a New Agent Lane

### 1. Update PRISMATIC_ENGINE.yaml
```yaml
lanes:
  agent:newagent:
    write: ["new-domain/"]
    read_only: ["src/", "content/"]
    branch_prefix: "newagent/"
    commit_prefix: "[NewAgent]"
```

### 2. Create Agent SOUL Template
Copy from `prismatic/templates/profiles/ned/SOUL.md` and customize.

### 3. Register in Dispatcher
Add `signal_newagent()` function and registration entries in
`agent_dispatcher.py` (4 changes required).

### 4. Update Pre-Push Hook
Add the new agent to the lane validation logic in `pre-push-hook.py`.

### 5. Test
```bash
# Attempt a lane violation — should be blocked
git checkout -b newagent/test-push
echo "test" >> src/protected.js
git add src/protected.js
git commit -m "[NewAgent] Test lane violation"
git push origin newagent/test-push  # Should FAIL
```

## Pitfalls

- ❌ **Direct push to main:** Blocked by pre-push hook. Use `feature/`
  branches and merge via Fred.
- ❌ **Pushing outside owned lane:** Hook will reject. Check
  `PRISMATIC_ENGINE.yaml` for your lane's write directories.
- ❌ **Missing commit prefix:** Hook will reject. Format:
  `[Agent] description (#ISSUE)`.
- ❌ **Stale locks:** If a lockfile exists for >5 minutes without a heartbeat,
  it auto-expires. Check `/home/ubuntu/.antigravity/swarm_locks.json` if
  you get unexpected lock conflicts.
- ❌ **Bypassing with `--no-verify`:** Only for Phase 1 governance-layer
  initialization. After the initial push, never use `--no-verify`.

See also: `prismatic-7-step-loop` skill, `agent-soul-template` skill.
