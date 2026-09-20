---
title: Review Jobs Schema
version: v1
rf_slice: RF-1
status: frozen
---

## Overview
This document defines the `review_jobs` table, the central state tracking mechanism for the review factory.

## Table Schema: `review_jobs`
- `review_job_id` (UUID v4, PK)
- `completed_work_id` (UUID, FK to `agy_completed_work`)
- `task_id` (str, e.g., GRO-4188)
- `repository` (str)
- `base_commit`, `base_tree` (str, SHA)
- `candidate_commit`, `candidate_tree` (str, SHA)
- `result_packet_path` (str)
- `result_packet_sha256` (str)
- `changed_paths_json` (JSON)
- `risk_tier` (int, 0/1/2/3)
- `policy_version` (str)
- `state` (enum)
- `required_witnesses` (int, 0/1/2)
- `completed_witnesses` (int)
- `created_at` (timestamp UTC)
- `lease_owner` (str)
- `lease_expires_at` (timestamp UTC)

## State Machine
Transitions are strictly idempotent:
- `queued` → `verifying`
- `verifying` → `review_ready` (success)
- `verifying` → `queued` (timeout/crash)
- `review_ready` → `reviewing`
- `reviewing` → `merge_ready` (verdict=clean)
- `reviewing` → `repair_required` (verdict=repair_required)
- `reviewing` → `rejected` (verdict=rejected)
- `reviewing` → `review_ready` (timeout)
- `repair_required` → `queued` (repair consumed)
- `merge_ready` → `merge_authorized`
- `merge_authorized` → `merging`
- `merging` → `merged`
- `merging` → `merge_verification_failed`

## Lease Semantics & Risk Tiers
- Tier 0 (T0): 2 min lease.
- Tier 1 (T1): 15 min lease.
- Tier 2 (T2): 30 min lease.
- Tier 3 (T3): N/A (human review required).

Risk tier classification dictates `required_witnesses` (e.g., T2/T3 require human or advanced witnesses).
