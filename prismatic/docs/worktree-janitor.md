# Core worktree janitor and crons

Prismatic Engine ships a first-class, profile-agnostic worktree janitor. It is not a Ned or Hermes script; it runs anywhere PE is installed.

## Safety contract

The janitor does **not** try to guess whether work is emotionally or strategically “good.” It uses enforceable Git evidence and refuses ambiguity.

A worktree is automatically removable only when all of these are true in code:

1. It is not the canonical checkout.
2. The path exists and has no unmerged/conflicted files.
3. `git status --short` is clean.
4. `HEAD` is already an ancestor of the configured base ref.
5. The worktree is older than the stale threshold.

Everything else is kept or moved to manual review.

Dirty work is special:

- Dirty worktrees are **not** removed by the hourly cron.
- `--include-dirty` only includes dirty worktrees in the manifest and archives them.
- Actual dirty deletion requires the exact `dirty_confirm_token` printed in the manifest, passed back as `--confirm-dirty-token` or `confirm_dirty_token` in the API request.
- The manifest is written before mutation and includes the safety class/reasons for every removable and kept worktree.

This makes “dirty” a mechanical Git state, and “good” equivalent to “not proven safe to remove.” Good/ambiguous work is preserved by default.

## Usefulness/proof contract

Preservation is not enough. Agents should also leave portable evidence that their work is useful, promotable, or intentionally abandoned.

Each worktree can include a proof file at one of these paths:

- `.prismatic/worktree-proof.json`
- `prismatic-worktree-proof.json`
- `.worktree-proof.json`

Generate a template:

```bash
prismatic worktrees proof-template --issue GRO-1234 --summary "Implement useful thing"
```

The proof bundle is plain JSON and travels with the worktree. It can record:

- issue/task identifier,
- summary,
- verdict: `useful`, `indispensable`, `promote`, `broken`, or `superseded`,
- agent name,
- verification evidence,
- artifacts,
- handoff notes.

The janitor reads this evidence and adds value fields to every worktree record:

- `value_class`: `indispensable`, `preserve-needs-proof`, `broken-review`, `disposable`, or `unknown`
- `value_score`
- `value_signals`
- `proof_gaps`
- `promotion_recommendation`

Important rule: **missing proof never makes work disposable.** It creates a proof gap and preserves the work for review. This prevents undocumented but valuable work from being trashed just because an agent failed to document itself.

## CLI

```bash
# Inspect registered Git worktrees as JSON with safety/value classes and reasons
prismatic worktrees status --repo /path/to/prismatic-engine

# Emit a portable proof template agents can commit or leave in the worktree
prismatic worktrees proof-template --issue GRO-1234 --summary "Useful work"

# Plan cleanup without removing anything
prismatic worktrees janitor --repo /path/to/prismatic-engine --stale-hours 24

# Remove only clean+merged stale worktrees
prismatic worktrees janitor --repo /path/to/prismatic-engine --apply

# Include dirty work in the manifest/archive, but do not delete it without token
prismatic worktrees janitor --repo /path/to/prismatic-engine --include-dirty --apply

# Deliberately delete dirty work only after reviewing the manifest
prismatic worktrees janitor \
  --repo /path/to/prismatic-engine \
  --include-dirty \
  --confirm-dirty-token 'DELETE-DIRTY-WORKTREES:origin/main' \
  --apply
```

## Core API

Authenticated endpoints:

- `GET /api/v1/worktrees`
- `GET /api/v1/worktrees/proof-template`
- `POST /api/v1/worktrees/janitor` (`apply=false` by default)

The API uses the same safety and value gates as the CLI. Dirty deletion requires `confirm_dirty_token`.

## Core cron

The built-in cron manifest includes `prismatic.worktree-janitor.hourly`.

It runs the core CLI and stays silent when there is nothing to clean. The cron intentionally **does not** pass `--include-dirty`, so it cannot delete dirty or ambiguous work.

```bash
prismatic crons emit --repo /path/to/prismatic-engine
prismatic crons install --repo /path/to/prismatic-engine --yes
```
