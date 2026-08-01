---
title: Review Decisions Schema
version: v1
rf_slice: RF-3
status: frozen
---

## Overview
This document defines the `review_decisions` table, capturing the structured outcome of a code review by an agent or human.

## Table Schema: `review_decisions`
- `decision_id` (UUID, PK)
- `review_job_id` (UUID, FK to `review_jobs`)
- `reviewer_id` (str)
- `reviewer_capability_version` (str)
- `candidate_commit`, `candidate_tree` (str)
- `receipt_id` (UUID, FK to `verification_receipts`)
- `verdict` (enum: `clean`, `repair_required`, `rejected`)
- `findings` (JSON)
- `idempotency_key` (str, UNIQUE)
- `created_at` (timestamp UTC)

## Structures & Constraints
- **Idempotency Key**: Generated as `sha256(reviewer_id + candidate_tree + verdict)`. Guaranteed UNIQUE per decision to prevent duplicate reviews.
- **Findings JSON Structure**:
  ```json
  [
    {
      "severity": "string",
      "path": "string",
      "line": "int",
      "invariant": "string",
      "reproduction_command": "string"
    }
  ]
  ```
- **Receipt Binding**: Every decision MUST reference a valid `receipt_id` from `verification_receipts`, ensuring no review occurs without prior verification.
