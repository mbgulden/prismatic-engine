# GRO-3498 — Webhook queue replay/backfill mode

## Summary

`scripts/drain_webhook_queue.py` now has a bounded replay/backfill path for missed bus events and failed dispatch attempts.

## Operator contract

Use:

```bash
python3 scripts/drain_webhook_queue.py --backfill --since <unix-start> --until <unix-end> --max <N>
```

Behavior:

- Normal drain mode still processes only `pending` rows.
- `--backfill` replays `pending`, `stale`, and `failed:*` rows without first reclassifying old pending rows as stale.
- `--since` and `--until` bound the replay by `linear_webhook_queue.received_at` Unix timestamps.
- Replay remains idempotent because the dispatch path still goes through `dispatch_issue_by_identifier`, which applies the existing dispatch/dedup gates before launching an agent.
- Rows outside the requested range are left untouched.

## Verification

Focused pytest:

```bash
python3 -m pytest scripts/test_drain_webhook_queue_backfill.py -q
```

Coverage in that test:

- backfill selection includes `stale`, `failed:*`, and `pending` rows;
- bounded replay only selects rows inside the requested timestamp window;
- replay dispatches through an injected dispatcher function and leaves out-of-window stale/failed rows untouched;
- `--help` documents `--backfill`, `--since`, and `--until`.
