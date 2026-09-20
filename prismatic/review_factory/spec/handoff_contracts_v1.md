---
title: Handoff Contracts
version: v1
rf_slice: All
status: frozen
---

## Overview
This document defines the transitions and state handoffs between RF slices within the `PipelineStateMachine`.

## Handoff Flows
- **RF-1 → RF-2**: `ReviewJob` is created in `queued` state. A Verifier agent leases it and transitions state to `verifying`.
- **RF-2 → RF-3**: The Verifier completes validation. `VerificationReceipt` is written. Job transitions to `review_ready`.
- **RF-3 → RF-4**: Reviewer analyzes receipt + changes. Writes a `ReviewDecision` with `verdict=clean`. Job transitions to `merge_ready`.
- **RF-3 → RF-3 (Repair)**: Reviewer writes `ReviewDecision` with `verdict=repair_required`. Creates a `RepairPacket`. Job transitions back to `queued`.
- **RF-4 → RF-5**: A `MergeAuthorization` is created. The dashboard starts showing pending merges for human execution or tracking.
- **RF-6 → RF-1**: Backlog manifest processor reads backlog, performs a batch enqueue into the queue.

## PR Reviewer Adapter
- `PreliminaryReviewAdapter`: Wraps `pr_reviewer.py` (`PRReviewer.review_pr()`).
- Output Mapping:
  - `APPROVE` → `clean`
  - `REQUEST_CHANGES` → `repair_required`
  - `NEEDS_DISCUSSION` → `rejected`

## Pipeline Integration
- The Review Factory orchestrates these tasks explicitly during `Step.REVIEW` (Step 3) in the `PipelineStateMachine`.
