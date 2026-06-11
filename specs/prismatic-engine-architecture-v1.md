# Prismatic Engine — Architecture Spec v1

**Date:** 2026-06-07  
**Author:** Kai (Hermes Swarm)  
**Status:** Draft — pending AGY review  

---

## Overview

The Prismatic Engine governs how multiple AI agents collaborate on the same git repository without stepping on each other. It composes patterns from the Antigravity Orchestration Hub (VS Code extension), GitHub research (claude-swarm, agit, dist-space, FSBerlin), and Hermes agent profiles into a coherent protocol.

---

## Layer 1: Lane Assignment

Every agent is assigned a **lane** — a set of directories it owns and a branch prefix it uses.

### Default Lane Map

| Agent | Role | Lane (owned directories) | Branch Prefix | Read-Only |
|-------|------|-------------------------|---------------|-----------|
| Fred | Orchestrator/Infra | `src/`, `infra/`, `deploy/`, `.github/` | `feature/` or `infra/` | `content/` (read-only) |
| Kai | Content Writer | `content/`, `active-oahu/` | `content/` | `src/` (read-only) |
| AGY | Designer/Researcher | `assets/`, `designs/`, `research/` | `design/` | Everything else (read-only) |
| Jules | PR Agent | no direct edits — only PRs | `fix/` | Everything (read-only, code review only) |

### Governance File

Each repo contains `PRISMATIC_ENGINE.yaml` at root:

```yaml
version: 1
agents:
  fred:
    lanes:
      - owner: ["src/", "infra/", "deploy/", ".github/"]
      - read_only: ["content/"]
    branch_prefix: "feature/"
  kai:
    lanes:
      - owner: ["content/", "active-oahu/"]
      - read_only: ["src/"]
    branch_prefix: "content/"
  agy:
    lanes:
      - owner: ["assets/", "designs/", "research/"]
      - read_only: ["src/", "content/"]
    branch_prefix: "design/"
  jules:
    lanes:
      - owner: []  # no direct edits
      - read_only: ["*"] # everything
    branch_prefix: "fix/"

locks:
  file: ".antigravity/swarm_locks.json"
  heartbeat_ttl_ms: 300000  # 5 min stale timeout

staging:
  governor: "fred"  # only this agent merges to staging
  branch: "deploy-fresh"
```

---

## Layer 2: File Claim System

### How It Works

1. Before editing any file, an agent **claims** it via `swarm.js`:
   ```
   node .antigravity/swarm.js lock content/tours/mokulua.md kai
   ```

2. If the file is already locked by another agent → ERROR, agent must wait and retry.

3. While holding the lock, the agent can edit freely. Other agents see the file as "claimed."

4. After editing, the agent **releases** the lock:
   ```
   node .antigravity/swarm.js unlock content/tours/mokulua.md kai
   ```

5. **Heartbeat**: Every agent pings its locks every 60s. If an agent hasn't pinged in 5 minutes, all its locks are auto-released (stale lock cleanup).

### Lock Entry Schema

```json
{
  "filePath": "content/tours/mokulua.md",
  "agentId": "kai",
  "timestamp": 1749326400000,
  "lastHeartbeat": 1749326460000
}
```

---

## Layer 3: Branch Governance

### The Flow

```
Kai:     content/add-mokulua-page → push → ...
Fred:    feature/update-nav       → push → ...
AGY:     design/new-logo          → push → ...

Staging (deploy-fresh):
  ONLY Fred merges into this branch.
  Content and design branches are merged by Fred after review.

Production (main):
  ONLY from deploy-fresh, after Michael approves.
```

### Rules

1. **Content agents (Kai):** Must work in `content/*` branches off `deploy-fresh`. Never push directly to `deploy-fresh` or `main`.

2. **Code agents (Fred):** Must work in `feature/*` or `infra/*` branches off `deploy-fresh`.

3. **Design agents (AGY):** Must work in `design/*` branches off `deploy-fresh`.

4. **PR agents (Jules):** Creates PRs from agent branches to `deploy-fresh`. Never pushes directly.

5. **Only Fred** may merge to `deploy-fresh` (the staging governor).

6. **Pre-push hook** (installed per repo):
   - Rejects push to `deploy-fresh` if agent is not Fred
   - Rejects push to `main` always (manual only)
   - Validates that all changed files are within agent's lane
   - Checks swarm_locks for conflicts

---

## Layer 4: Agent Identity in Git

Every commit carries agent attribution:

1. **Commit message prefix**: `[Kai] Add mokulua islands tour page`
2. **git config**: Each Hermes profile sets its own `user.email` (e.g., `kai@activeoahutours.com`)
3. **Pre-push validation**: The hook verifies the agent prefix matches the actual agent pushing

---

## Layer 5: Conflict Predictor

Before any push to a shared branch:

1. **Pull latest** from `deploy-fresh` or target base
2. **Diff** each changed file against the base branch version
3. **Check** `swarm_locks.json` — does another agent hold a lock on any changed file?
4. **Check** git log — did another agent commit changes to the same files since our last pull?
5. **Report**:
   - ✅ If clean → proceed
   - ❌ If conflict detected → BLOCK, print report:
     ```
     CONFLICT DETECTED:
     - src/components/Nav.tsx: Fred modified this file after your last pull.
       Run: git pull origin deploy-fresh and reconcile.
     - content/tours/chinamans-hat.md: No conflict. Proceed.
     ```

---

## Layer 6: Heartbeat + Grey Release

### Heartbeat

- Each agent runs a background ping to the lock registry every 60s
- `swarm_locks.json` updated with `lastHeartbeat` timestamp
- Stale lock watcher (cron job) runs every 2 minutes:
  - Scans all locks
  - If `now - lastHeartbeat > 300000` (5 min) → release lock
  - Logs: `[Prismatic Engine] Released stale lock on content/tours/mokulua.md held by kai`

### Grey Release

- Content changes pushed to staging are visible immediately on preview URL
- Fred must verify before promoting to production
- If a change breaks staging, Fred can reject the merge
- Agent is notified: `[Prismatic Engine] Your content/changes-to-nav.md was rejected. Reason: broke mobile nav.`

---

## Implementation Phases

### Phase 1: Convention (now)
- Write `PRISMATIC_ENGINE.yaml` for each repo
- Document lane assignments in each agent's SOUL.md
- Implement commit message prefixes manually
- Branch discipline: agents use `content/*`, `feature/*`, `design/*`

### Phase 2: Pre-push Hooks (next)
- Install `pre-push` hook in each repo
- Validates:
  - Agent lane ownership of changed files
  - Branch naming convention matches agent
  - Staging merge governor rule
- Test against active-oahu repo

### Phase 3: Lock Integration (soon)
- Adapt `swarm.js` for Hermes agent use (not VS Code)
- Add heartbeat pings to each agent's startup
- Add stale lock cleanup cron job
- Connect lock checking to pre-push hook

### Phase 4: Conflict Predictor (later)
- Full pre-push diff analysis against staging
- Automatic conflict reports
- Suggested reconciliation steps

### Phase 5: Dashboard (future)
- Realtime swarm lock visualization
- Agent activity log
- Merge governance dashboard
