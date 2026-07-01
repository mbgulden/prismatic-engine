# GRO-3124 — Verification report

**Issue:** [Ned] Verify memory-grooming cron sweeps .bak-* files
**Triage date:** 2026-07-01
**Branch:** `ned/GRO-3124`
**Status:** ✅ Verified working

## TL;DR

The grooming/cleanup chain that backs the `.bak-*` sweep is **already wired and working correctly**. There is no bug to fix — only a verification gap that this report, plus the new behavioral test (`scripts/quality/test_ned_memories_bak_sweep.sh`), now closes.

The issue's premise ("`.bak-*` files keep accumulating") was misleading: at the time of triage (2026-07-01 11:13 UTC) there were 4 `.bak-*` files in `~/.hermes/profiles/ned/memories/`, **none of which was older than the 7-day retention threshold**. The `*.bak-prune-*` namespace is shared with `memory_capacity_check.py`'s `cleanup_old_backups()` (BACKUP_RETENTION_DAYS=7), so those files are intentionally retained until they age out.

## Chain of custody (current state)

| Layer | Script | Trigger | Behavior |
|---|---|---|---|
| Hermes profile-level prune | `memory_capacity_check.py::cleanup_old_backups()` | On every `mem` tool write (or manual) | Deletes `*.bak-prune-*` and `*.bak-bulkprune-*` > 7d |
| Ned lane housekeeping | `~/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh` | Cron `gro3104-ned-bak-sweep` weekly Mon 00:30 UTC | Globs **all** `*.bak-*`, deletes > 7d, **alerts via exit 2** if stale remain post-delete |

The housekeeping script was authored in GRO-3104 (see `logs/gro-3104-fix.md`) and registered in the orchestrator's `cron/jobs.json` as:

```json
{
  "id": "gro3104-ned-bak-sweep",
  "name": "Ned memories .bak-* sweep (weekly Mon, no-agent)",
  "schedule": { "kind": "cron", "expr": "30 0 * * 1", "display": "30 0 * * 1" },
  "enabled": true,
  "script": "/home/ubuntu/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh --apply",
  "next_run_at": "2026-07-06T00:30:00-06:00"
}
```

## Verification evidence

### 1. Dry-run against the live memories dir (no destructive changes)

```
$ bash ~/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh
[ned-bak-sweep] mode=dry-run found=4 deleted=0 retention_days=7
$ echo $?
0
```

Per-file age (2026-07-01 11:13 UTC reference):
```
MEMORY.md.bak-prune-1782403842   age=5.79d  stale=False
MEMORY.md.bak-prune-1782766394   age=1.60d  stale=False
USER.md.bak-prune-1782766395     age=1.60d  stale=False
USER.md.bak-prune-1782766417     age=1.60d  stale=False
```

No file is over 7 days → nothing would have been deleted. The script reported `deleted=0` and exited 0, which is the correct expected behavior. The first file (`1782403842`, age 5.79d) will become stale at age 7d → the weekly Mon Jul 6 sweep will be the first to touch it.

### 2. Apply against the live memories dir (sanity)

```
$ bash ~/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh --apply
[ned-bak-sweep] mode=apply found=4 deleted=0 retention_days=7
$ echo $?
0
```

Zero deletions, exit 0. Confirming nothing over 7d exists. Post-condition re-scan found no stale files, so exit 2 path did not (correctly) fire.

### 3. Behavioral test (`scripts/quality/test_ned_memories_bak_sweep.sh`)

New in this commit. Exercises the script in a sandboxed fake-HOME so the real memories dir is never touched. Four assertions:

| # | Scenario | Expected | Got |
|---|---|---|---|
| 1 | dry-run with 1 stale + 2 fresh files | `found=3 deleted=0`, exit 0, source untouched | ✅ |
| 2 | `--apply` with 1 stale + 2 fresh files | 1 deleted, 2 remain (≤7d), exit 0 | ✅ |
| 3 | `--apply` with parent dir `chmod 555` (delete blocked) | exit 2 + `POST-CONDITION ALERT` | ✅ |
| 4 | missing `MEM_DIR` | exit 1 + `missing` error | ✅ |

```
$ bash scripts/quality/test_ned_memories_bak_sweep.sh
======================================
✅ Passed: 4
❌ Failed: 0
All behavioral tests passed.
```

## Why this is a verification task, not a code-fix task

The issue description assumed accumulation indicated a broken cleanup; verification showed:
- The script exists, is correct, and is wired to a real cron.
- The 4 files present at triage are all under retention.
- The next scheduled sweep (Mon Jul 6) will delete any that have crossed 7d.
- A new behavioral test prevents future regressions.

## Acceptance criteria (issue §Acceptance)

| Criterion | Status | Evidence |
|---|---|---|
| `.bak-*` > 7 days deleted by next grooming run | ✅ already met by Mon Jul 6 weekly cron | `next_run_at: 2026-07-06T00:30:00-06:00` |
| Post-condition check logs alert | ✅ exists in script (exit 2 + stderr) | confirmed by Test 3 of new behavioral test |
| Current backlog cleaned | ✅ no backlog (0 files > 7d) | dry-run reported `deleted=0` |

## Side findings (not bugs, noted for posterity)

- `memory_capacity_check.py`'s `cleanup_old_backups()` and the Ned-lane `bak_sweep.sh` together provide defense in depth: the Python path cleans `*.bak-prune-*` on every mem-write, the Ned cron cleans any foreign `*.bak-pre-*` patterns weekly.
- The issue's reference to "claims success but `.bak-*` files keep accumulating" was based on stale description; current state shows the system is working.
- No additional cron wiring was needed. No code change to the sweep script was needed.

## Out-of-scope items the issue's description mentioned but which are not Ned-lane

- "Add metric to factory digest for wakeup-empty-rate" — that's GRO-3121 (different issue, separate pickup).
- "OKF sprawl curation" — that's GRO-3122 (separate pickup, larger scope).

These are flagged for next Ned wakeup / next-cycle dispatch.
