# Core worktree janitor and crons

Prismatic Engine ships a first-class, profile-agnostic worktree janitor. It is not a Ned or Hermes script; it runs anywhere PE is installed.

```bash
# Inspect registered Git worktrees as JSON
prismatic worktrees status --repo /path/to/prismatic-engine

# Plan cleanup without removing anything
prismatic worktrees janitor --repo /path/to/prismatic-engine --stale-hours 24

# Archive dirty/stale worktrees, remove them, and prune Git metadata
prismatic worktrees janitor --repo /path/to/prismatic-engine --include-dirty --apply

# Emit portable POSIX crontab lines for PE core crons
prismatic crons emit --repo /path/to/prismatic-engine

# Install the PE-managed cron block into the current user's crontab
prismatic crons install --repo /path/to/prismatic-engine --yes
```

The public Core API exposes the same feature under authenticated endpoints:

- `GET /api/v1/worktrees`
- `POST /api/v1/worktrees/janitor` (`apply=false` by default)

The built-in cron manifest includes `prismatic.worktree-janitor.hourly`, which runs the same core CLI and stays silent when there is nothing to clean.
