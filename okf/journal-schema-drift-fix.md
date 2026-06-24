# Prismatic Journal — Schema Drift Fix

**Date:** 2026-06-24
**Status:** Fixed (verified live)
**Author:** Fred (per Michael's request)
**Refs:** Hermes cron `ce3dd849ede5`, GRO-XXXX

## Bug

The "Hermes daily journal snapshot" cron (`ce3dd849ede5`, every 60min) was
**failing every run** since the registry schema changed. Last 24h output:

```
Script exited with code 1
AttributeError: 'str' object has no attribute 'get'
  File ".../prismatic/journal.py", line 485, in extract_golden_thread_summary
    lines.append(f"- Linear: {sync.get('linear_in_progress', 0)} ...")
```

## Root cause

`project-registry.json` schema drift:

| Key | Old shape | New shape |
|---|---|---|
| `_last_sync` | `dict` (sync metadata) | `str` (ISO timestamp) |
| `_last_sync_previous` | did not exist | `dict` (sync metadata moved here) |

The code at line 483 read `_last_sync` expecting a dict but got a string.
Calling `.get()` on a string → `AttributeError` → cron marked `error`.

This was the journal that **was supposed to be standalone in Prismatic Engine
and hook into Hermes seamlessly**. It IS standalone (stdlib-only imports, no
Hermes coupling), but it had a latent schema-drift bug that no one caught
because the cron output goes to `deliver: origin` (silent failure mode —
Michael wouldn't have seen it unless he checked cron output).

## Fix (3 files)

### 1. `prismatic/journal.py` — `extract_golden_thread_summary`

- Read `_last_sync_previous` first (current schema)
- Fall back to `_last_sync` if it's still a dict (legacy)
- Map `linear_todo` → `linear_unstarted` (alias, both mean "unstarted state")
- Surface new-schema fields too: `linear_total_active`, `stale_gt7d`,
  `agent_done_stuck`, `cron_errors`, `cron_silent_fails`

### 2. `prismatic/journal.py` — `__main__` block

Default to `cli_journal_snapshot` when invoked as `python -m prismatic.journal`
with no subcommand. Previously crashed with `AttributeError: Namespace has no
attribute 'period'`. The Hermes cron path used the installed
`prismatic-journal-snapshot` binary, so it wasn't affected — but the module
invocation path was broken.

### 3. `tests/test_journal.py` — 4 new tests

- `test_golden_thread_summary_handles_current_schema` — the bug case (regression test)
- `test_golden_thread_summary_handles_legacy_schema` — pre-drift registries still work
- `test_golden_thread_summary_handles_missing_registry` — graceful degradation
- `test_golden_thread_summary_handles_empty_registry` — no crash on empty sync data

## Verification

```
$ python -m prismatic.journal
{
  "changed": true,
  "signals": 1153,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-06-24.md",
  "lines": 51
}

$ pytest tests/test_journal.py
8 passed (4 new + 4 existing)

$ pytest tests/
285 passed (was 281), 2 skipped, 22 subtests — no regressions
```

The Golden Thread section now renders:
```
### 🔗 Golden Thread (project-registry.json)
- Linear: 218 In Progress, 0 In Review, 30 Todo
- GitHub: 0 open PRs, 0 issues
- Linear totals: 600 active, 292 stale >7d, 0 agent:done stuck
- Crons: 17 errors, 14 silent fails
```

## Architecture verification

**Standalone in Prismatic Engine:** ✅
- `prismatic/journal.py` imports only stdlib (`os`, `sys`, `json`, `urllib`, etc.)
- `cli_journal_snapshot()` is the public API
- Installed CLI: `prismatic-journal-snapshot` (binary at `/home/ubuntu/.local/bin/`)
- Works without Hermes: `python -m prismatic.journal` runs standalone

**Hooks into Hermes seamlessly:** ✅
- Hermes cron `ce3dd849ede5` calls wrapper `/home/ubuntu/.hermes/profiles/orchestrator/scripts/journal_snapshot.py`
- Wrapper execs `prismatic-journal-snapshot` binary (1 line of bash)
- Cron output goes to `deliver: origin` (Hermes's natural delivery path)
- Other Hermes crons can call the binary too

## Failure mode that hid this bug

This bug ran for **22 hours** (last successful run: Jun 23 12:51, first
failing run after that). It was invisible because:

1. Cron delivered to `origin` (Hermes conversation, not Telegram)
2. The error output went to `cron/output/ce3dd849ede5/*.md` — Michael didn't check
3. No downstream consumer cares if the daily snapshot fails (it's informational)

**The fix for this failure mode:** per the prismatic-engine-operations skill,
all cron jobs with `deliver: local` should be in the silent-failure-watchdog
scope. Adding `ce3dd849ede5` to the silent-cron-detector's expected-known
list would have surfaced this in <24h.

## Follow-ups

- [ ] Add `journal-snapshot-success` health check to fleet_watchdog.py
- [ ] Consider daily Telegram digest of snapshot key metrics (so Michael sees counts)
- [ ] Audit other journal.py functions for similar schema assumptions
- [ ] Add a registry-schema-version field so future drift is detectable