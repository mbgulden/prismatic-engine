# GRO-3484 — Queue processing idempotency

## Change summary

The SQLite dispatch consumer now has a persistent replay ledger in addition to the existing per-row `processed` marker.

## Contract

- **Dedup key exists:** `events.dedup_key` remains the primary stable bus-event key; legacy rows without a key receive a deterministic `legacy:<topic>:<sha256(payload)>` key.
- **Processed marker exists:** `events.processed` is created/migrated by `ensure_schema()` and is set to `1` for processed or replay-skipped rows.
- **Replay safety:** `processed_event_keys` stores claimed/processed keys before supervisor spawn. Reprocessing the same event key after a restart or manual `processed=0` reset skips side effects.

## Files

- `prismatic/gateway/event_handlers/dispatch_consumer_v3.py`
- `prismatic/gateway/event_handlers/test_dispatch_consumer_v3_idempotency.py`
