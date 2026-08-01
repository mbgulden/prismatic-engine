---
title: Merge Authorizations Schema
version: v1
rf_slice: RF-4
status: frozen
---

## Overview
This document defines the `merge_authorizations` table, controlling the final step before code is merged. Review completion does NOT automatically confer merge authority.

## Table Schema: `merge_authorizations`
- `authorization_id` (UUID, PK)
- `review_job_id` (UUID, FK to `review_jobs`)
- `repository` (str)
- `pr_number` (str)
- `pr_head_commit`, `pr_base_commit` (str)
- `candidate_tree`, `expected_merge_tree` (str)
- `policy_version` (str)
- `actor` (str)
- `scope` (enum: `tier-0-auto`, `tier-1-auto`, `tier-2-exception`, `tier-3-exception`)
- `expires_at` (timestamp UTC)
- `consumed_at` (timestamp UTC, nullable)
- `idempotency_key` (str)

## Authority Policy
- **Auto-Authorization**: Tier 0 and Tier 1 automatically generate a `merge_authorization` record (`scope: tier-X-auto`). NO human intervention ("Michael click") required.
- **Human Exception**: Tier 2+ explicitly requires human authorization via CLI:
  `python -m prismatic.merge_executor approve <id>`
- **Lifecycle**: 
  - `expires_at` restricts the validity window of an authorization.
  - `consumed_at` is set idempotently when the merge executor acts upon the authorization.
