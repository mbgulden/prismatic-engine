# Prismatic Engine operational docs

## State backup module

The orchestrator profile's `Prismatic Engine State Backup` cron job runs
`~/.hermes/profiles/orchestrator/scripts/prismatic_backup.py`, which imports
`prismatic.backup.create_backup()` from this repository.

`create_backup()` writes a compressed archive to:

```text
~/.prismatic/backups/state/prismatic-state-YYYYMMDD-HHMMSS.tar.gz
```

Runtime overrides:

- `PRISMATIC_STATE_BACKUP_DIR` — destination directory for backup archives.
- `PRISMATIC_STATE_DIR` — repo-local state root, defaulting to
  `/home/ubuntu/work/prismatic-engine/prismatic_state` when imported from the
  production checkout.
- `PRISMATIC_HOME` — canonical Prismatic home, defaulting to `~/.prismatic`.

The archive contains a `manifest.json`, repo-local state, and the canonical
Prismatic home DB/bus/curator state. SQLite-looking files are copied through
Python's SQLite backup API when possible, then fall back to a regular file copy
for non-SQLite fixtures or legacy files.

Smoke test the cron import path without waiting for 03:00:

```bash
cd /home/ubuntu/work/prismatic-engine
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_backup.py
```
