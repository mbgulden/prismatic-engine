# GRO-3460 — Watchdog Timers & Fleet Uptime Audit

**Date:** 2026-07-05  
**Agent:** Ned  
**Scope:** Prismatic Engine watchdog intervals, escalation channels, failover/recovery triggers, and live fleet uptime signals.

## Executive summary

🟡 **Warning — watchdog coverage is active, but the live watchdog topology is split across two generations and has stale signals.**

The currently installed host has three active systemd timers:

1. `prismatic-watchdog.timer` — local dispatcher/gateway health probe every **30s**.
2. `prismatic-webhook-drain.timer` — webhook queue drain every **30s**.
3. `prismatic-fleet-watchdog.timer` — broader fleet/engine health sweep every **5m**.

The local watchdog is firing continuously and sees the gateway `/health` endpoint healthy, but it also logs `prismatic-dispatcher.service` inactive and missing heartbeat on every run. Because the health endpoint passes, it resets the failure counter to clean instead of escalating. The fleet watchdog is also active and reports several warnings: stale webhook freshness in the deployed log, stale/absent PE run-record updates, and `alerts.log` not updated for multiple hours.

This is not a hard outage — `prismatic-gateway.service` is active and the 30s timers are running — but the current signals are noisy enough that a real dispatcher/fleet failure could be masked by the gateway-health fallback.

## Evidence collected

### Source paths inspected

Repository branch baseline (`origin/deploy-fresh` worktree):

- `scripts/prismatic-watchdog.timer`
- `scripts/prismatic-watchdog.service`
- `scripts/watchdog.sh`
- `scripts/heartbeat.sh`
- `scripts/rollback.sh`
- `prismatic/distributed_watchdog.py`
- `tests/test_distributed_watchdog.py`
- `prismatic/gateway/alert_manager.py`
- `tests/test_alert_manager.py`

Live deployed / newer-tree evidence:

- `/etc/systemd/system/prismatic-watchdog.timer`
- `/etc/systemd/system/prismatic-webhook-drain.timer`
- `/etc/systemd/system/prismatic-fleet-watchdog.timer`
- `/home/ubuntu/work/prismatic-engine/prismatic/fleet_watchdog.py`
- `/home/ubuntu/work/prismatic-engine/prismatic/fleet_actions.py`
- `/home/ubuntu/.prismatic/logs/watchdog.log`
- `/home/ubuntu/.prismatic/logs/fleet-watchdog.log`
- `/home/ubuntu/.hermes/profiles/*/cron/jobs.json`

### Live timer state

```text
systemctl list-timers --all 'prismatic*'

prismatic-watchdog.timer       last=2026-07-05 21:18:25 UTC, next≈30s cadence
prismatic-webhook-drain.timer  last=2026-07-05 21:18:25 UTC, next≈30s cadence
prismatic-fleet-watchdog.timer last=2026-07-05 21:15:42 UTC, next≈5m cadence
prismatic-curator-digest.timer daily, next=2026-07-06 14:00 UTC
```

Installed timer definitions:

| Timer | Installed cadence | Repository baseline cadence | Notes |
|---|---:|---:|---|
| `prismatic-watchdog.timer` | `OnUnitActiveSec=30`, `AccuracySec=1` | `OnUnitActiveSec=60`, `AccuracySec=5` | Live unit is more aggressive than the committed baseline. |
| `prismatic-webhook-drain.timer` | `OnUnitActiveSec=30`, `AccuracySec=5` | not present on `origin/deploy-fresh` branch | Present in the live/newer tree, not this branch baseline. |
| `prismatic-fleet-watchdog.timer` | `OnUnitActiveSec=300`, `AccuracySec=5` | not present on `origin/deploy-fresh` branch | Present in the live/newer tree, not this branch baseline. |

### Live service state

```text
prismatic-watchdog.timer               active
prismatic-watchdog.service             inactive (oneshot)
prismatic-fleet-watchdog.timer         active
prismatic-fleet-watchdog.service       inactive (oneshot)
prismatic-webhook-drain.timer          active
prismatic-webhook-drain.service        inactive (oneshot)
prismatic-gateway.service              active
```

