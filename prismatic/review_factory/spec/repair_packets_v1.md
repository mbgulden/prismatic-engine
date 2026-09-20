---
title: Repair Packets Schema
version: v1
rf_slice: RF-3
status: frozen
---

## Overview
This document defines the `repair_packets` table, handling feedback returned to producer agents when a review yields a `repair_required` verdict.

## Table Schema: `repair_packets`
- `packet_id` (UUID, PK)
- `candidate_tree` (str)
- `findings_json` (JSON)
- `producer_id` (str)
- `consumed_at` (timestamp UTC, nullable)
- `resolution_attempt_n` (int)
- `created_at` (timestamp UTC)

## Contract
- **Consumption**: The packet is consumed by the original `producer_id`. The work goes back to the agent who produced it.
- **Resolution Counter**: `resolution_attempt_n` tracks the number of repair cycles for a given `completed_work_id`. This serves to break infinite loops.
- **Findings Structure**: `findings_json` strictly matches the schema in `review_decisions.findings` `[{severity, path, line, invariant, reproduction_command}]`.
- **Cycle Flow**: `reviewing` -> `repair_required` -> Generates `repair_packet`. Once packet is consumed -> new candidate ingested -> state back to `queued`.
