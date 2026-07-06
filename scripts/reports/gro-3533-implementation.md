# GRO-3533 — Quota payload normalization

## Summary

Implemented pane-safe Vertex quota rows so every model/metric record now includes one of:

- `remaining_value` when Cloud Quotas provides a positive limit; or
- `unavailable_reason` when a remaining value cannot be computed because the limit is missing/zero.

The normalization path also recursively strips `None`, empty strings, and string nullish sentinels (`undefined`, `null`, `none`, `nan`) before quota records, raw payloads, errors, or status summaries can reach the dashboard surface.

## Files changed

- `prismatic/vertex_telemetry.py`
  - Added `remaining_value` / `unavailable_reason` to normalized quota records.
  - Computes `remaining_value` for persisted historical rows returned by `get_latest_quota()` without requiring a schema migration.
  - Preserves explicit quota poll errors and strips nullish raw payload fields before persistence.
- `prismatic/test_vertex_telemetry_integrity.py`
  - Covers nullish stripping, computed remaining quota, unavailable-limit explanation, explicit poll errors, persisted summary rows, and sanitized raw payload storage.

## Verification planned

Focused verification command:

```bash
PYTHONPATH=. pytest prismatic/test_vertex_telemetry_integrity.py -q
```

This report is the same-commit documentation update required for the implementation task.
