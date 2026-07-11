# Prismatic Engine Dispatch Contracts (GRO-3474)

This document is the Phase 3 lane-routing contract for the live dispatcher. The
testable source of truth is `prismatic/lane_contracts.py`; this file exists so a
human can audit the routing behavior without reading Python.

## Contract rule

`agent:*` is a necessary label, not a complete dispatch rule. An issue qualifies
for a lane only when all of the following hold:

1. The lane's required `agent:<name>` label is present.
2. The issue state is allowed for that lane.
3. Execution lanes do not receive review-only labels such as `review:only`,
   `type:review`, or `validation:only`.
4. If no issue qualifies, the dispatcher emits the lane's starvation signal
   instead of silently treating the queue as healthy.

## Lane table

| Lane | Queue kind | Required label | Allowed states | Fallback behavior | Starvation signal |
| --- | --- | --- | --- | --- | --- |
| Fred | coordination | `agent:fred` | Backlog, Todo, In Progress | hold; Fred is the ambiguity owner | `fred_queue_empty` |
| Kai | execution | `agent:kai` | Backlog, Todo, In Progress | route ambiguous/non-contract work back to Fred | `kai_queue_empty` |
| Ned | execution | `agent:ned` | Backlog, Todo, In Progress | route ambiguous/non-contract work back to Fred | `ned_queue_empty` |
| AGY | execution | `agent:agy` | Backlog, Todo, In Progress | route ambiguous/non-contract work back to Fred | `agy_queue_empty` |
| Jules | review | `agent:jules` | Backlog, Todo, In Review, Review, QA | review queue only; no execution fallback | `jules_review_queue_empty` |

## Execution vs review boundary

Execution lanes are `kai`, `ned`, and `agy`. If an issue has a review-only label,
those lanes hold it with `review_only_not_execution:<label>`. Jules is the review
lane and can accept review-only work when its state is review-compatible.

## Dispatcher behavior

`prismatic.dispatcher.dispatch_once()` now asks `filter_dispatchable_issues()` to
split candidates into:

- dispatchable issues, which proceed to the existing launcher path;
- held issues, logged with the stable reason string; and
- an empty dispatchable queue, logged as the lane's starvation signal.

The dispatcher also queries actual Linear labels in the single-colon form
(`agent:ned`, `agent:agy`, etc.). The older double-colon form remains tolerated
only when checking legacy local fixtures.

## Verification

Run the focused contract tests from the repository root:

```bash
python3 -m pytest prismatic/test_lane_contracts.py -q
```

The tests prove the Phase 3 acceptance points: all five named lane contracts
exist, label-only dispatch is rejected when the state is wrong, review-only work
cannot leak into execution queues, Jules remains a review lane, and starvation
signals are stable.
