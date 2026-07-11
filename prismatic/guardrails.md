# Phase 6 Guardrails, Replay, and Rollout Safety

`prismatic.guardrails` adds a harness-agnostic safety layer for the Prismatic Engine factory.

## What it covers

- **Replay/backfill mode:** `ReplayQueue` mirrors live ingest, dispatch, artifact, and state-sync events into append-only JSONL. After outage or corruption, callers replay records with per-event handlers and get a `ReplayResult` showing successes and failures.
- **Live-path smoke tests:** `SmokeSuite` runs caller-provided probes for ingest, dispatch, artifact, and state sync. A thrown exception or failed probe is a fast `fail` verdict.
- **Silent-stall alerting:** `detect_silent_stalls()` compares heartbeat timestamps against a concrete alert path such as ops feed, Linear comment, webhook, or pager.
- **Rollout / rollback gate:** `rollout_gate()` blocks rollout unless smoke, replay, and stall checks are green, manual approval is present, and a rollback target is recorded.

## Stop/go contract

Rollout is allowed only when all five inputs are true:

1. Smoke suite status is `pass`.
2. Replay/backfill status is `pass`.
3. Silent-stall detector status is `pass`.
4. Manual approval is explicit.
5. Rollback target is non-empty.

Any failed input produces `RolloutDecision(go=False, status=fail, reasons=[...])`.

## Example

```python
from prismatic.guardrails import ReplayQueue, SmokeCheck, SmokeSuite, detect_silent_stalls, rollout_gate

queue = ReplayQueue("/var/lib/prismatic/replay.jsonl")
queue.append("evt-1", "ingest", {"issue": "GRO-123"})
replay = queue.replay({"ingest": lambda record: live_ingest(record.payload)})

smoke = SmokeSuite([
    SmokeCheck("ingest", probe_ingest),
    SmokeCheck("dispatch", probe_dispatch),
    SmokeCheck("artifact", probe_artifact),
    SmokeCheck("state_sync", probe_state_sync),
]).run()

stalls = detect_silent_stalls(heartbeats, max_age_seconds=300, alert_path="ops-feed")
decision = rollout_gate(
    smoke=smoke,
    replay=replay,
    stalls=stalls,
    manual_approval=operator_clicked_go,
    rollback_target=current_previous_release,
)
```

The implementation deliberately avoids profile-specific paths or Hermes-only assumptions; deployment adapters should supply paths, probes, alert sinks, and approval state.
