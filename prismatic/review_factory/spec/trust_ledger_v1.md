---
title: Trust Ledger Schema
version: v1
rf_slice: RF-6
status: frozen
---

## Overview
This document defines the `trust_ledger_events` table, the append-only
record of merge outcomes, rollbacks, pause resolutions, tier changes, and
brake pulls that backs earned-autonomy tier decisions. The ledger is
DATA ONLY: nothing here changes merge behavior. Promotion is always
Michael's explicit decision; the ledger only records it and proposes.

## Table Schema: `trust_ledger_events`
- `event_id` (UUID hex, PK)
- `ts` (timestamp UTC, ISO-8601)
- `event_type` (enum, see below)
- `artifact_id` (str, nullable — e.g. review_job_id or merge sha)
- `change_class` (enum, nullable — docs | chore | dep_bump | agent_standard | sensitive | production)
- `tier_at_event` (int, nullable — tier relevant at event time)
- `merged_by` (enum, nullable — auto | michael)
- `deterministic_verdict` (str, nullable — e.g. CLEAN)
- `judgment` (JSON string, nullable — structured context, e.g. `{"judge": "jev", "decision": "PAUSE", "agreed": true}`)
- `notes` (str)

## Event Types
- `merge_completed` — a merge finished; carries change_class, merged_by, deterministic_verdict.
- `rollback_detected` — a merged change was rolled back.
- `human_revert` — Michael (or a human) reverted a change.
- `pause_resolved` — a Jev judge pause was resolved; judgment carries `{"judge", "decision": "PAUSE", "agreed"}`.
- `tier_promoted` — an explicit tier promotion; judgment carries `{"to_tier", "approver"}`; approver must be a named human.
- `tier_revoked` — a mechanical tier drop; judgment carries `{"from_tier", "to_tier"}`; tier_at_event is the new tier.
- `brake_pulled` — a manual brake pull (promotion freeze; any tier drop is a separate `tier_revoked`).

## Derivation Rules
- **Tier fold**: start at tier 0. `tier_promoted` sets the current tier to
  its `to_tier`; `tier_revoked` sets it to its `to_tier`. Last event wins.
  No event in the ledger may ever set a tier directly (no bare-state tier
  setter exists on the ledger class by design).
- **Clean streaks**: `consecutive_clean_by_class` counts `merge_completed`
  events per class since the last `rollback_detected` or `human_revert`
  of ANY class — one rollback resets every class.
- **Promotion freeze**: each `tier_revoked` freezes promotion until
  `ts + 7 days`. `promotion_freeze_until` is the max over revocations,
  reported only while still in the future.
- **Pause precision**: `agreed / total` over the trailing 30
  `pause_resolved` events; `None` when there are none.
- **Windows**: `rollback_count_30d`, `pauses_trailing_30`,
  `agreed_pauses_trailing_30`, and `revocation_count_30d` are counts over
  the trailing 30 days from "now".

## Graduation Thresholds
`check_graduation()` never changes the tier; it returns a proposal dict
(with `requires_michael: true`) or `None`. Graduation requires:
- no active promotion freeze,
- `clean >= threshold` AND `rollback_count_30d == 0`, where `clean` is the
  UNION streak across the tier's qualifying classes
  (`sum` of `consecutive_clean_by_class` over those classes — all classes
  reset at the same rollback point, so the sum is the unbroken streak),
- plus for T1 -> T2: pause_precision >= 0.80 and pauses_trailing_30 >= 10,
- plus for T2 -> T3: pause_precision >= 0.85 and agreed_pauses_trailing_30 >= 2.

| to_tier | threshold | qualifying classes |
|---------|-----------|--------------------|
| 1       | 20        | docs, chore, dep_bump |
| 2       | 30        | agent_standard |
| 3       | 50        | agent_standard |

## Revocation Rules
- `rollback_detected` with `auto_merged=True` (default) and
  `human_revert` with `auto_merged=True` (default) immediately record a
  `tier_revoked` event: one tier down by default (floor 0), or to an
  explicit `to_tier`.
- Manual `revoke(reason, to_tier=None)` drops one tier (or to the target).
- Every revocation triggers the 7-day promotion freeze above.

## JSONL Mirror Convention
The ledger mirrors every event to `<audit_dir>/trust-ledger.jsonl`
(`$PRISMATIC_AUDIT_DIR`, else `~/.prismatic/audit`), one JSON object per
line, append-only. The SQLite table is authoritative: if the mirror write
fails, the DB row still commits and the returned event carries
`"mirror_ok": false`.
