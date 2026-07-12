# GRO-3485 — Replay-safe dead-letter handling

## What changed

Added `prismatic.dead_letter.DeadLetterStore`, a SQLite-backed failed-event store that:

- retains failed event payloads instead of dropping them;
- explicitly separates retryable rows (`retry` / `pending_retry`) from capped rows (`dead_letter`);
- supports replay discovery via `due_for_replay()` and success/failure state transitions without deleting payloads;
- deduplicates by `(event_id, source)` so webhook retries update one recoverable record instead of creating replay noise.

Added `scripts/prismatic_dead_letter.py` for operator-safe inspection:

- `list` prints retained failed events as JSON lines;
- `due` prints retry/dead-letter rows that can be replayed now;
- `mark-replayed` lets an operator mark a retained row after external re-enqueue/replay.

## Verification

Focused test target:

```bash
python3 -m pytest prismatic/test_dead_letter.py -q
```

Coverage in the focused test:

- failed items are retained with payload JSON in SQLite;
- retry vs dead-letter status is explicit at the attempt cap;
- re-recording the same event/source is idempotent;
- replay success keeps the payload for auditability;
- replay failure reschedules and then dead-letters at the cap;
- CLI `list` can recover retained rows without manual DB archaeology.
