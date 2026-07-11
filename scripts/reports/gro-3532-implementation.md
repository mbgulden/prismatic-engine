# GRO-3532 — Enterprise quota and telemetry integrity

## Scope

This implementation hardens `prismatic.vertex_telemetry` so production quota panes receive normalized records instead of raw Cloud Quotas payload fragments.

## Contract covered

- Quota payload normalization now accepts multiple Cloud Quotas shapes:
  - model identifiers from dimensions (`model`, `base_model`, `baseModel`, etc.)
  - usage from top-level fields or `metricInfos`
  - limit values from top-level fields or `limits[*]`
- Nullish values are stripped recursively before records/errors are exposed or persisted:
  - `None`
  - empty strings
  - string sentinels: `undefined`, `null`, `none`, `nan`
- Freshness is now explicit:
  - normalized quota records include `recorded_at`
  - `get_status_summary()` exposes `generated_at`
  - `quota_freshness.last_recorded_at`, `age_seconds`, and `stale`
  - Prometheus text includes `prismatic_vertex_quota_last_recorded_timestamp_seconds`
- Error handling is now explicit:
  - `poll_vertex_quota_status()` returns both normalized `records` and `errors`
  - `VertexBillingLedger.record_quota_errors()` persists poll errors to `gcp_vertex_poll_errors`
  - `get_status_summary()` exposes recent `latest_errors`
  - Prometheus text includes `prismatic_vertex_quota_poll_errors_total`
- Existing ledgers are migrated safely by adding `gcp_vertex_quota_snapshots.raw_payload` if missing.

## Verification

Focused regression coverage lives in `prismatic/test_vertex_telemetry_integrity.py` so the verification artifact stays inside Ned's lane:

1. normalizer strips null/undefined payload fields and adds freshness;
2. poll status returns explicit per-location errors while still returning successful records;
3. ledger summary/metrics expose freshness, recent errors, and sanitized persisted payloads.

Run:

```bash
python3 -m pytest prismatic/test_vertex_telemetry_integrity.py -q
```
