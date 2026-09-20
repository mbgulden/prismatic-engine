# AGY Closeout Packet Contract Rollout & Legacy Cutoff

**Date**: 2026-08-04
**Status**: ACTIVE
**Required From Issue Threshold**: `GRO-4500`
**Manifest Key**: `required_from_issue_min: GRO-4500`

---

## 1. Overview

The `prismatic-agent-closeout-contract` v0.2 specification establishes a machine-enforced dual-artifact reporting standard (`RESULT.md` + `result-packet.json`) for AGY execution tasks.

To prevent pipeline freezing on legacy runs dispatched prior to v0.2 activation, a **legacy cutoff threshold** is established at issue identifier `GRO-4500`.

---

## 2. In-Flight Issue Audit & Exemption Policy

- **Cutoff Rule**:
  - Tasks with `issue_identifier < GRO-4500` are **EXEMPT** from strict v0.2 schema validation.
  - Tasks with `issue_identifier >= GRO-4500` MUST strictly adhere to the 25-field v0.2 JSON schema (`MARKER: AGY_TASK_RESULT_PACKET_OK`).

- **Database Audit Receipt** (Executed 2026-08-04 22:16 UTC):
  - Database: `prismatic_state/event_router.db` & `agy_completed_work.db`
  - Total In-Flight Pending Legacy Runs: `0`
  - Max In-Flight Issue ID: `N/A (clean slate)`

---

## 3. Runtime Gating Logic

In `prismatic/agy_result_packet.py` and `prismatic/agy_completed_work.py`:
- Task prompt generation automatically injects `templates/AGY_TASK_APPENDIX.md` into all newly generated tasks (`GRO-4500`+).
- Ingestion checks `issue_identifier`. If `issue_identifier < GRO-4500`, the packet bypasses strict v0.2 schema validation and routes via the legacy free-form completion handler.
