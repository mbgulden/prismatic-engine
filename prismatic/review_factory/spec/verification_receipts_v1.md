---
title: Verification Receipts Schema
version: v1
rf_slice: RF-2
status: frozen
---

## Overview
This document defines the `verification_receipts` table, representing the outcome of an automated or manual verification run.

## Table Schema: `verification_receipts`
- `receipt_id` (UUID, PK)
- `review_job_id` (UUID, FK to `review_jobs`)
- `candidate_commit`, `candidate_tree` (str)
- `immutable_archive_id` (str)
- `commands` (JSON)
- `exit_codes` (JSON)
- `log_paths` (JSON)
- `log_sha256` (JSON)
- `changed_path_invariance_proof` (str)
- `classification` (enum: `targeted`, `bounded_regression`, `canonical_suite`, `package_wheel`, `browser`, `production`)
- `explicit_non_claims` (JSON)
- `baseline_failures` (JSON)
- `created_at` (timestamp UTC)

## Contract & Invariants
- **Immutability**: Receipts are write-once. They are never updated.
- **Invariance Proof**: `changed_path_invariance_proof` proves that the verification ran exactly on the candidate tree without mutating the worktree.
- **Honesty in Claims**: `explicit_non_claims` strictly documents what was NOT tested, ensuring transparency.
- **Baseline Accuracy**: `baseline_failures` must record pre-existing failures. **Never** call baseline rot "canonical green".
- **Anti-pattern**: NEVER mutate the producer's worktree. The verifier must operate on a fresh, immutable archive.
