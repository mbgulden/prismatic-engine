# GRO-2437 verification refresh — Nightly Autonomous Backlog Worker

Verified by: Ned  
Timestamp (UTC): 2026-07-04T17:15:12Z

## Issue

[GRO-2437](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2437) reported that Fred cron job `0ce73bbeee4e` (`Nightly Autonomous Backlog Worker`) was silent-failing. The original issue body cited `last_run_at` from 2026-06-25 and `deliver=local`.

## Current live ledger evidence

Both Fred and orchestrator profile ledgers now agree for job `0ce73bbeee4e`:

| Profile | enabled | state | last_run_at | last_status | last_error | last_delivery_error | deliver | next_run_at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fred | `true` | `scheduled` | `2026-07-04T04:01:45.766051-06:00` | `ok` | `None` | `None` | `telegram:8190664947` | `2026-07-05T04:00:00-06:00` |
| orchestrator | `true` | `scheduled` | `2026-07-04T04:01:45.766051-06:00` | `ok` | `None` | `None` | `telegram:8190664947` | `2026-07-05T04:00:00-06:00` |

## Current output artifact evidence

Latest output artifact exists in both profiles at:

- `/home/ubuntu/.hermes/profiles/fred/cron/output/0ce73bbeee4e/2026-07-04_04-01-44.md`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/0ce73bbeee4e/2026-07-04_04-01-44.md`

Relevant lines from the artifact:

```text
[NIGHTLY-BACKLOG] Backlog changed — processing...
[NIGHTLY-BACKLOG] AGY exit: 0
### Gaps Detected
| Linear backlog | 0 | GRO-3401 | [CRON-FIX] `gpt-oss-quota-headroom` is silent-failing (unknown) |
| Linear backlog | 0 | GRO-3400 | [CRON-FIX] `OKF Google Drive Drift Check` is silent-failing (unknown) |
| Linear backlog | 0 | GRO-3399 | [CRON-FIX] `Hermes daily journal snapshot` is silent-failing (unknown) |
```

## Script sanity check

`python3 -m py_compile` passed for both live wrapper copies:

- `/home/ubuntu/.hermes/profiles/fred/scripts/nightly_backlog_delta.py`
- `/home/ubuntu/.hermes/profiles/orchestrator/scripts/nightly_backlog_delta.py`

The wrapper contains the two previously reported recovery patterns:

- headless AGY execution via `script -qc` around the `agy --model sonnet --prompt ...` call;
- Linear backlog query filters unassigned issues via `assignee: { null: true }`.

## Conclusion

The silent-failure condition is stale. The job is enabled, scheduled, ran successfully today, delivered to Telegram instead of local-only output, produced current artifacts, and the wrapper compiles.

## Action

This is verification-only closure. Keep [GRO-2437](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2437) in `In Review`, remove stale active routing (`agent:ned`), and hand it to peer/human review for final Done/close disposition.
