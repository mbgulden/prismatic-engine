---
title: Lease Semantics
version: v1
rf_slice: RF-1/RF-3
status: frozen
---

## Overview
This document outlines lease mechanics across verification (RF-2) and review (RF-3) slices to ensure robustness and concurrent processing.

## Lease Durations
Leases are constrained by Risk Tier:
- **Tier 0 (T0)**: 2 minutes
- **Tier 1 (T1)**: 15 minutes
- **Tier 2 (T2)**: 30 minutes
- **Tier 3 (T3)**: No lease (reserved for human-only processes)

## Recovery & Auto-Requeue
- **First Expiry**: Job is auto-requeued.
  - `verifying` → `queued`
  - `reviewing` → `review_ready`
- **Escalation**: On second consecutive expiry, the job is flagged for human intervention (escalated).

## Concurrency Limits
- **Reviewer Cap**: Max 3 concurrent reviewers per queue (configurable based on agent availability).
- **Dead-lock Prevention**: Queues are dynamically held and bypassed if a lease lock acquisition stalls for > 500ms.
- **Janitor Job**: `reset_stale_leases()` runs periodically (e.g., via a scheduler) to reset expired records safely.