`prismatic-watchdog.service`, `prismatic-fleet-watchdog.service`, and `prismatic-webhook-drain.service` are oneshot services, so `inactive` after execution is expected. The concerning inactive unit is `prismatic-dispatcher.service`, which `watchdog.sh` checks but does not find active.

### Local watchdog live output

`PRISMATIC_HOME=/home/ubuntu /home/ubuntu/work/prismatic-engine/scripts/watchdog.sh --status`:

```text
Failures: 0/3 (clean)
```

Recent `/home/ubuntu/.prismatic/logs/watchdog.log` repeats this pattern every 30s:

```text
CHECK 1/3: systemd service is NOT active — FAIL
CHECK 2/3: heartbeat check failed: MISSING: heartbeat.pid not found at /home/ubuntu/.prismatic/run/heartbeat.pid — FAIL
CHECK 3/3: health endpoint http://localhost:9000/health → 200 — PASS
✅ Healthy — no failures recorded
```

Interpretation: the health endpoint is currently sufficient to mark the system healthy even when the dispatcher service and heartbeat checks fail. That may be intentional during the gateway-centric deployment, but it weakens dispatcher-specific uptime detection.

### Fleet watchdog live output

Dry JSON run at `2026-07-05T21:18:31Z` with live state dir/log dir:

```text
summary: ok=10 warn=2 fail=0
ok:
- prismatic-gateway.service active
- prismatic-webhook-drain.timer active
- Gateway /health 200
- Webhook queue 0 pending
- no webhooks yet
- 0 rejections in last 300s
- HMAC disabled in dev / no secret configured
- all state DBs within size
- 1 active lock(s), none stale
- all logs within size
warn:
- PE run_records.json not updated in 523h. No recent PE-dispatched run observed.
- alerts.log not updated in 17760s. Watchdog may be silently dead.
```

Recent deployed `/home/ubuntu/.prismatic/logs/fleet-watchdog.log` included a red webhook-freshness alert:

```text
Status: 🔴 red
Alerts: 3 (healthy checks: 9)
🔴 No webhook received in 2h (9145s). Threshold: 3600s. This is the metric that caught GRO-2400 (HMAC drift).
🟡 No PE run_records.json yet; external ASO agent-runs stale for 551h.
🟡 alerts.log not updated in 17592s. Watchdog may be silently dead.
```

The dry run and log tail disagree on the webhook-freshness wording (`no webhooks yet` vs `No webhook received in 2h`). That points to state-dir/runtime-environment drift between the installed unit and the manual run, not necessarily a new outage.

### Distributed watchdog state

```text
/tmp/prismatic/swarm_nodes.json                  missing
/tmp/prismatic/distributed_watchdog_state.json   missing
/tmp/prismatic/vram/                             missing
```

The code supports distributed node registration, stale job detection, VRAM orphan cleanup, decommissioning, and IPC events, but no live distributed watchdog state is present on this host right now.

### Hermes / orchestrator watchdog jobs

Selected live cron watchdogs from profile ledgers:

| Profile | Job | Cadence | Deliver | Latest status |
|---|---|---:|---|---|
| `ned` | `ned-daily-infra-sweep` | daily 23:55 UTC | origin | ok, last 2026-07-04 |
| `orchestrator` | `GPU Health Monitor — k3s node + Ollama/vLLM endpoints` | 5m | local | ok |
| `orchestrator` | `bot-delegation-watchdog` | 1m | local | ok |
| `orchestrator` | `Jules Session Watchdog` | 15m | local | ok |
| `orchestrator` | `Silent Cron Detector — daily health digest` | daily 08:00 | local | ok |
| `orchestrator` | `Tier-1 Silent Failure Watchdog` | every 6h | Telegram | ok |
| `orchestrator` | `AGY Auto-Re-Arm Watchdog` | 5m | local | ok |
| `orchestrator` | `Event-Driven Factory Watchdog` | 5m | local | ok |

Known disabled/stale health job:

```text
State DB health check + alert cron
enabled=false
last_status=error
last_error=/home/ubuntu/work/prismatic-engine/scripts/check-state-db-health.py: No such file or directory
```

