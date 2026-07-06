# GRO-3534 — Quota freshness and failure handling

## Summary

Implemented pane-facing quota sync state so the Vertex quota surface can show:

- last successful quota sync timestamp;
- last sync attempt timestamp, including failed attempts;
- latest failure timestamp and failure message when the newest attempt failed;
- stale/sync-failed booleans for dashboard status chips; and
- explicit operator retry/refresh actions.

## Files changed

- `prismatic/vertex_telemetry.py`
  - Added defensive ISO timestamp parsing for freshness comparisons.
  - Extended `get_status_summary()` with `quota_freshness` and `quota_sync` fields for pane rendering.
  - Marks `sync_failed=true` when the newest quota attempt is an error newer than the latest successful snapshot.
  - Adds retry/refresh command hints: `python3 -m prismatic.vertex_telemetry poll` and `python3 -m prismatic.vertex_telemetry check`.
  - Prints last sync, last attempt, failure message, and retry command in the CLI `check` output.
- `prismatic/test_vertex_telemetry_integrity.py`
  - Covers successful freshness metadata, retry/refresh hints, and failure-only ledgers with no quota records.

## Verification

Focused verification command:

```bash
PYTHONPATH=. pytest prismatic/test_vertex_telemetry_integrity.py -q
```

Expected scope: focused regression coverage for the quota pane payload and CLI-visible failure handling. This is the same-commit documentation update required for the implementation task.
