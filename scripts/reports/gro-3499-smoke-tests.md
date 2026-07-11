# GRO-3499 — Ingest / Dispatch / State-Sync Smoke Tests

## Purpose

Add a small regression net for the live Prismatic task path without touching Linear or launching real agents.

## Smoke coverage

- **Ingest smoke**: `LocalTaskQueue.create()` persists a queued task into the durable SQLite queue.
- **Dispatch smoke**: `dispatch_local_tasks()` consumes a queued local task, calls the configured launcher, marks the task `dispatched`, and records the dedup marker.
- **State-sync smoke**: `AgentRunRecordStore` persists a run update to `completed` and a fresh store instance reloads that terminal state from disk.

## Test file

- `prismatic/tests/test_ingest_dispatch_sync_smoke.py`

## Verification command

```bash
python3 -m pytest prismatic/tests/test_ingest_dispatch_sync_smoke.py -q
```

## Lane note

The smoke test lives under `prismatic/tests/` so Ned can push it inside the `prismatic/` lane while still keeping it pytest-discoverable by explicit path.
