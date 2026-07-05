# GRO-3410 Verification — Prismatic Engine State Backup

## TL;DR

🟢 `Prismatic Engine State Backup` is no longer silent-failing. The `a2e5b9f7c3d1` orchestrator cron failure was the missing `prismatic.backup` module; branch `ned/GRO-3410` now provides `prismatic.backup.create_backup()`, regression coverage, and a docs note. The latest cron artifact and a live wrapper run both create a backup archive successfully.

## Issue / job

- Linear: GRO-3410
- Job ID: `a2e5b9f7c3d1`
- Profile: `orchestrator`
- Job name: `Prismatic Engine State Backup`
- Script: `/home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_backup.py`
- Schedule: `0 3 * * *`

## Root cause

The orchestrator wrapper prepends `/home/ubuntu/work/prismatic-engine` to `sys.path` and imports:

```python
from prismatic.backup import create_backup
```

The failing cron artifacts showed that module was absent:

```text
2026-07-04_03-00-28.md: Error importing prismatic backup module: No module named 'prismatic.backup'
2026-07-05_03-00-28.md: Error importing prismatic backup module: No module named 'prismatic.backup'
```

## Fix present on branch

Commit `8ce798041 [Ned] Add Prismatic state backup module (#GRO-3410)` adds:

- `prismatic/backup.py` — dependency-light `create_backup()` implementation.
- `prismatic/test_backup.py` — regression tests for archive creation and missing-source refusal.
- `prismatic/docs/README.md` — documents the backup helper and environment overrides.

The helper copies live state into a staging directory, uses SQLite's backup API for SQLite-like files where possible, writes `manifest.json`, and emits `prismatic-state-YYYYMMDD-HHMMSS.tar.gz` under `$PRISMATIC_STATE_BACKUP_DIR` or `$HOME/.prismatic/backups/state/`.

## Live evidence — 2026-07-05

### 1) Cron job ledger

`/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json` now reports:

```json
{
  "id": "a2e5b9f7c3d1",
  "name": "Prismatic Engine State Backup",
  "enabled": true,
  "state": "scheduled",
  "script": "prismatic_backup.py",
  "last_run_at": "2026-07-05T04:47:36.538725-06:00",
  "last_status": "ok",
  "last_error": null,
  "last_delivery_error": null,
  "next_run_at": "2026-07-06T03:00:00-06:00"
}
```

### 2) Cron output artifact

Latest artifact:

`/home/ubuntu/.hermes/profiles/orchestrator/cron/output/a2e5b9f7c3d1/2026-07-05_04-47-36.md`

```text
Success: Backup created at /home/ubuntu/.prismatic/backups/state/prismatic-state-20260705-104735.tar.gz
```

### 3) Targeted test run

Command:

```bash
python3 -m pytest prismatic/test_backup.py -q
```

Output:

```text
..                                                                       [100%]
2 passed in 0.10s
```

### 4) Live wrapper smoke run

Command:

```bash
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_backup.py
```

Output:

```text
Success: Backup created at /home/ubuntu/.prismatic/backups/state/prismatic-state-20260705-121524.tar.gz
```

### 5) Backup archive sanity

Recent archives under `/home/ubuntu/.prismatic/backups/state/` are ~3.1 MB and contain Prismatic runtime state entries such as:

```text
prismatic_state/alerts.log
prismatic_state/event_router.db
prismatic_state/fleet_watchdog_state.json
prismatic_state/linear_budget.db
prismatic_state/pipelines/GRO-9999.json
```

## Verification status

✅ The import failure is fixed.
✅ The cron ledger now shows `last_status: ok` with no delivery error.
✅ The latest per-job cron artifact contains the expected success payload.
✅ The regression tests pass.
✅ A live run through the actual orchestrator wrapper creates a backup archive.

## Recommendation

Move GRO-3410 to review. No further code changes are needed for this silent-cron alert unless the next scheduled 03:00 run regresses.