# GRO-3106 — event_router.db growth + VACUUM coverage audit

## Scope

Ned re-ran the database-growth audit for `event_router.db` after the issue was re-dispatched despite already sitting in Linear `In Review`.

## Live database inventory

Three state roots currently exist:

| DB path | Size | Primary growth source |
|---|---:|---|
| `/home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db` | 10,305,536 bytes | `telemetry_credit_ledger` (18,094), `telemetry_media_artifacts` (17,772), `lane_budgets` (12,491) |
| `/home/ubuntu/.prismatic/db/event_router.db` | 50,946,048 bytes | `label_snapshots` (180,145), `telemetry_credit_ledger` (15,828), `telemetry_media_artifacts` (15,828) |
| `/home/ubuntu/.prismatic/published/prismatic-engine/prismatic_state/event_router.db` | 3,977,216 bytes | `telemetry_credit_ledger` (8,231), `telemetry_media_artifacts` (6,654), `telemetry_agent_runs` (566) |

The original 43MB outlier is no longer specifically the repo-local DB; the deployed root is now the largest file and is dominated by `label_snapshots`.

## Root cause

`TelemetryCollector.cleanup_expired()` had two retention gaps that made the SQLite VACUUM cron insufficient by itself:

1. The high-growth tables `telemetry_media_artifacts`, `label_snapshots`, and `dedup_log` were not in the retention map, so logical cleanup did not prune them.
2. Cleanup ran `VACUUM` before committing deletes, which triggers SQLite's `cannot VACUUM from within a transaction` failure mode.
3. State roots have divergent schemas; a cleanup map that assumes every table exists can abort the entire batch on `no such table`.

## Change made

- Added env-overridable retention periods:
  - `PRISMATIC_RETENTION_MEDIA_ARTIFACTS` default 30 days
  - `PRISMATIC_RETENTION_LABEL_SNAPSHOTS` default 14 days
  - `PRISMATIC_RETENTION_DEDUP_LOG` default 14 days
- Added those three high-growth tables to `cleanup_expired()`.
- Made cleanup skip tables absent from a divergent state root instead of aborting the batch.
- Changed cleanup ordering to `commit()` deletes before running `VACUUM`.
- Added regression tests for the high-growth tables, missing-table skip behavior, and real cleanup/VACUUM path.

## VACUUM cron coverage

The orchestrator SQLite VACUUM cron `3d2520fbfae8cf0e` is enabled and scheduled for Sundays at 03:00. Its wrapper scans all three relevant state roots:

- `/home/ubuntu/work/prismatic-engine/prismatic_state`
- `/home/ubuntu/.prismatic/db`
- `/home/ubuntu/.prismatic/published/prismatic-engine/prismatic_state`

That cron reclaims free pages, but it does not decide retention. The retention policy now lives in `prismatic.telemetry.cleanup_expired()` so the admin cleanup path can delete expired rows before VACUUM reclaims file space.

## Verification evidence

Committed before running tests per Ned's autonomous skeleton, then ran:

```text
$ python3 -m pytest prismatic/test_telemetry_extension.py::TestCleanupExpired -q --tb=short
....                                                                     [100%]
4 passed in 0.60s
```

Copy-based admin cleanup smoke test against the repo-local DB also passed without mutating live operational data:

```text
rc=0 pre=10305536 post=9994240 delta=-311296
prismatic-admin: cleanup complete — deleted:
  Total: 0 row(s)
```

That copy run confirms the fixed path can run `VACUUM` successfully and reclaim free pages even when no rows are past retention in that specific DB copy.
