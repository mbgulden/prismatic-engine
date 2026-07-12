# GRO-3396 Verification — Hermes daily journal snapshot

## Issue

Linear [GRO-3396](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3396) reported the Fred-profile cron job `Hermes daily journal snapshot` (`ce3dd849ede5`) as silent-failing with an `unknown` root-cause family.

## Fix verified

Implementation already exists on this branch in commit `f9044809a`:

- `prismatic/journal.py::collect_candidates()` now caches file mtimes during traversal.
- The final sort no longer re-stats paths that may be concurrently pruned by Hermes cron output rotation.
- This closes the transient `FileNotFoundError` race for cron output files disappearing between discovery and ordering.

## Live evidence (2026-07-04)

- Fred cron job config: `last_status=ok`, `last_error=None`, `last_delivery_error=None`, `deliver=local`, `script=journal_snapshot.py`, `no_agent=True`.
- Latest output artifact exists: `/home/ubuntu/.hermes/profiles/fred/cron/output/ce3dd849ede5/2026-07-04_10-15-04.md`.
- Latest output content reports success: `changed=true`, `signals=869`, `today_file=/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-04.md`, `lines=40`.
- Journal artifacts were updated by the job: inbox `2026-07-04.md` and event index `events-2026-07-04.json` both exist with fresh mtimes.

## Verification commands run

```bash
python3 -m py_compile prismatic/journal.py
/home/ubuntu/.hermes/profiles/fred/scripts/journal_snapshot.py
```

The direct snapshot run exited 0 and returned:

```json
{
  "changed": true,
  "signals": 868,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-04.md",
  "lines": 40
}
```

Ad-hoc race regression probe also passed: a temporary `JournalConfig` with a cron-output file that disappears after the initial traversal returned cleanly (`ok=True`) instead of raising during sort.

## Disposition

No additional Fred-profile cron config change was required. The job is now producing local artifacts and the transient race fix is present in the Prismatic Engine code branch.
