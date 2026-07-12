# GRO-3500 — Zero-execution and stale-queue alert contract

## Scope

`prismatic.gateway.alert_manager.AlertEvaluator` now treats a zero-execution window as a first-class critical alert and annotates the failing layer instead of only saying that a stall occurred.

## Rules

| Rule | Condition | Failing layer hint |
| --- | --- | --- |
| `AgentStall` | `total_agent_runs == 0` for the evaluation window | `execution/consumer` when queued work exists; otherwise `telemetry/execution` |
| `StaleQueue` | `linear_webhook_queue` has stale rows or the oldest pending/stale row exceeds `PRISMATIC_ALERT_STALE_QUEUE_MINUTES` (default: 15 minutes). This is emitted by `AlertEvaluator` and routed normally; it does not change the legacy `/alerts/rules` four-rule listing. | `queue/dispatcher` |

## Evidence fields

Alert details include:

- `failing_layer=...`
- `pending_queue_depth=...`
- `stale_queue_depth=...`
- `oldest_pending_age_sec=...` for stale queue alerts
- `threshold_sec=...` for stale queue alerts

## Verification

Focused pytest target:

```bash
pytest tests/test_alert_manager.py -q
```

The tests cover zero-execution windows, stale queue alerts, and SQLite fallback inspection of `linear_webhook_queue.db`.
