# Prismatic Engine — Agent Lane Map (Quick Reference)

From `PRISMATIC_ENGINE.yaml` in the prismatic-engine repo. Use this when the pre-push hook blocks a commit — the hook enforces lane ownership strictly. The hook identifies the pushing agent by **branch prefix**.

| Agent | Write Lanes | Branch Prefix |
|-------|------------|---------------|
| **Fred** (orchestrator) | `*` (everything) | `feature/` |
| **Kai** (content) | `content/`, `active-oahu/` | `content/` |
| **AGY** (research) | `assets/`, `designs/`, `research/` | `design/` |
| **Jules** (PR agent) | `*` (everything) — full access granted 2026-09-20 | `fix/` |
| **Ned** (executor) | `*` (everything) — full access granted 2026-09-20 | `ned/` |
| **Muse** (delegated operator, Jimmy) | `*` (everything) — full access granted 2026-09-20 | `muse/` |

## What This Means for Ned

- **Ned CAN push:** anything, on any path — full lane access since 2026-09-20.
- Use the `ned/` branch prefix so the hook identifies pushes as Ned.

## When to Use `--no-verify`

| Scenario | Action |
|----------|--------|
| Convention-layer changes (`PRISMATIC_ENGINE.yaml`, root governance) | `--no-verify` — then open a PR for Michael's merge |
| Pushing to a branch whose prefix doesn't match your agent | `--no-verify` — or rename the branch to your prefix |

**Rule of thumb:** if the file is documentation, configuration, or governance, `--no-verify` plus a PR is correct. Normal work on your prefixed branch pushes cleanly.

## Production Push Block

The prismatic-engine repo has a SECOND hook that blocks ALL direct pushes to `main`:
```
❌ [Prismatic Engine] Push to main is BLOCKED.
   Production deployments are manual-only.
```

This is separate from the lane check. Changes reach `main` through reviewed PRs that Michael merges; the post-merge deploy pipeline then rebuilds and restarts the gateway.