## Timer / watchdog interval map

| Surface | Interval | Trigger source | Healthy signal | Escalation / action |
|---|---:|---|---|---|
| Local watchdog | 30s live (`60s` in branch baseline) | systemd timer | gateway `/health` HTTP 200 OR dispatcher service fallback | after 3 consecutive unhealthy runs, calls `scripts/rollback.sh` |
| Heartbeat freshness | `MAX_AGE_SECONDS=120` | `scripts/heartbeat.sh --check` | `heartbeat.pid` exists, PID alive, timestamp <120s | contributes to local watchdog failure counter |
| Rollback threshold | 3 failures | `watchdog.sh` | failure counter file absent/zero | swaps `.prismatic/active` symlink to previous version, restarts `prismatic-dispatcher.service`, writes heartbeat |
| Fleet watchdog | 5m | systemd timer | `prismatic.fleet_watchdog --json` checks | action map in `prismatic.fleet_actions` |
| Webhook drain | 30s | systemd timer | drain timer active, queue pending below threshold | starts drain service when queue backed up |
| Webhook freshness | 1h threshold | fleet watchdog | recent webhook or empty fresh-install state | alert, currently no auto-action |
| Webhook rejection burst | 20 rejected / 300s | fleet watchdog | rejected count below threshold | alert and diagnostic actions available |
| PE agent-run freshness | 24h threshold | fleet watchdog | recent `run_records.json` | warning/actionable, no safe auto-action observed |
| Alert log freshness | 1h threshold | fleet watchdog | `alerts.log` mtime <1h | warning/actionable, no safe auto-action observed |
| State DB size | 100MB | fleet watchdog | DBs under threshold | `VACUUM` large DBs |
| Stale locks | 24h | fleet watchdog | lock heartbeats fresh | evicts stale lock entries |
| Log size | 10MB | fleet watchdog | logs below threshold | rotates/deletes large logs |
| Distributed watchdog | 30s default loop, 120s job timeout | `prismatic.distributed_watchdog` | node heartbeat within `2 * JOB_TIMEOUT_S` | timeout events, node decommission, failover targets, VRAM cleanup |
| Distributed decommission | 4 consecutive failures | `DISTRIBUTED_MAX_FAILURES` | node failure counter below threshold | node marked decommissioned; failover via registry targets |

## Alert escalation channels

### Prismatic gateway alert manager

`prismatic/gateway/alert_manager.py` defines the routing tree:

| Severity | Destination | Secondary |
|---|---|---|
| `critical` | Telegram | log file |
| `warning` | Slack | log file |
| `info` | log file | none |

Rules present in the branch baseline:

- `HighLockContention` — critical, default threshold `>= 5` waiters.
- `AgentStall` — critical, no completions in `15m`.
- `CreditBurnRate` — warning, default `>1000` credits/hr.
- `CircuitBreakerTrip` — critical, any tripped breaker.

Ned note: Slack is intentionally disabled for this profile. Warning alerts that only target Slack should be treated as log-only unless the orchestrator profile owns and configures the Slack webhook.

### Fleet watchdog actions

`prismatic.fleet_actions` maps alert text to idempotent handlers including:

- restart `prismatic-gateway.service`
- restart `prismatic-webhook-drain.timer`
- trigger webhook queue drain
- vacuum large SQLite DBs
- clear stale lock entries
- rotate large logs
- probe gateway and webhook endpoints
- optionally post webhook drift comments when `WATCHDOG_ALERT_ISSUE` and Linear token are configured

Several observed live alerts (`webhook freshness`, `PE run_records stale`, `alerts.log stale`) do not have an obvious automatic repair path in the tail output and therefore degrade to manual-review/no-auto-action.

## Failover / recovery trigger review

### What works

- The local 30s watchdog is actively firing and writing logs.
- `prismatic-gateway.service` is active and `/health` returns 200.
- The webhook drain timer is active on a 30s cadence.
- The fleet watchdog timer is active on a 5m cadence.
- The code-level distributed watchdog has explicit timeout, decommission, failover-target, VRAM orphan, and IPC event hooks.
- Test coverage for distributed watchdog and alert routing passed in this audit.

