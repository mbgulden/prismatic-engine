# Prismatic operational health view

GRO-3472 Phase 1 adds a small, harness-agnostic health view under
`prismatic.observability.health_view`. It is the shared contract for answering
"what is broken?" quickly across gateway, event consumer, curator, merge,
supervisor, and lane routing subsystems.

## Contract

The view has four operator-facing sections:

1. `overall_status` — `healthy`, `warning`, or `down` based on the worst
   subsystem/lane status.
2. `what_is_broken` — only actionable rows, each with a failure class, evidence,
   and next action. This is the one-minute operator readout.
3. `subsystems` — every critical service row, including healthy and idle rows.
4. `lanes` — per-lane `active`, `queued`, and `blocked` counts.

## Failure taxonomy

Every actionable failure should use one of these classes:

- `ingest` — source polling/webhook/feed failures.
- `routing` — dispatch contract or agent-label routing failures.
- `execution` — worker launch/runtime failures.
- `artifact` — missing, invalid, or unpublished output artifacts.
- `state_sync` — Linear/repo/local-state divergence.
- `label_debt` — stale or contradictory lane labels.
- `silent_stall` — work is present but the heartbeat is stale.

The `silent_stall` class is intentionally separate from idle. A stale optional
producer with no queued or active work is `idle`; stale heartbeat plus queued or
active work is `warning/silent_stall`.

## Minimal usage

```python
from datetime import datetime, timezone
from prismatic.observability.health_view import (
    FailureClass,
    LaneHealth,
    build_health_view,
    evaluate_subsystem,
    render_markdown,
)

now = datetime.now(timezone.utc)
view = build_health_view(
    [
        evaluate_subsystem(
            "gateway",
            last_seen_at="2026-07-06T20:00:00Z",
            now=now,
            failure_class=FailureClass.INGEST,
            action="Restart gateway and check ingress logs.",
        ),
        evaluate_subsystem(
            "supervisor",
            last_seen_at="2026-07-06T19:30:00Z",
            now=now,
            expected_interval_seconds=300,
            queued=3,
            required=False,
        ),
    ],
    [LaneHealth("ned", active=1, queued=15, blocked=0)],
)
print(render_markdown(view))
```

## Phase-1 acceptance mapping

- "Service health in one place" → `subsystems` rows.
- "Consistent failure taxonomy" → `FailureClass` enum and serialized
  `failure_taxonomy` list.
- "Per-lane active/queued/blocked visibility" → `LaneHealth` rows.
- "Silent stalls distinguishable from normal idle" → `evaluate_subsystem()`
  classifies stale optional/no-work rows as `idle` and stale with work as
  `silent_stall`.
- "One health view is linked from the epic" → this document is the canonical
  health-view contract for Phase 1; runtime producers can now fill the same
  schema without inventing subsystem-specific status formats.
