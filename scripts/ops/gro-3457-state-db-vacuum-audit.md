# GRO-3457 — State Database Vacuuming & Optimization Audit

Generated: 2026-07-05 20:39:55Z

Scope: metadata-only SQLite audit of Prismatic Engine state databases and the two largest Hermes profile state databases that affect the Ned/orchestrator fleet. No live database was modified; this pass did **not** run `VACUUM`, `ANALYZE`, `PRAGMA optimize`, or checkpoints.

## Executive summary

- Audited 14 existing SQLite DBs; 0 candidate paths missing.
- Largest files: `state.db` 4.6 GiB, `state.db` 1.1 GiB, `event_router.db` 11.6 MiB, `linear_budget.db` 5.6 MiB, `linear_webhooks.db` 4.6 MiB.
- Biggest risk: Hermes profile `state.db` files are multi-GiB; use `VACUUM INTO` against a copy/backup first, not blind in-place vacuum during cron.
- Prismatic Engine `prismatic_state/*.db` files are small to moderate; no metadata evidence of urgent bloat. `event_router.db` and `linear_budget.db` deserve routine monitoring because they are the only Engine DBs above 1 MiB.

## Audit table

| Database | Size | WAL/SHM | Journal | Pages | Freelist | Tables | Indexes | Recommendation |
|---|---:|---:|---|---:|---:|---:|---:|---|
| `/home/ubuntu/.hermes/profiles/orchestrator/state.db` | 4.6 GiB | 0 B / 64.0 KiB | `delete` | 1217733 | 670781 (2.6 GiB) | 17 | 12 | Large DB: schedule low-traffic backup + VACUUM INTO trial before any in-place VACUUM.<br>High freelist (55.1%); candidate for VACUUM/VACUUM INTO after backup. |
| `/home/ubuntu/.hermes/profiles/ned/state.db` | 1.1 GiB | 422.5 KiB / 96.0 KiB | `delete` | 300852 | 441 (1.7 MiB) | 17 | 12 | Large DB: schedule low-traffic backup + VACUUM INTO trial before any in-place VACUUM. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db` | 11.6 MiB | 0 B / 0 B | `delete` | 2976 | 0 (0 B) | 28 | 32 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/linear_budget.db` | 5.6 MiB | 0 B / 0 B | `delete` | 1445 | 0 (0 B) | 2 | 0 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/.hermes/profiles/orchestrator/state/event-router/linear_webhooks.db` | 4.6 MiB | 0 B / 0 B | `delete` | 1190 | 0 (0 B) | 1 | 3 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_state/linear_budget.db` | 728.0 KiB | 0 B / 0 B | `delete` | 182 | 0 (0 B) | 2 | 0 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/.hermes/profiles/ned/verification_evidence.db` | 108.0 KiB | 0 B / 0 B | `delete` | 27 | 0 (0 B) | 3 | 1 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/event_bus.db` | 80.0 KiB | 0 B / 0 B | `delete` | 20 | 0 (0 B) | 2 | 0 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/curator_state.sqlite` | 36.0 KiB | 0 B / 32.0 KiB | `delete` | 9 | 0 (0 B) | 3 | 2 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/dedup.db` | 20.0 KiB | 0 B / 32.0 KiB | `delete` | 5 | 0 (0 B) | 1 | 2 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/curator_metrics.db` | 20.0 KiB | 0 B / 0 B | `delete` | 5 | 0 (0 B) | 2 | 0 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/linear_webhook_queue.db` | 16.0 KiB | 0 B / 0 B | `delete` | 4 | 0 (0 B) | 1 | 0 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/plugin_lifecycle.db` | 12.0 KiB | 0 B / 0 B | `delete` | 3 | 0 (0 B) | 1 | 0 | No immediate vacuum/index emergency from metadata-only audit. |
| `/home/ubuntu/work/prismatic-engine/prismatic_state/lane_queue.db` | 12.0 KiB | 0 B / 0 B | `delete` | 3 | 0 (0 B) | 1 | 0 | No immediate vacuum/index emergency from metadata-only audit. |

## Index inventory notes

### `/home/ubuntu/.hermes/profiles/orchestrator/state.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `39`.
- Explicit index sample: `idx_compression_locks_expires` on `compression_locks`, `idx_messages_platform_msg_id` on `messages`, `idx_messages_session` on `messages`, `idx_messages_session_active` on `messages`, `idx_sessions_gateway_peer` on `sessions`, `idx_sessions_handoff_state` on `sessions`, `idx_sessions_parent` on `sessions`, `idx_sessions_session_key` on `sessions`, `idx_sessions_source` on `sessions`, `idx_sessions_source_id` on `sessions`, `idx_sessions_started` on `sessions`, `idx_sessions_title_unique` on `sessions`.

