# GRO-3408 verification — Hermes daily journal snapshot

Verified by: Ned  
Timestamp (UTC): 2026-07-05T11:10:15Z

## Issue

[GRO-3408](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3408) reports `[CRON-FIX] Hermes daily journal snapshot is silent-failing (unknown)` for orchestrator cron job `ce3dd849ede5`.

This is the same failure family as GRO-3396/GRO-3399: `prismatic.journal.collect_candidates()` could discover a cron-output file and then re-stat it during final sorting after Hermes cron rotation had removed it. That race can crash the hourly `journal_snapshot.py` feeder and appear as an `unknown` silent-cron failure.

## Fix applied on this branch

Updated `prismatic/journal.py` so `collect_candidates()` caches each candidate file's `st_mtime` during traversal and sorts by the cached timestamp instead of calling `p.stat()` again in the sort key.

Why this matters:

- cron output files are written/pruned concurrently by Hermes;
- `FileNotFoundError` during discovery is already handled;
- the old sort-time re-stat escaped that guard;
- the new cached-mtime sort removes the second race window.

## Live cron evidence before finalization

Current orchestrator ledger for job `ce3dd849ede5` showed:

- `enabled=true`
- `state=scheduled`
- `last_run_at=2026-07-05T04:27:33.387788-06:00`
- `last_status=ok`
- `last_error=null`
- `last_delivery_error=null`
- `deliver=local`

Latest output artifact inspected:

- `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/ce3dd849ede5/2026-07-05_04-27-33.md`

Artifact body:

```json
{
  "changed": false,
  "signals": 0,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-05.md"
}
```

The job is currently exiting cleanly, but this branch keeps the underlying transient-file race fix on the GRO-3408 review branch so the duplicate filing has a durable work product and verification trail.

## Verification performed

Verification ran after the commit on 2026-07-05T11:11Z:

1. `python3 -m py_compile prismatic/journal.py` — passed.
2. Focused ad-hoc race probe — passed:
   - output: `AD_HOC_RACE_PASS collect_candidates cached mtime avoids sort-time restat`
   - assertion: candidate cron output was discovered with exactly two `Path.stat()` calls; the old sort-time re-stat path would perform the third call and raise `FileNotFoundError`.
3. Direct cron smoke run — passed:
   - command: `hermes --profile orchestrator cron run ce3dd849ede5`
   - output: `Ran now: succeeded.`
4. Post-run ledger check — passed:
   - `last_run_at=2026-07-05T05:11:06.977174-06:00`
   - `last_status=ok`
   - `last_error=null`
   - `last_delivery_error=null`
5. Latest output artifact exists and is non-empty:
   - `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/ce3dd849ede5/2026-07-05_05-11-06.md`
   - payload included `"changed": true`, `"signals": 772`, `"lines": 40`.

## Disposition

Move GRO-3408 to `In Review` after verification/finalization. If duplicate cleanup is desired, reviewer can close it against GRO-3396/GRO-3399; Ned's branch contains the same concrete race fix applied to the current deploy-fresh baseline.
