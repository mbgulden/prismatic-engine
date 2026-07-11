# GRO-2981 investigation — telemetry_agent_runs silence

Verified at: 2026-07-09T22:20Z

## Issue

[GRO-2981](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2981) reported that `telemetry_agent_runs` had no fresh rows after 2026-06-25T10:21Z.

## Acceptance checks

### 1. `telemetry_agent_runs` silence by agent

Checked `/home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db` with Python `sqlite3` because the `sqlite3` CLI is not installed on this host.

Result:

| Agent | Latest `telemetry_agent_runs.start_time` |
|---|---|
| `agy` | 2026-06-25T10:21:25.204969+00:00 |
| `fred` | 2026-06-24T20:59:01.540695+00:00 |
| `kai` | 2026-06-23T16:56:47.901410+00:00 |

Total `telemetry_agent_runs` rows: `635`.

Conclusion: the silence is real and per-agent, not just one lane.

### 2. Dispatcher status

`prismatic-dispatcher` is not installed as a system or user systemd unit on this host:

```text
systemctl --user status prismatic-dispatcher -> Failed to connect to bus: No medium found
systemctl status prismatic-dispatcher -> Unit prismatic-dispatcher.service could not be found
journalctl -u prismatic-dispatcher -> No entries
```

The active dispatch topology has shifted to Hermes/orchestrator cron jobs and event-driven watchdogs instead of the old `prismatic.dispatcher` service:

| Job | Enabled | Last status | Last run | Notes |
|---|---:|---|---|---|
| `Agent Dispatcher — Daily Safety-Net Sweep (Tier 7)` | yes | ok | 2026-07-09T08:04:19-06:00 | Demoted from 5min to daily; webhook is primary path |
| `Ned Delta Dispatcher — replaces 6 Ned LLM crons` | yes | ok | 2026-07-09T16:08:26-06:00 | active delta dispatcher |
| `Kai Delta Dispatcher — replaces 4 Kai LLM crons` | yes | ok | 2026-07-09T16:02:25-06:00 | active delta dispatcher |
| `Peer Review Orchestrator` | yes | ok | 2026-07-09T16:06:05-06:00 | active review loop |
| `Jules Dispatcher` | yes | ok | 2026-07-09T16:00:54-06:00 | active dispatcher |
| `Event-Driven Factory Watchdog` | yes | ok | 2026-07-09T16:13:31-06:00 | keeps gateway/supervisor/consumer alive |

### 3. Dispatcher code changed after 2026-06-25

Relevant files were modified after the silence began:

```text
2026-07-02T20:15:14Z /home/ubuntu/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py
2026-07-08T01:27:59Z /home/ubuntu/.hermes/profiles/orchestrator/scripts/event_handlers/dispatch_consumer_v3.py
2026-07-02T23:39:13Z /home/ubuntu/.hermes/profiles/orchestrator/scripts/event_driven_watchdog.sh
```

This supports the current architecture-shift finding: dispatch continued through Hermes/orchestrator scripts, but the legacy `telemetry_agent_runs` writer no longer appears to be the canonical active signal.

### 4. PVE6 / GPU node availability

GPU node `100.78.237.7` is still unreachable from this VM:

```text
ping -c 2 -W 2 100.78.237.7 -> 100% packet loss
curl --max-time 5 http://100.78.237.7:31434/api/tags -> timeout
```

This is still an infrastructure issue, but it is not the sole cause of the `telemetry_agent_runs` silence because local Hermes/orchestrator dispatch crons continue to run successfully.

### 5. Restore fresh `telemetry_agent_runs` row or document pause

No fresh row was written to `telemetry_agent_runs` during this investigation. I did **not** force-run `python -m prismatic.dispatcher serve --once` because it would have side effects against live Linear tasks and may launch agents. That would be a bad diagnostic probe.

Documented cause instead: the legacy Prismatic dispatcher/service path is intentionally not the primary dispatcher now. The daily safety-net dispatcher is demoted to once/day, and the live system is driven by Hermes/orchestrator cron scripts plus event-driven watchdogs.

## Additional telemetry signals

`telemetry_credit_ledger` is also stale now, latest row:

```text
2026-07-07T16:25:46.965573+00:00
```

`event_bus.db` is active for heartbeat events, latest:

```text
2026-07-09T18:00:45.064022+00:00 profile-audit-watchdog heartbeat
```

But agent lifecycle events in `event_bus.db` are stale; latest agent-related events were 2026-07-07 test/supervisor events.

## Disposition

The original acceptance signal (`telemetry_agent_runs`) is obsolete/stale under the current dispatch architecture. The issue should not keep redispatching as an autonomous Ned code task until one of these is chosen:

1. update the event-driven/orchestrator dispatcher path to write modern dispatch rows back into `telemetry_agent_runs`, or
2. replace `telemetry_agent_runs` as the health gate with the active event-driven telemetry source, or
3. explicitly retire `telemetry_agent_runs` and update the dashboard/alerts that still depend on it.

Recommended follow-up: create a focused implementation task for the orchestrator/event-driven lane to restore a unified dispatch telemetry writer. Ned can verify it once the writer exists.
