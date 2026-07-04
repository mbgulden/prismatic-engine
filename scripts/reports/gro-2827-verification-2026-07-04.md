# GRO-2827 verification — Engine log rotation cron

Timestamp: 2026-07-04T15:07:53Z  
Issue: GRO-2827 — `[SILENT-CRON] Engine log rotation — compress logs > 10MB daily is silent-failing`

## TL;DR

The originally reported cron job is not active in any checked Hermes profile. No live scheduler row remains for job `5f1d4354121f492e`, and the `rotate-engine-logs.py` wrapper/script is absent from the checked profile and engine script locations. This is a stale silent-cron ticket already resolved by prior cleanup; there is no new log-rotation code path to repair in this pass.

## Live evidence

Command run at `2026-07-04T15:07:53Z` checked `fred`, `orchestrator`, and `ned` profile cron ledgers plus the expected script paths.

### jobs.json scan

| Profile | jobs.json | Job count | Matches for job ID/name/script |
|---|---:|---:|---:|
| `fred` | present | 76 | 0 |
| `orchestrator` | present | 76 | 0 |
| `ned` | present | 8 | 0 |

Match criteria:
- exact job ID `5f1d4354121f492e`
- name containing `Engine log rotation`
- script path containing `rotate-engine-logs`

### cron output directories

| Profile | Output dir | Result |
|---|---|---|
| `fred` | `/home/ubuntu/.hermes/profiles/fred/cron/output/5f1d4354121f492e` | absent |
| `orchestrator` | `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/5f1d4354121f492e` | absent |
| `ned` | `/home/ubuntu/.hermes/profiles/ned/cron/output/5f1d4354121f492e` | absent |

### script path scan

All expected script locations are absent:

- `/home/ubuntu/.hermes/profiles/fred/scripts/rotate-engine-logs.py`
- `/home/ubuntu/.hermes/profiles/orchestrator/scripts/rotate-engine-logs.py`
- `/home/ubuntu/.hermes/profiles/ned/scripts/rotate-engine-logs.py`
- `/home/ubuntu/work/prismatic-engine/scripts/rotate-engine-logs.py`

## Prior Linear evidence incorporated

The issue already had a 2026-07-03 Ned verification comment showing the active orchestrator cron ledger had 0 matches for the same job ID/name and that both the orchestrator wrapper and engine-side script were absent. Today's pass reconfirmed that state across `fred`, `orchestrator`, and `ned` profiles.

## Recommendation

Keep GRO-2827 in In Review for human closeout. Do not recreate the log-rotation cron unless a separate issue defines the desired canonical engine log rotation behavior and target profile. The broken silent-failing row from the Tier-1 watchdog alert is gone.

## Files/locations inspected

- `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/ned/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/{fred,orchestrator,ned}/cron/output/5f1d4354121f492e/`
- `/home/ubuntu/.hermes/profiles/{fred,orchestrator,ned}/scripts/rotate-engine-logs.py`
- `/home/ubuntu/work/prismatic-engine/scripts/rotate-engine-logs.py`
