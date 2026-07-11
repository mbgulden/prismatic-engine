# Board hygiene worker and stale/noise suppression

GRO-3552 adds `prismatic.board_hygiene`, a harness-agnostic worker for
recurring board scans.  The worker classifies Linear-like issue snapshots,
suppresses noisy classes, persists the last actionable fingerprint, and emits
only when actionable work has actually changed.

## Suppression classes

The hygiene pass suppresses these issue classes before computing board deltas:

- **Completed**: state type/name is `completed`, `done`, `canceled`, or
  `cancelled`.
- **Duplicate**: label `duplicate` / `noise:duplicate` or a `Duplicate:` / `Dupe:`
  title prefix.
- **Umbrella**: label `epic`, `umbrella`, `noise:umbrella`, or an `Epic:` /
  `Umbrella:` title prefix.
- **Stale**: explicit stale labels or `updatedAt` older than the configured
  `stale_after_days` threshold.
- **Unassigned**: no `agent:*`, `dispatch:ready`, or `dispatch:priority` marker.

Suppressed issues remain in the returned evidence map so operators can audit
why something disappeared without waking downstream dispatchers.

## Actionable delta contract

`BoardHygieneWorker.run()` returns `HygieneResult` with:

- `delta.new_actionable`: actionable IDs not present in the previous state.
- `delta.changed_actionable`: actionable IDs whose title/state/labels/url
  fingerprint changed.
- `delta.resolved_actionable`: IDs that were actionable last run but no longer
  are actionable this run.
- `should_emit`: `True` only if one of those actionable delta buckets is non-empty.

Noise-only churn updates the persisted snapshot but leaves `should_emit=False`.
That is the important bit. No more waking the fleet because a duplicate, completed
issue, or umbrella epic wobbled on the board. We've had enough of that.

## Usage

```python
from prismatic.board_hygiene import BoardHygieneWorker

worker = BoardHygieneWorker("/var/lib/prismatic/board-hygiene.json")
result = worker.run(linear_issues)
if result.should_emit:
    dispatch(result.delta.as_dict())
else:
    log_debug(result.as_dict()["suppressed"])
```

The module only depends on the Python standard library and accepts plain dicts,
so any Linear poller, webhook reducer, or local board scanner can use it without
pulling in Hermes-specific runtime code.
