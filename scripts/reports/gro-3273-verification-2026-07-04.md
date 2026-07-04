# GRO-3273 verification refresh — Ned Dispatcher Daily Summary

Date: 2026-07-04T15:52:00Z
Agent: Ned
Mode: verification-only / stale redispatch cleanup

## Issue

GRO-3273 was already moved to In Review after the `Ned Dispatcher Daily Summary` silent-cron hard failure was mitigated, but it reappeared in Ned's active queue because the issue still carried `agent:ned` + `dispatch:ready`.

## Live evidence gathered

- Cron job `c72b9496a7ef` exists in `/home/ubuntu/.hermes/profiles/ned/cron/jobs.json`.
- Current scheduler fields at verification time:
  - `enabled`: true
  - `state`: `scheduled`
  - `schedule`: `55 23 * * *`
  - `next_run_at`: `2026-07-04T23:55:00+00:00`
  - `last_run_at`: `2026-07-03T23:55:03.829099+00:00`
  - `last_status`: `ok`
  - `last_error`: `null`
  - `last_delivery_error`: `null`
  - `deliver`: `local`
- Latest scheduled output artifact exists:
  - `/home/ubuntu/.hermes/profiles/ned/cron/output/c72b9496a7ef/2026-07-03_23-55-03.md`
  - Output body: `=== Ned Dispatcher Daily Summary: SKIPPED (no metrics table) ===`
- Manual live run on 2026-07-04 exited `0` and printed the same explicit skip path.
- Metrics DB exists at `/home/ubuntu/work/prismatic-engine/prismatic_state/curator_metrics.db`.
- Tables present now: `curator_runs`, `audit_runs`, `sqlite_sequence`.
- `ned_dispatcher_metrics` is still absent, so `SKIPPED (no metrics table)` remains the correct non-fatal behavior.

## Verdict

The original Tier-1 silent hard failure remains mitigated. The cron is not crashing; it exits cleanly and leaves an output artifact. The remaining `SKIPPED` output is a data-population/instrumentation gap, not a silent-failure regression.

## Linear disposition

Keep GRO-3273 in In Review, remove the stale active-dispatch label `agent:ned`, and add a review/closure label if available so the dispatcher stops re-routing it to Ned. Human reviewer can mark Done after accepting the evidence.
