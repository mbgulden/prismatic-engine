# Hermes Profile Path Isolation

## Problem
Hermes profiles use a sandboxed home directory:
```
~/.hermes/profiles/<profile>/home/
```

This sandbox may contain a **stale copy** of `~/work/` created when the profile was set up. The real filesystem at `/home/ubuntu/work/` evolves independently — new repos cloned, files created, registry updated. But the sandbox sees only the frozen snapshot from profile creation time.

## Symptoms
- `project-registry.json` appears missing even though `ls /home/ubuntu/work/` shows it exists
- Only a few old directories visible in `~/work/`
- `gh repo list` shows repos that aren't in `~/work/`
- Cron jobs reference `/home/ubuntu/work/` paths correctly but the agent can't read them

## Fix
```bash
# Back up the stale sandbox copy
mv ~/work ~/work.stale.$(date +%Y%m%d)
# Symlink to the real filesystem
ln -s /home/ubuntu/work ~/work
# Verify
ls ~/work/project-registry.json
```

## Detection Before Rebuilding
Always check the real filesystem FIRST before concluding something is missing:
```bash
ls /home/ubuntu/work/   # Real filesystem
ls ~/work/              # Sandbox (may be stale)
```

If they differ significantly, the sandbox is stale. Don't rebuild — symlink.

## Session Reference
2026-06-03: Hermes sandbox `~/work/` had 4 dirs from May 30. Real `/home/ubuntu/work/` had 20+ dirs including a fully-maintained `project-registry.json` (885 lines, updated 2 hours prior). Symlink fixed instantly.
