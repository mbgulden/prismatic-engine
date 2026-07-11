# GRO-3488 — Review lane routing guard

## Change summary

Review-only Linear work is now explicitly separated from execution dispatch paths.

## Deterministic routing rules

- Labels such as `agent:peer-review`, `agent:ned-review`, `agent:needs-human-review`, and `agent:post-publish-review*` are review-only.
- Issues already in Linear state `In Review` are review-only.
- Titles/descriptions containing explicit review lane phrases such as `peer review`, `self-review`, `PR review`, or `needs human review` are review-only.

## Expected behavior

- Review-only issues are skipped by `prismatic.curator.issue_to_task.assign_lane()` with `review-only-lane` and do not become executable tasks.
- The AGY sandbox supervisor no longer includes review labels in its default execution-label fetch set and filters review-only nodes after fetch as a defensive second gate.
- `scripts/linear_relabel.py` no longer marks review-only work as `dispatch:ready`, even when the title contains runnable verbs.

## Verification

Focused tests:

```bash
pytest prismatic/curator/tests/test_issue_to_task_review_lanes.py scripts/tests/test_review_lane_routing.py -q
```