### Gaps found

1. **Dispatcher-specific failure may be masked by gateway health.**  
   The local watchdog logs dispatcher service inactive and missing heartbeat every run, but still declares healthy because the gateway endpoint is 200. If dispatcher uptime is still a requirement, the health decision should distinguish `gateway healthy` from `dispatcher healthy` instead of using gateway 200 as a blanket pass.

2. **Installed timer drift from committed baseline.**  
   Live `/etc/systemd/system/prismatic-watchdog.timer` runs every 30s, while the branch baseline says 60s. Newer fleet/webhook timers are deployed but absent from the `origin/deploy-fresh` baseline used for this issue branch. That makes code review/history reconstruction harder.

3. **Fleet watchdog warnings are actionable but under-routed.**  
   `PE run_records.json` and `alerts.log` freshness warnings have no automatic recovery in the observed report. `alerts.log not updated` is especially awkward: it says alert logging may be dead, while the fleet watchdog itself logs elsewhere.

4. **Webhook freshness state is environment-sensitive.**  
   The deployed log reported a red no-webhook-in-2h alert, while the manual dry JSON run against the selected state dir reported `no webhooks yet`. The unit environment/state-dir should be pinned and documented so operators know which DB is authoritative.

5. **Distributed watchdog is code-ready but not visibly active.**  
   No `/tmp/prismatic/swarm_nodes.json`, distributed state file, or VRAM marker dir exists. That means no live multi-node roster/failover state was observed on this host at audit time.

## Risk rating

🟡 **Medium operational risk.**

The gateway is up, timers are active, and test coverage for the watchdog/alert modules is green. The main risk is signal ambiguity: dispatcher heartbeat failures are being tolerated because gateway health passes, and fleet warnings currently point to stale observability surfaces rather than a precise recovery action.

## Recommended follow-ups

1. **Split local watchdog health into separate gateway and dispatcher verdicts.**  
   Keep gateway `/health` as a service-health signal, but do not let it fully mask `prismatic-dispatcher.service` inactive + missing heartbeat when dispatcher uptime matters.

2. **Reconcile installed systemd units with repository-managed units.**  
   Either commit the deployed 30s `prismatic-watchdog.timer` cadence plus fleet/webhook timer units to the canonical branch, or document why `/etc/systemd/system` intentionally differs from the repo baseline.

3. **Add auto-action or explicit operator runbook for stale `alerts.log` and `run_records.json`.**  
   If these are known false positives in the current gateway-led architecture, downgrade or reword them. If not, wire them to a precise restart/resume/probe action.

4. **Pin and expose the fleet watchdog state dir.**  
   The deployed unit and manual run should read the same queue/run-record DBs. Add `--json` output path and state-dir echoing to make drift obvious in logs.

5. **Decide whether distributed watchdog should be live.**  
   If multi-node failover is production-critical, install a timer/service for `prismatic.distributed_watchdog` and create/populate the node roster. If it is dormant scaffolding, label it explicitly in docs so missing state is not mistaken for failure.

## Verification

Targeted code verification run:

```text
cd /tmp/prismatic-gro3460
PYTHONPATH=. pytest tests/test_distributed_watchdog.py tests/test_alert_manager.py -q

63 passed, 1 warning in 0.83s
```

Live probes used for the audit:

- `systemctl list-timers --all 'prismatic*'`
- `systemctl cat prismatic-watchdog.timer prismatic-fleet-watchdog.timer prismatic-webhook-drain.timer`
- service `is-active` sweep for watchdog/fleet/drain/gateway units
- `scripts/watchdog.sh --status`
- tail of `/home/ubuntu/.prismatic/logs/watchdog.log`
- tail of `/home/ubuntu/.prismatic/logs/fleet-watchdog.log`
- `prismatic.fleet_watchdog --json` dry probe
- Hermes cron ledger scan across `ned`, `orchestrator`, `fred`, and `kai`

## Definition of Done mapping

- ✅ Watchdog intervals audited.
- ✅ Alert escalation channels audited.
- ✅ Failover/recovery triggers audited.
- ✅ Live timer/service state checked.
- ✅ Results written in markdown.
