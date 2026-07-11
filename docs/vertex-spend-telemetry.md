# Vertex spend telemetry

Prismatic records Vertex quota polling into the shared engine telemetry database
(`event_router.db` by default):

1. `VertexBillingLedger` writes durable quota snapshots to
   `gcp_vertex_billing_ledger` and `gcp_vertex_quota_snapshots`.
2. `TelemetryCollector.record_vertex_spend()` writes spend rows to the existing
   `gcp_vertex_spend_events` table using the same canonical Vertex ledger
   schema.

The writer makes this operational query meaningful after a quota poll:

```sql
SELECT COUNT(*)
FROM gcp_vertex_spend_events
WHERE recorded_at > datetime('now','-7 days');
```

## Schema compatibility

`TelemetryCollector` creates the minimal Vertex billing tables it needs when it
is pointed at a fresh telemetry database. The schema is intentionally compatible
with `prismatic.vertex_telemetry.LEDGER_SCHEMA`:

- `gcp_vertex_billing_ledger` stores `project` and `credits`.
- `gcp_vertex_spend_events` stores `ledger_id`, `model`, `region`,
  `tpm_used`, `rpm_used`, `context_pct`, `estimated_cost`, `operation`, and
  `recorded_at`.

`record_vertex_spend()` inserts a billing-ledger row first, then inserts the
spend event with that `ledger_id`. This avoids a table-name/schema collision
when `VertexBillingLedger` and `TelemetryCollector` use the same default DB.

## Quota polling wiring

`VertexBillingLedger.record_quota_snapshot()` commits quota snapshots first, then
best-effort emits one `record_vertex_spend()` event per quota record.

Mapping:

- billing `project`: `project_id`
- billing `credits`: `utilization_pct / 100.0`
- spend `operation`: `quota_poll_<metric_type>`
- spend `context_pct`: `utilization_pct / 100.0`
- spend `tpm_used`: quota `usage` when `metric_type == "tpm"`
- spend `rpm_used`: quota `usage` when `metric_type == "rpm"`

Telemetry failures are swallowed after the quota snapshot commit. The Vertex
quota ledger is the load-bearing path; telemetry must not break it.
