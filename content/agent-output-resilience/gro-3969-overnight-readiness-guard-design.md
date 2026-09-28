# AGY limited overnight readiness guard design

Linear: GRO-3969
Acceptance marker: `OVERNIGHT_READINESS_GUARD_DESIGN_OK`

## Purpose

This document is the operator-facing design/API contract for the limited unattended AGY overnight guard. It does **not** authorize or enable autonomous production execution by itself. The guard only evaluates whether a separately approved limited dry-run packet is safe to start.

## Safe default policy

The machine-readable defaults live in `prismatic/agy_overnight_guard.py` as `OvernightGuardPolicy` and are surfaced by the CLI/API as JSON.

```json
{
  "allowed_agents": ["agy"],
  "max_tasks_per_run": 2,
  "max_consecutive_failures": 1,
  "stop_on_first_failure": true,
  "auto_merge_enabled": false,
  "production_deploy_enabled": false,
  "real_github_pr_create_enabled": false,
  "requires_one_task_success": true,
  "requires_gateway_healthy": true,
  "requires_ingestion_healthy": true,
  "requires_merge_backlog_healthy": true,
  "requires_verification_gate_healthy": true,
  "requires_operator_pause_control": true,
  "requires_operator_summary": true
}
```

Additional hard restrictions:

- `max_tasks` requests above `2` are blocked.
- Any requested agent outside `agy` is blocked.
- `auto_merge=true` is blocked.
- `production_deploy=true` is blocked.
- `real_github_pr_create=true` is blocked.
- `bulk_dispatch=true` is blocked.
- Operator pause blocks readiness until resumed.
- Missing completed-work ingestion, merge-backlog health, gateway health, verification health, or the one-task dry-run success marker blocks readiness.

## CLI contract

Script: `scripts/agy_overnight_guard.py`

Commands:

```bash
python3 scripts/agy_overnight_guard.py status --limit 10
python3 scripts/agy_overnight_guard.py evaluate --max-tasks 1 --agent agy --requested-by fred
python3 scripts/agy_overnight_guard.py pause
python3 scripts/agy_overnight_guard.py resume
```

Guarantees:

- `status` evaluates and persists a guard decision; it launches zero tasks.
- `evaluate` persists a dry-run guard decision; it launches zero tasks.
- `pause` and `resume` only toggle operator pause state.
- Output includes `marker`, `guard`, `persisted_decision`, `operator_pause`, and `non_claims`.
- Output explicitly reports `tasks_launched: 0` where applicable.

## Gateway/API contract

Gateway endpoints in `prismatic/gateway/server.py`:

| Method | Path | Purpose | Side effects |
| --- | --- | --- | --- |
| `GET` | `/api/gateway/agy/overnight-guard` | Return current guard status, persist decision, include recent guard runs | No task launch |
| `POST` | `/api/gateway/agy/overnight-guard/evaluate` | Evaluate request body against policy and persist dry-run decision | No task launch |
| `POST` | `/api/gateway/agy/overnight-guard/pause` | Operator pause on | No task launch |
| `POST` | `/api/gateway/agy/overnight-guard/resume` | Operator pause off | No task launch |
| `GET` | `/api/gateway/agy/overnight-guard/runs` | List persisted guard run attempts | No task launch |

Accepted evaluate body fields:

```json
{
  "requested_by": "fred",
  "allowed_agents": ["agy"],
  "max_tasks": 1,
  "auto_merge": false,
  "production_deploy": false,
  "real_github_pr_create": false,
  "bulk_dispatch": false,
  "gateway_healthy": true,
  "operator_summary_required": true,
  "required_preflight_ok": true
}
```

Response invariants:

- `marker` is `AGY_OVERNIGHT_READINESS_GUARD_OK` only when all policy gates pass.
- Blocked responses include exact blocker strings in `guard.blockers`.
- `dry_run: true` and `tasks_launched: 0` are returned from evaluate.
- `non_claims` must keep all production/automerge/bulk-dispatch claims false.

## Dashboard/operator controls

The dashboard should expose the following read/write controls using the API above:

1. **Status panel**
   - Readiness state: `ready` or `blocked`.
   - Marker and reason.
   - Current blockers/warnings.
   - Latest one-task success marker.
   - Latest completed-work row and merge-backlog row IDs.
   - Recent guard decisions/runs.

2. **Policy summary**
   - Allowed agents.
   - Max tasks per run.
   - Stop-on-first-failure.
   - Auto-merge / production-deploy / real-PR / bulk-dispatch all visibly disabled.

3. **Operator pause**
   - Pause button calls `POST /api/gateway/agy/overnight-guard/pause`.
   - Resume button calls `POST /api/gateway/agy/overnight-guard/resume`.
   - UI must show current `operator_pause` before any separate dry-run launch action.

4. **Preflight/evaluate action**
   - Evaluate button calls `POST /api/gateway/agy/overnight-guard/evaluate` with `max_tasks` 1 or 2 and `allowed_agents: ["agy"]`.
   - The button must not launch AGY tasks. It only records the guard decision.

5. **Operator summary requirement**
   - After any later separately approved limited overnight dry-run, the dashboard must show an operator summary containing runs attempted, accepted/blocked completed-work IDs, errors, and no-claim confirmations.

## Non-claims

This design and the existing guard implementation do not claim:

- Overnight autopilot is enabled.
- Auto-merge is enabled.
- Production deploy is enabled.
- Bulk AGY dispatch is enabled.
- GitHub PR creation is enabled by the guard.
- This task alone proves overnight readiness.

## Verification map

- Design doc/API contract: this file.
- Machine-readable defaults: `prismatic/agy_overnight_guard.py::OvernightGuardPolicy.as_dict()`.
- CLI contract: `scripts/agy_overnight_guard.py`.
- Gateway/operator controls: `prismatic/gateway/server.py` overnight guard endpoints.
- Tests: `tests/test_agy_overnight_guard.py` and `tests/test_agy_overnight_guard_api.py`.

`OVERNIGHT_READINESS_GUARD_DESIGN_OK`