### `/home/ubuntu/.hermes/profiles/ned/state.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `39`.
- Explicit index sample: `idx_compression_locks_expires` on `compression_locks`, `idx_messages_platform_msg_id` on `messages`, `idx_messages_session` on `messages`, `idx_messages_session_active` on `messages`, `idx_sessions_gateway_peer` on `sessions`, `idx_sessions_handoff_state` on `sessions`, `idx_sessions_parent` on `sessions`, `idx_sessions_session_key` on `sessions`, `idx_sessions_source` on `sessions`, `idx_sessions_source_id` on `sessions`, `idx_sessions_started` on `sessions`, `idx_sessions_title_unique` on `sessions`.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `72`.
- Explicit index sample: `idx_agy_live_run` on `agy_live_state`, `idx_agy_live_time` on `agy_live_state`, `idx_billing_mapping_client` on `billing_mapping`, `idx_billing_mapping_project` on `billing_mapping`, `idx_bridge_subscriptions_client` on `bridge_subscriptions`, `idx_dedup_cycle` on `dedup_log`, `idx_vertex_billing_project` on `gcp_vertex_billing_ledger`, `idx_vertex_billing_time` on `gcp_vertex_billing_ledger`, `idx_quota_snapshots_lookup` on `gcp_vertex_quota_snapshots`, `idx_quota_snapshots_region_model` on `gcp_vertex_quota_snapshots`, `idx_vertex_spend_model` on `gcp_vertex_spend_events`, `idx_label_snapshots_issue` on `label_snapshots` …
- Largest dbstat objects: `telemetry_media_artifacts` 3.9 MiB, `telemetry_credit_ledger` 3.1 MiB, `sqlite_autoindex_telemetry_media_artifacts_1` 2.1 MiB, `idx_media_detected_at` 908.0 KiB, `idx_credit_ledger_run` 852.0 KiB, `lane_budgets` 372.0 KiB, `telemetry_agent_runs` 68.0 KiB, `idx_agent_runs_agent` 32.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/linear_budget.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `3`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `budget_logs` 5.6 MiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_budget_state_1` 4.0 KiB, `budget_state` 4.0 KiB.

### `/home/ubuntu/.hermes/profiles/orchestrator/state/event-router/linear_webhooks.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `6`.
- Explicit index sample: `idx_linear_events_hash` on `linear_events`, `idx_linear_events_identifier` on `linear_events`, `idx_linear_events_status` on `linear_events`.
- Largest dbstat objects: `linear_events` 4.1 MiB, `sqlite_autoindex_linear_events_2` 168.0 KiB, `idx_linear_events_hash` 168.0 KiB, `sqlite_autoindex_linear_events_1` 164.0 KiB, `idx_linear_events_status` 44.0 KiB, `idx_linear_events_identifier` 40.0 KiB, `sqlite_schema` 4.0 KiB.

### `/home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_state/linear_budget.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `3`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `budget_logs` 716.0 KiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_budget_state_1` 4.0 KiB, `budget_state` 4.0 KiB.

