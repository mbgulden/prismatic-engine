# Ned Cron Pitfalls — Discovered Jun 12, 2026

These pitfalls were encountered during the GRO-1483/1484/1485 execution run.
They belong in the main SKILL.md when space permits; for now they live here.

## Pre-Push Hook YAML Format Mismatch (`lanes:` vs `agents:`)

The shared pre-push hook script (`scripts/pre-push-hook.py`) reads
`config.get("agents", {})` and `agent_cfg.get("lanes", {}).get("owner", [])`.
Repos generated with the earlier `lanes:` top-level format (AOT mirror, AOT static)
will silently fail branch/lane validation because the hook finds no `agents` key.

**Symptoms:**
- Hook prints "No PRISMATIC_ENGINE.yaml found" (false negative — file exists)
- Hook prints "Branch 'X' doesn't match any agent prefix" (false negative — YAML has prefixes)

**Fix:** Convert the repo's PRISMATIC_ENGINE.yaml to the `agents:` format used by
prismatic-engine. See `references/aot-hook-installation.md` for the full conversion
workflow.

## Production Branch Name Varies by Repo (`main` vs `master`)

The shared pre-push hook hardcodes `PRODUCTION_BRANCH = "main"` but AOT repos deploy
Cloudflare Pages on push to `master`. If the hook isn't adapted, direct pushes to
`master` won't be blocked.

**Fix (preferred):** Make the hook configurable — read `staging.production_branch`
from the YAML, default to `"main"`:
```python
production_branch = staging_cfg.get("production_branch", "main")
```

**Fix (quick):** Modify the hook copy in the target repo with the right default:
```python
DEFAULT_PRODUCTION = "master"
```

**Real example (GRO-1484):** AOT hook adapted with `DEFAULT_PRODUCTION = "master"`
and YAML sets `staging: { production_branch: "master" }`.

## Sweep 6 Auto-Closes Same-Session Completions (Preempts Fred Review)

When sweep 6 (`git log --oneline --all --grep=ISSUE_ID`) runs in the same cron
tick as task execution, it finds feature-branch commits for issues Ned just moved
to `agent:fred` In Progress and closes them before Fred can review.

**Detection signal:** A closed issue has:
- Completion comment from < 1 hour ago (from the same cron tick)
- Commit on a non-master branch (e.g., `ned/gro-1485-origin-completions`)
- Was In Progress → Done in < 10 minutes

**Fix:** In sweep 6, skip issues where the latest non-dispatcher comment's
`createdAt` is within the last 2 hours. Or: only close when the commit exists
on `origin/master` (not just `--all`).

**Real example (Jun 12 2026):** GRO-1485/1484/1481 all closed by sweep 6 within
10 minutes of Ned's completion — commits on `ned/gro-1485-origin-completions`,
`ned/gro-1484-aot-hooks`, not yet on master.

**Workaround if this happens:** Revert the Done state if Fred hasn't reviewed:
```python
# Move back to In Progress + agent:fred
gql(f'''mutation {{ issueUpdate(id: "{uuid}", input: {{ 
  stateId: "{IN_PROGRESS_ID}", 
  labelIds: ["{FRED_LABEL_ID}"]
}}) {{ success }} }}''')
# Post correction comment
```

## `detect_origin_completions` — Generalized Peer-Review Loop (GRO-1485)

Replaced the Kai-specific `detect_agy_kai_completions()` with a fully generalized
`detect_origin_completions()` that works for ANY agent pair:

**Detection logic:**
1. Snapshot labels for all agent-labeled issues (builds history across cycles)
2. Query `agent:fred` issues
3. For each: look through ALL configured agents for one that previously appeared
   on this issue AND is not the reviewer (agy) or terminal (fred)
4. Require `agent:agy` in history (confirms this was a review, not direct dispatch)
5. Signal the origin agent via nudge file with `review_complete` signal type
   and `origin_agent` metadata field

**nudge_detector.py additions:**
- `review_complete` signal type (generalized, with `origin_agent` field)
- `is_review_complete()` detection function
- `get_origin_agent()` metadata extractor
- Legacy `agy_review_complete` still supported for backward compatibility

**Key insight:** The dedup key is per-issue (`origin_complete:{identifier}`),
not per-agent-pair — prevents double-signaling the same completion.

## Combined Sweep Script Doesn't Exist Yet

The skill references `scripts/maintenance-sweep.py` (combined sweeps 1-5) but
this file was never created. Each cron run reconstructs sweeps via ad-hoc Python
scripts in `/tmp/`. The Sweep 6 script (`scripts/sweep6-ip-fred-verification.py`)
also doesn't exist yet.

**Impact:** Every 5-minute tick, the agent writes sweep scripts from scratch,
burning tokens. A combined, committed script would reduce token burn.
