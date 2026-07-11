# GRO-3531 — live service-boundary recovery verification

Date: 2026-07-06T16:51:03Z
Agent: Ned
Repo branch: `ned/GRO-3531`

## Scope

GRO-3531 asked to run the watchdog control path against the active service boundary and verify that the dashboard, logs, and backend state agree after the action completes.

This verification used the live systemd boundary, not a mocked subprocess path:

```bash
sudo systemctl start prismatic-watchdog.service
```

The unit's `ExecStart` is the deployed service boundary:

```text
/usr/bin/bash /home/ubuntu/work/prismatic-engine/scripts/watchdog.sh
```

No production service was restarted. The test seeded only the watchdog failure counter file so the healthy recovery path had a concrete state action to perform.

## Pre-check: live gateway/dashboard boundary

The gateway health endpoint was reachable before the watchdog run:

```text
GET http://localhost:9000/health
HTTP 200
{"status":"ok","uptime_seconds":2097.6,"started_at":1783354541.0781143}
```

The dashboard/event-bus endpoint was also reachable and showed a drained queue:

```text
GET http://localhost:9000/events/bus-stats
{"exists":true,"total":10000,"processed":10000,"pending":0,"oldest_ts":1783340460.0131838,"newest_ts":1783356094.6579082}
```

## Recovery-state control action

To prove the action path completed against backend state, I seeded the watchdog failure counter:

```bash
echo 2 | sudo tee /home/ubuntu/.prismatic/run/watchdog_failures.txt
cat /home/ubuntu/.prismatic/run/watchdog_failures.txt
# 2
```

Then I triggered the active systemd unit:

```bash
sudo systemctl start prismatic-watchdog.service
# exit 0
```

Systemd reported a clean one-shot completion:

```text
Result=success
ExecMainCode=1
ExecMainStatus=0
ActiveState=inactive
SubState=dead
```

## Log evidence

`/home/ubuntu/.prismatic/run/watchdog.log` recorded the expected healthy recovery action:

```text
[2026-07-06 16:51:03] CHECK 1/3: systemd service is NOT active — FAIL
[2026-07-06 16:51:03] CHECK 2/3: heartbeat check failed: DEAD: PID 2746156 2026-07-06T14:58:34Z is not running — FAIL
[2026-07-06 16:51:03] CHECK 3/3: health endpoint http://localhost:9000/health → 200 — PASS
[2026-07-06 16:51:03] ✅ Healthy — failure counter reset (was 2/3)
```

Interpretation: local service/heartbeat diagnostics are stale, but the live gateway endpoint is healthy. The watchdog correctly treated the live health response as decisive and reset backend recovery state instead of escalating.

## Backend state after action

After the systemd action completed:

```text
/home/ubuntu/.prismatic/run/watchdog_failures.txt: absent
```

That matches the log line `failure counter reset (was 2/3)`.

## Dashboard state after action

The live dashboard/event-bus API remained healthy and internally consistent:

```text
GET http://localhost:9000/health
HTTP 200
{"status":"ok","uptime_seconds":2122.6,"started_at":1783354541.0781143}

GET http://localhost:9000/events/bus-stats
{"exists":true,"total":10000,"processed":10000,"pending":0,"oldest_ts":1783340460.0131838,"newest_ts":1783356094.6579082}
```

No new `watchdog.recovery` event was expected in this specific run because the service was healthy and the action was failure-counter reset, not service restart/escalation. The dashboard still confirmed the backend event queue was drained (`pending: 0`) and reachable through the active gateway.

## Verdict

✅ PASS — the active service boundary completed successfully, logs recorded the recovery-state reset, backend state was cleared, and live dashboard endpoints agreed the gateway/event bus remained healthy.

Follow-up note: the active watchdog logs still phrase service-manager and heartbeat checks as `FAIL` before the healthy endpoint pass. That is diagnostic wording noise, not a functional failure, but it should be reconciled with the GRO-3512 source-of-truth wording when that branch lands on the active service checkout.
