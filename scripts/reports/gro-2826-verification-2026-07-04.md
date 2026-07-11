# GRO-2826 — State DB retention silent-cron follow-up

Verified: 2026-07-04T15:12:23Z

## Findings

- The originally reported cron job `8ec9963f6d59e8b2` (`State DB retention policy — purge old dedup_log + durable_events`) is still absent from the active orchestrator and fred `cron/jobs.json` files.
- The old wrapper script `purge-retention.py` is absent from active profile script directories.
- A related disabled replacement row exists as `8c49acb91e076092` (`State DB health check + alert cron`), paused because it referenced deleted files. I did not re-enable it.
- The live database `/home/ubuntu/.prismatic/db/event_router.db` currently has `dedup_log` and `durable_events` tables present with 0 rows, plus large adjacent state tables (`label_snapshots`, `telemetry_media_artifacts`) that need retention coverage.

## Fix shipped in this branch

`TelemetryCollector.cleanup_expired()` now includes the state-retention tables that were outside the prior retention map:

- `telemetry_media_artifacts` via `detected_at` (default 30 days)
- `label_snapshots` via `seen_at` (default 14 days)
- `dedup_log` via `processed_at` (default 14 days)
- `durable_events` via `created_at` (default 30 days)

The cleanup command now skips absent external tables instead of aborting on partial DBs, so it can run against both the live engine DB and test/local state DBs with divergent schemas.

## Verification commands

```bash
python3 -m pytest prismatic/test_telemetry_extension.py -q
# 26 passed in 4.38s

python3 -m prismatic.admin telemetry cleanup --dry-run --db-path /home/ubuntu/.prismatic/db/event_router.db
# exit 0; includes dedup_log=0, durable_events=0, label_snapshots=180145,
# telemetry_media_artifacts=0; no schema errors.

python3 -m prismatic.admin telemetry cleanup --dry-run --db-path /home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db
# exit 0; skips absent durable_events on the local partial DB.
```
