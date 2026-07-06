# GRO-3516 — merge pending terminal Linear prune

`/api/gateway/merge/status` now resolves Linear state for pending merge tickets and removes terminal Linear issues from the pending response/state unless the entry carries an explicit force-keep flag and rationale.

Terminal states covered:

- `Done`
- `Canceled` / `Cancelled`
- `Duplicate`
- Linear state types `completed` and `canceled`

Force-keep contract for a terminal issue in merge pending state:

- Set one of `force_keep_terminal`, `terminal_keep`, or `force_keep` on the pending metadata.
- Include `terminal_keep_rationale`, `force_keep_rationale`, or `rationale` explaining why the terminal Linear issue still needs merge attention.

Status response evidence fields:

- `terminal_pruned_count`
- `retained_terminal_count`
- `pruned_terminal`
- `retained_terminal`
- per-pending-entry `linear_state` and `terminal_keep_rationale`

Regression coverage: `prismatic/gateway/test_merge_status.py` verifies terminal-state detection and the force-kept exception path.
