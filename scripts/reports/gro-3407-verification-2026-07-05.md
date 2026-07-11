# GRO-3407 Verification — Prismatic Engine State Backup

## TL;DR

`Prismatic Engine State Backup` was failing because Fred's cron wrapper imports `prismatic.backup.create_backup`, but the Prismatic Engine checkout did not ship `prismatic/backup.py`. This branch adds the missing dependency-light backup module and regression tests. Manual smoke run now exits 0 and writes the expected archive under `$HOME/.prismatic/backups/state/`.

## Issue / job

- Linear: GRO-3407 — `[CRON-FIX] Prismatic Engine State Backup is silent-failing (unknown)`
- Profile: `fred`
- Job ID: `a2e5b9f7c3d1`
- Script: `/home/ubuntu/.hermes/profiles/fred/scripts/prismatic_backup.py`
- Schedule: `0 3 * * *`

## Evidence inspected

### Cron ledger

From `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`:

```json
{
  "id": "a2e5b9f7c3d1",
  "name": "Prismatic Engine State Backup",
  "script": "prismatic_backup.py",
  "enabled": true,
  "state": "scheduled",
  "last_run_at": "2026-07-05T04:47:36.538725-06:00",
  "last_status": "ok",
  "last_error": null,
  "last_delivery_error": null,
  "next_run_at": "2026-07-06T03:00:00-06:00",
  "deliver": "local"
}
```

### Failure artifact

`/home/ubuntu/.hermes/profiles/fred/cron/output/a2e5b9f7c3d1/2026-07-05_03-00-28.md` recorded the root failure:

```text
Script exited with code 1
stderr:
Error importing prismatic backup module: No module named 'prismatic.backup'
```

### Latest cron artifact

`/home/ubuntu/.hermes/profiles/fred/cron/output/a2e5b9f7c3d1/2026-07-05_04-47-36.md` recorded a successful run:

```text
Success: Backup created at /home/ubuntu/.prismatic/backups/state/prismatic-state-20260705-104735.tar.gz
```

### Live smoke run after the branch fix

Command:

```bash
cd /home/ubuntu/work/prismatic-engine
python3 /home/ubuntu/.hermes/profiles/fred/scripts/prismatic_backup.py ; echo EXIT:$?
```

Output:

```text
Success: Backup created at /home/ubuntu/.prismatic/backups/state/prismatic-state-20260705-112400.tar.gz
EXIT:0
```

## Code change

Added `prismatic.backup.create_backup()`:

- Captures `prismatic_state/` from the engine checkout when present.
- Captures the shared swarm lock registry when present.
- Writes atomically via a temporary tarball, then renames into place.
- Uses `$PRISMATIC_BACKUP_DIR` override when set, otherwise `$HOME/.prismatic/backups/state/` to match the cron's existing artifact path.
- Raises `FileNotFoundError` if no state source exists instead of creating a misleading empty archive.

Added `prismatic/test_backup.py` coverage for:

- Successful archive creation from explicit state paths.
- Archive member names are relative/safe, not absolute extraction paths.
- Empty source set fails loudly.

## Verification

```text
python3 -m pytest prismatic/test_backup.py -q
..                                                                       [100%]
2 passed in 0.06s
```

Ad-hoc verifier (temporary `/tmp/hermes-verify-*.py`, cleaned in `finally`) asserted:

- `prismatic/backup.py`, `prismatic/test_backup.py`, and this report are present on the branch.
- `python3 -m py_compile prismatic/backup.py` passes.
- `python3 -m pytest prismatic/test_backup.py -q` passes.
- Fred's existing cron wrapper exits 0 and creates a `$HOME/.prismatic/backups/state/prismatic-state-*.tar.gz` archive.

Fresh verifier output:

```text
..                                                                       [100%]
2 passed in 0.06s
GRO-3407 verifier passed
smoke: Success: Backup created at /home/ubuntu/.prismatic/backups/state/prismatic-state-20260705-112514.tar.gz
cleaned /tmp/hermes-verify-iw65kle3.py
```

## Files inspected / changed

Inspected:

- `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/fred/scripts/prismatic_backup.py`
- `/home/ubuntu/.hermes/profiles/fred/cron/output/a2e5b9f7c3d1/2026-07-05_03-00-28.md`
- `/home/ubuntu/.hermes/profiles/fred/cron/output/a2e5b9f7c3d1/2026-07-05_04-47-36.md`

Changed:

- `prismatic/backup.py`
- `prismatic/test_backup.py`
- `scripts/reports/gro-3407-verification-2026-07-05.md`

## Recommendation

Move GRO-3407 to review. The missing module that produced the `No module named 'prismatic.backup'` failure is now present, covered by tests, and confirmed by a live run through Fred's existing cron wrapper.
