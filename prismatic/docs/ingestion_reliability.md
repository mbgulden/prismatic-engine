# Ingestion reliability and idempotent dispatch

`prismatic.dispatcher.EventRouterDedup` maintains two ledgers:

- `dedup_log` remains the per-cycle compatibility log used by older dispatch logic.
- `dispatch_events` is the durable logical-event ledger keyed by `dispatch:<issue_id>:<agent_label>`.

The durable ledger gives the dispatcher restart-safe semantics:

1. `begin_dispatch_event(...)` returns `False` once an event has reached `processed`, so replaying the same Linear issue/agent label does not create duplicate launch/comment side effects.
2. Interrupted or failed attempts stay retryable. A later cycle can acquire the same event key again until it is marked `processed`.
3. `mark_dispatch_failed(...)` preserves the error text and attempt count for dead-letter/operator analysis.
4. `get_failed_dispatches()` exposes recent failed events for replay tooling or dashboards.

Targeted verification lives in `prismatic/tests/test_dispatcher_ingestion_reliability.py` and covers replay suppression, failed-event visibility, and a `dispatch_once()` replay across two cycles.
