# GRO-3459 — Circuit Breakers & Auto-Resume Gates Audit

**Date:** 2026-07-05  
**Agent:** Ned  
**Scope:** Prismatic Engine circuit breaker parameters, error-rate monitors, automatic gate escalation triggers, and current live state.

## Executive summary

🟡 **Warning — the implemented breaker logic exists, but one live gate is stale-paused.**

The AGY supervisor has two overlapping protection layers:

1. **Auto-resume/preflight gates** in `scripts/agy_sandbox_event_supervisor.py`: storage free-space, Linear API, and AGY backend probes.
2. **Runtime circuit breaker** in `EventDrivenSupervisor.record_result_for_circuit()`: trips after consecutive AGY failures and calls cron pause + Linear alert.

The code-level parameters are clear and test-covered, but the live orchestrator cron ledger currently shows the AGY Sandbox Supervisor cron (`faf8d91da716`) disabled with stale paused reason `Storage gate failed: /tmp or /archive free space below threshold`, while a separate long-running supervisor process is still alive. Current disk state no longer supports the storage failure (`/tmp` 191G free, `/archive` 1.1T free). The auto-rearm watchdog only re-arms **Circuit breaker tripped** pauses, so it ignores this storage-pause state.

## Evidence collected

### Source paths inspected

- `scripts/agy_sandbox_event_supervisor.py`
- `prismatic/telemetry.py`
- `prismatic/gateway/alert_manager.py`
- `prismatic/admin.py`
- `prismatic/cli/state.py`
- `prismatic/supervisor/recovery.py`
- `prismatic/supervisor/tests/test_event_driven_supervisor.py`
- `/home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_auto_rearm.py`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`
- `/home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db`

### Live probes

```text
AGY supervisor process:
PID 3287397  age 1-02:12:26  python3 -u .../agy_sandbox_event_supervisor.py --long-run --cron-mode --from-linear --max-concurrent 3 --lane-mode auto --active-project all --backlog-age-days 30 --watchdog --watchdog-interval 30 --jitter 5-15 --backoff 3-8

Disk gates:
/tmp     191G free / 35% used
/archive 1.1T free / 6% used

AGY Sandbox Supervisor cron:
id=faf8d91da716
enabled=false
state=paused
last_status=ok
paused_reason="Storage gate failed: /tmp or /archive free space below threshold."
last_run_at=2026-07-03T20:17:05.706125-06:00

