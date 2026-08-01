---
title: AGY Completed Work Contract
version: v1
rf_slice: RF-1
status: frozen
---

## Overview
This document defines the interface between `agy_completed_work.py` and the review factory intake process.

## Configuration
- `default_db_path()`: Returns the database path in the following priority order:
  1. `$PRISMATIC_AGY_COMPLETED_WORK_DB`
  2. `$PRISMATIC_STATE_DIR/agy_completed_work.db`
  3. `./prismatic_state/agy_completed_work.db`

## CompletedWorkRow Schema
- `id`: UUID
- `created_at`: Timestamp (UTC)
- `updated_at`: Timestamp (UTC)
- `agent`: string
- `source_branch`: string
- `source_path`: string
- `base_branch`: string
- `classification`: enum
- `eligible_for_merge`: boolean
- `requires_clean_rebuild`: boolean
- `proof_result`: string
- `proof_marker`: string
- `gate_marker`: string
- `ingestion_marker`: string
- `packet_json`: JSON
- `gate_json`: JSON
- `non_claims_json`: JSON
- `evidence_json`: JSON

## Intake Path
- `AgyCompletedWorkStore.ingest(packet)`: The canonical intake path for new completed work.
- `completed_work_id(packet)`: Generates deterministic hash IDs like `agy-cw-{digest}` based on packet contents.
- `normalize_agy_result_packet()`: Adapts raw AGY packets into the standardized schema.
- `retain_completed_work_evidence()`: Creates immutable evidence bundles for auditing.

## Factory Integration
The review factory reads from this store via SQL queries on the same database.
**CRITICAL**: All review factory tables (`review_jobs`, `verification_receipts`, etc.) MUST be stored in the SAME database file (`agy_completed_work.db`) to allow strict foreign keys and atomic transactions.
