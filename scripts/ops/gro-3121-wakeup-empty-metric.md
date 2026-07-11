# GRO-3121 — wakeup-empty metric

## What changed

- Added `TelemetryCollector.record_wakeup_empty()` backed by the `telemetry_wakeup_empty` SQLite table.
- Added dashboard/factory-digest surface via `TelemetryCollector.get_dashboard_data()["wakeup_empty"]` with:
  - `count` — empty wakeups in the selected window
  - `per_hour` — empty wakeups / requested hours
  - `by_agent` — grouped empty-wakeup counts by agent
- Wired the dispatcher polling loop to record one `wakeup_empty` event when a cycle dispatches zero tasks and records zero errors.

## Baseline interpretation

Ned currently wakes on a 15-minute cadence: 4 wakeups/hour, 96 wakeups/day. The observed idle baseline from the issue is ~95 empty wakeups/day. After this change, the daily factory digest can report the measured value from `telemetry_wakeup_empty` instead of relying on that estimate.

## Verification

Run from the Prismatic Engine repo:

```bash
python3 -m pytest prismatic/test_gro3121_wakeup_empty.py -q
```

Expected result at implementation time: `15 passed`.