### `/home/ubuntu/.hermes/profiles/ned/verification_evidence.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `7`.
- Explicit index sample: `idx_verification_events_session_root` on `verification_events`.
- Largest dbstat objects: `verification_events` 48.0 KiB, `verification_state` 28.0 KiB, `sqlite_autoindex_verification_state_1` 12.0 KiB, `sqlite_sequence` 4.0 KiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_meta_1` 4.0 KiB, `meta` 4.0 KiB, `idx_verification_events_session_root` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/event_bus.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `4`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `events` 64.0 KiB, `subscription_cursors` 4.0 KiB, `sqlite_sequence` 4.0 KiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_subscription_cursors_1` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/curator_state.sqlite`
- `auto_vacuum=0`, `user_version=0`, schema objects `8`.
- Explicit index sample: `idx_tagged_event_rowid` on `tagged_events`, `idx_tagged_tag` on `tagged_events`.
- Largest dbstat objects: `tagged_events` 4.0 KiB, `sqlite_sequence` 4.0 KiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_lane_stats_1` 4.0 KiB, `sqlite_autoindex_digest_runs_1` 4.0 KiB, `lane_stats` 4.0 KiB, `idx_tagged_tag` 4.0 KiB, `idx_tagged_event_rowid` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/dedup.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `4`.
- Explicit index sample: `idx_processed_events_expires` on `processed_events`, `idx_processed_events_type` on `processed_events`.
- Largest dbstat objects: `sqlite_schema` 4.0 KiB, `sqlite_autoindex_processed_events_1` 4.0 KiB, `processed_events` 4.0 KiB, `idx_processed_events_type` 4.0 KiB, `idx_processed_events_expires` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/curator_metrics.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `4`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `sqlite_sequence` 4.0 KiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_curator_runs_1` 4.0 KiB, `curator_runs` 4.0 KiB, `audit_runs` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/linear_webhook_queue.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `3`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `sqlite_sequence` 4.0 KiB, `sqlite_schema` 4.0 KiB, `sqlite_autoindex_linear_webhook_queue_1` 4.0 KiB, `linear_webhook_queue` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/plugin_lifecycle.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `2`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `sqlite_schema` 4.0 KiB, `sqlite_autoindex_plugin_states_1` 4.0 KiB, `plugin_states` 4.0 KiB.

### `/home/ubuntu/work/prismatic-engine/prismatic_state/lane_queue.db`
- `auto_vacuum=0`, `user_version=0`, schema objects `2`.
- No explicit non-SQLite auto-indexes found in schema sample.
- Largest dbstat objects: `sqlite_schema` 4.0 KiB, `sqlite_autoindex_lane_queue_1` 4.0 KiB, `lane_queue` 4.0 KiB.

## Recommended maintenance plan

1. **Do not vacuum live multi-GiB Hermes `state.db` files from cron.** First run `sqlite3 state.db "VACUUM INTO '/tmp/state-vacuum-trial.db'"` on a copied/offline DB or during a maintenance window, then compare size and `PRAGMA integrity_check`.
2. **Engine DBs:** add a lightweight weekly metadata probe for `prismatic_state/event_router.db`, `linear_budget.db`, and `event_bus.db`: `page_count`, `freelist_count`, WAL size, and top dbstat objects. Escalate if freelist exceeds 10% and >100 MiB reclaimable.
3. **Index review:** DBs with several tables and zero explicit indexes (`event_bus.db`, `curator_state.sqlite`, `linear_webhook_queue.db`, `plugin_lifecycle.db`, `lane_queue.db`) are fine at current size, but should get query-specific indexes before they become hot paths.
4. **Checkpoint policy:** no oversized WAL was observed in Engine DBs. Ned profile `state.db-wal` was ~117 MiB; if it grows beyond a few hundred MiB, review the writer/checkpoint cadence rather than deleting WAL files.

## Verification

- Audit collection used read-only SQLite URIs with `immutable=1` and did not execute mutating PRAGMAs.
- Large DBs were not fully scanned with `dbstat`; only metadata PRAGMAs were collected to avoid production impact.
- Smaller Engine DBs used `dbstat` for top-object sizing where available.
