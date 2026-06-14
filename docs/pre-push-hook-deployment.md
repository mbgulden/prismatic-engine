# Pre-Push Hook Deployment (GRO-1561)

## Overview
The Prismatic Engine pre-push hook (`scripts/pre-push-hook.py`) enforces lane governance, file locking, staging governor rules, and main-branch protection on all prismatic-governed repos.

## Deployed Repos (6)
| Repo | Path | Status |
|------|------|--------|
| Prismatic Engine | `/home/ubuntu/work/prismatic-engine` | ✅ Live |
| Active Oahu Tours Mirror | `/home/ubuntu/work/active-oahu-tours-mirror` | ✅ Live |
| Agentic Swarm Ops | `/home/ubuntu/work/agentic-swarm-ops` | ✅ Live |
| OpenHumanDesignMCP | `/home/ubuntu/work/OpenHumanDesignMCP` | ✅ Live |
| Darius Star | `/home/ubuntu/work/darius-star` | ✅ Live |
| Prismatic Engine Site | `/home/ubuntu/work/prismatic-engine-site` | ✅ Live |

## Enforcement Rules
1. **Branch prefix matching** — agent is identified by the local branch prefix (e.g., `feature/` → Fred, `ned/` → Ned)
2. **Lane ownership** — agent can only write to directories listed in their `lanes.owner` array
3. **File locking** — files locked by another agent block the push (unless lock is stale: 5 min TTL)
4. **Staging governor** — only `fred` can push to `deploy-fresh`
5. **Main protection** — all pushes to `main` are blocked (production is manual-only)

## Installation
```bash
# All governed repos
./scripts/install-pre-push-hook.sh --all

# Single repo
./scripts/install-pre-push-hook.sh /path/to/repo
```

## YAML Config Requirement
Each repo must have a `PRISMATIC_ENGINE.yaml` with `agents:` format:
```yaml
agents:
  agent_name:
    role: Description
    lanes:
      owner: ["src/", "infra/"]
      read_only: ["content/"]
    branch_prefix: "feature/"
    staging_governor: true  # Only on the governor agent
```

## Verification
```bash
# Test main-blocking
printf 'refs/heads/main abc123 refs/heads/main def456\n' | python3 .git/hooks/pre-push
# Expected: exit=1, "Push to main is BLOCKED."

# Test feature-branch push (must be on a valid agent branch)
printf 'refs/heads/feature/task abc123 refs/heads/feature/task def456\n' | python3 .git/hooks/pre-push
# Expected: exit=0
```

## History
- **2026-06-11**: Pre-push hook created (GRO-1315, GRO-1218)
- **2026-06-14**: Deployed to all 6 repos; YAML configs standardized (GRO-1561)