AGY Auto-Re-Arm Watchdog:
enabled=true
last_status=ok
last_run_at=2026-07-05T14:59:11.053704-06:00
status output: cron_enabled=false, cron_state=paused, paused_reason=storage failure, tier=medium
```

### Telemetry database snapshot

```text
event_router.db tables include telemetry_circuit_breakers, telemetry_loop_events, lane_stops.
telemetry_circuit_breakers rows: []
telemetry_loop_events: launch=5, last=2026-06-15T19:45:12.863686+00:00
telemetry_agent_runs: 635 rows, last start_time=2026-06-25T10:21:25.204969+00:00
telemetry_credit_ledger: 22018 rows, last recorded_at=2026-07-05T16:22:30.058585+00:00
lane_stops: unknown until 1783151121.8000689; agy until 1783280372.8668945
```

`prismatic-admin telemetry alerts --hours 24` currently triggers one unrelated high credit-burn alert:

```text
[HIGH] credit_burn — Credit burn rate 2428/hr exceeds threshold 1000/hr (total: 58275 credits in 24h)
```

No active `telemetry_circuit_breakers` rows were present at audit time.

## Circuit breaker parameters

### Runtime supervisor breaker

Location: `scripts/agy_sandbox_event_supervisor.py`

| Parameter | Default | Source | Notes |
|---|---:|---|---|
| `AGY_CIRCUIT_BREAKER_FAILURE_LIMIT` | `2` | env override | Trips after two consecutive qualifying AGY failures. |
| Qualifying failures | `has_inactivity_kill`, `has_backend_timeout`, `has_partial_result`, `has_start_timeout` | `_is_circuit_failure()` | Any of these increments `consecutive_failures`. |
| Reset behavior | success resets to `0` | `record_result_for_circuit()` | Non-failing result clears failure streak. |
| Trip action | pause cron + post alert + set shutdown event | `record_result_for_circuit()` | Calls `cron_client.pause_cron(reason)`, `post_gate_alert()`, and `shutdown_event.set()`. |
| Long-run bypass | breaker does **not** trip when `long_run=True` | `and not getattr(self, "long_run", False)` | Live process runs with `--long-run`, so transient failures increment but do not pause via this breaker. |

Test coverage exists in `prismatic/supervisor/tests/test_event_driven_supervisor.py::test_circuit_breaker`: two consecutive `has_inactivity_kill` results trip the circuit and call cron pause + alert.

### AGY inactivity and result guards

Location: `scripts/agy_sandbox_event_supervisor.py`

| Parameter | Default | Cron-mode behavior | Notes |
|---|---:|---:|---|
| `AGY_INACTIVITY_KILL_SEC` | `120` | raised to `900` if below `600` | Sandbox file-inactivity ceiling, not wall-clock timeout. |
| `AGY_DONE_WAIT_SEC` | `600` | unchanged | If `RESULT.md` exists but `DONE` is not emitted, AGY is killed after this wait. |
| `PRINT_TIMEOUT` | `24h0m0s` | unchanged | AGY CLI print timeout. |

The inactivity guard is a useful bounded-work kill switch. It is not an error-rate monitor by itself; it feeds the runtime circuit breaker via `has_inactivity_kill`.

### Auto-resume / preflight gates

Location: `scripts/agy_sandbox_event_supervisor.py`

| Gate | Default threshold | Failure action in cron mode | Alert target |
|---|---:|---|---|
| Storage gate | `/tmp >= 10GB`, `/archive >= 50GB` | `pause_supervisor_cron(reason)` + Linear alert | `GRO-2492`, `GRO-2551` by default |
| Linear API gate | viewer GraphQL succeeds within 15s | pause cron + Linear alert | same |
| AGY backend gate | `agy --print ... --print-timeout 30s` succeeds within 45s | pause cron + Linear alert | same |

These are pre-dispatch gates, not runtime breaker counters. They pause the Hermes cron record directly when `cron_mode=True`.

## Error-rate and alert monitors

### Gateway alert evaluator

Location: `prismatic/gateway/alert_manager.py`

Rules and defaults:

| Rule | Default trigger | Severity | Route |
|---|---:|---|---|
| `HighLockContention` | `>= 5` waiters | critical | Telegram + log |
| `AgentStall` | `0` completions in 15 min | critical | Telegram + log |
| `CreditBurnRate` | `> 1000` credits/hr | warning | Slack + log |
| `CircuitBreakerTrip` | `breakers_tripped > 0` | critical | Telegram + log |
| `EmailBounce` | `bounces > 0` | critical | Telegram + log |

### Telemetry circuit-breaker table

Location: `prismatic/telemetry.py`

`TelemetryCollector.check_circuit(issue_id, agent, micro_count, macro_count=0)` persists cumulative counts to `telemetry_circuit_breakers` and trips when either threshold is exceeded. The function emits a `telemetry_loop_events` row with `loop_type='circuit_breaker'` on trip.

Concern: the audit found the telemetry table present but empty, and live agent-run telemetry in this DB appears stale (`telemetry_agent_runs` last row on 2026-06-25). The current long-running AGY supervisor is alive, but the dashboard-facing breaker telemetry is not receiving recent breaker rows.

## Automatic gate escalation and auto-resume behavior

### What works

- Preflight gate code pauses the supervisor cron and posts Linear comments on storage/API/backend failures.
- Runtime circuit-breaker code pauses the cron and posts Linear alerts when **not** in long-run mode.
- `agy_auto_rearm.py` can detect paused cron records whose `paused_reason` contains `Circuit breaker tripped` and re-arm or fail over by tier policy.
- Tests cover the runtime breaker trip path.

### Gaps found

1. **Stale storage pause not auto-rearmed.**  
   `agy_auto_rearm.py` only recognizes `paused_reason` containing `Circuit breaker tripped`. The live cron is paused for storage, but current disk state is healthy. The watchdog exits cleanly without re-arming.

2. **Long-run mode disables the runtime circuit trip.**  
   The live supervisor process uses `--long-run`; therefore `record_result_for_circuit()` will not set `circuit_tripped`, pause cron, or shutdown on consecutive AGY failures. That may be intentional for resilience, but it means the advertised breaker semantics differ between normal and production long-run mode.

3. **Two breaker systems are not unified.**  
   `TelemetryCollector.check_circuit()` tracks micro/macro breaker counts in `telemetry_circuit_breakers`, while the live AGY supervisor tracks `consecutive_failures` in memory. The gateway alert rule reads dashboard data for `breakers_tripped`, but the runtime supervisor trip path does not appear to write `telemetry_circuit_breakers`.

4. **Alert routing references Slack for warnings.**  
   `AlertRouter` sends warnings to Slack, but Ned's platform doctrine says Slack is intentionally disabled for this profile and owned by the orchestrator. Warning alerts still log, but any Slack delivery expectation should be treated as orchestrator-owned, not Ned-owned.

## Risk rating

🟡 **Medium operational risk.**

The live supervisor process is currently running, so this is not an outage. But the durable scheduler record is stale-paused, and the auto-rearm path does not clear the stale storage pause despite healthy disks. If the live process exits, the disabled cron record may prevent the supervisor from returning automatically.

## Recommended follow-ups

1. **Add a storage-pause revalidation branch to `agy_auto_rearm.py`.**  
   If `paused_reason` starts with `Storage gate failed`, re-run the same `/tmp` + `/archive` thresholds and re-enable the supervisor cron when both are healthy. Post a Linear comment to the gate issues with the before/after evidence.

2. **Document or revise long-run breaker semantics.**  
   Either make the production long-run bypass explicit in operator docs, or add a second threshold for long-run mode that alerts without hard-stopping the process.

3. **Wire runtime breaker trips into telemetry.**  
   When `record_result_for_circuit()` trips, also call the telemetry circuit path or publish a canonical event so `telemetry_circuit_breakers` and gateway `CircuitBreakerTrip` alerting see the same state.

4. **Audit and clear stale `lane_stops`.**  
   The `agy` lane stop timestamp was present in `event_router.db`; confirm it is expired/ignored by the live scheduler or clean it with the canonical lane-resume command.

5. **Keep warning routes harness-aware.**  
   Do not assume Slack delivery from Ned. Warnings should have a log/Telegram/orchestrator-owned route when they are operationally important.

## Definition of Done mapping

- ✅ Circuit breaker parameters audited.
- ✅ Error-rate / alert monitors audited.
- ✅ Automatic gate escalation and auto-resume triggers audited.
- ✅ Live state checked against code-level expectations.
- ✅ Results written in markdown.

